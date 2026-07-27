"""Spawn a profiled claude agent in a new cmux tab (self-contained).

Source: https://gist.github.com/HeinrichHartmann/bb4d3a8b25b8515b6aaf94b014033b1a

1. Resolves the profile from .agents/profiles/<name>.md (there is no
   --profile flag on claude itself; the profile is passed as the system
   prompt).
2. Forks the workspace: creates a git worktree ../<repo>-<profile>-<id>
   on branch <profile>/<id> from main, allows its .envrc, and grants
   scoped permissions via .claude/settings.local.json.
3. Opens a new terminal tab (surface) in the current cmux workspace and
   starts claude inside the worktree.
4. Waits for the claude banner, then sends a kickoff message so the
   interactive session starts working instead of idling at the prompt.

Usage:
    cspawn --model sonnet --profile worker --beads "gax-sy6 gax-qo8"
    cspawn --profile worker --beads "gdoc"   # by label
    cspawn --no-fork --profile researcher    # run in current repo, no worktree

Agent lifecycle
---------------
Workers and masters have distinct responsibilities:

WORKER (the spawned agent):
  - Do the work scoped to your beads.
  - When done: bd note <id> "output: <what you produced>"
  - Signal completion: bd label <id> review
  - Then stop and wait. Do not exit, close your surface, or clean up.
    Your master owns your lifecycle.

MASTER (the agent that called cspawn):
  - You own the full lifecycle of every surface you spawn.
  - Check for completed work: bd children <master-id> --label review
  - Review the output. Approve or send feedback via a new message on
    the worker's surface.
  - On approval:
      bd close <worker-bead-id>
      cmux close-surface --surface <surface-id>
      git worktree remove <worktree-path>   # if forked
  - Never leave surfaces or worktrees running after work is accepted.
"""

import argparse
import datetime
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

# Permissions granted to forked agent worktrees via .claude/settings.local.json
# (per-checkout, never committed — dies with the worktree).
WORKTREE_PERMISSIONS = {
    "permissions": {
        "allow": [
            "Edit",
            "Write",
            "Bash(direnv exec:*)",
            "Bash(git status:*)",
            "Bash(git diff:*)",
            "Bash(git log:*)",
            "Bash(git add:*)",
            "Bash(git commit:*)",
            "Bash(git rebase main)",
            "Bash(bd:*)",
        ]
    }
}


def sh(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        cmd, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def cmux(*args: str) -> str:
    return sh("cmux", *args)


def _get_surface_pid(surface: str, workspace: str) -> str | None:
    """Return the PID of the process running on *surface*, or None on failure."""
    try:
        out = cmux("top", "--processes", "--workspace", workspace)
        # Each line looks like: "surface:39  pid:12345  ..."
        for line in out.splitlines():
            if surface in line:
                for tok in line.split():
                    if tok.startswith("pid:"):
                        return tok[4:]
    except subprocess.CalledProcessError:
        pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="", help="claude model override; omit to use global settings.json default")
    ap.add_argument(
        "--profile",
        default="",
        help="profile name in .agents/profiles/ (worker, architect) or a path; "
             "omit to launch claude without a system prompt or worktree",
    )
    ap.add_argument(
        "--beads",
        default="",
        help='bead IDs or label to scope the agent to, e.g. "gax-cvi.1 gax-75t" or "gdoc"',
    )
    ap.add_argument("--extra-prompt", default="", help="appended to the system prompt")
    ap.add_argument(
        "--kickoff",
        default="go! Follow your session scope.",
        help="first message sent to the interactive claude session",
    )
    ap.add_argument(
        "--timeout", type=int, default=30, help="seconds to wait for claude banner"
    )
    ap.add_argument(
        "--workspace",
        default="",
        help="cmux workspace ref (e.g. workspace:4); default: cmux current-workspace",
    )
    ap.add_argument(
        "--no-fork",
        action="store_true",
        help="run in the current repo without creating a git worktree; "
             "the profile is still used as the system prompt",
    )
    args = ap.parse_args()

    repo = Path(sh("git", "rev-parse", "--show-toplevel"))

    if args.profile:
        # Resolve profile: explicit path, then walk cwd → $HOME looking for .agents/profiles/<name>.md
        profile_path = Path(args.profile)
        if not profile_path.exists():
            home = Path.home()
            search_dir = repo
            profile_path = None
            while True:
                candidate = search_dir / ".agents" / "profiles" / f"{args.profile}.md"
                if candidate.exists():
                    profile_path = candidate
                    break
                if search_dir == home or search_dir.parent == search_dir:
                    break
                search_dir = search_dir.parent
        if not profile_path or not profile_path.exists():
            sys.exit(f"error: profile not found: {args.profile}")
        profile_name = profile_path.stem
        system_prompt = profile_path.read_text(encoding="utf-8")

        if args.no_fork:
            # Run in-place: no worktree, no branch, no permissions file.
            worktree = repo
            branch = None
            claude_dir = repo / ".claude"
            claude_dir.mkdir(exist_ok=True)
        else:
            # Fork workspace: worktree on a fresh branch from main
            agent_id = secrets.token_hex(3)
            branch = f"{profile_name}/{agent_id}"
            worktree = repo.parent / f"{repo.name}-{profile_name}-{agent_id}"
            sh("git", "worktree", "add", str(worktree), "-b", branch, "main", cwd=repo)
            # Allow the worktree's .envrc — otherwise direnv silently falls back
            # to a parent .envrc and agents run against the wrong environment.
            if (worktree / ".envrc").exists():
                sh("direnv", "allow", str(worktree))
            # Grant edit/test/git permissions scoped to this worktree only
            claude_dir = worktree / ".claude"
            claude_dir.mkdir(exist_ok=True)
            (claude_dir / "settings.local.json").write_text(
                json.dumps(WORKTREE_PERMISSIONS, indent=2) + "\n", encoding="utf-8"
            )

        # Compose scope and write the full prompt (dies with the session for
        # forked worktrees; lives in .claude/ for --no-fork runs).
        # Passing it via a file avoids shell-quoting a multi-KB argument through
        # the cmux send pipeline.
        parts = [system_prompt, "", "## Session scope"]
        if args.no_fork:
            parts.append(f"You are running in the main checkout: {worktree}.")
        else:
            parts.append(
                f"Your worktree is already created: {worktree} on branch {branch} "
                f"(forked from main). You are running inside it. Skip any worktree "
                f"setup steps from the profile. Never modify the main checkout."
            )
        if args.beads:
            parts.append(
                f"Work ONLY on these beads/labels: {args.beads}. "
                f"Inspect them with `bd show <id>` (or `bd list -l <label>`) before starting."
            )
        else:
            parts.append("Find work with `bd ready`.")
        if args.extra_prompt:
            parts.append(args.extra_prompt)

        # Lifecycle policy — injected into every spawned worker session.
        parts += [
            "",
            "## Agent lifecycle",
            f"You were spawned by a master agent. Your session context:",
            f"- Working directory: {worktree}",
            f"- Surface: (see cmux — you are the most recently spawned surface)",
            "",
            "**Worker responsibilities:**",
            "1. Do the work scoped to your beads.",
            "2. When complete, record output on the bead: `bd note <id> 'output: <what you produced>'`",
            "3. Signal readiness for review: `bd label <id> review`",
            "4. Then STOP and wait. Do not exit, do not clean up, do not take further action.",
            "   Your master will review your work, send feedback or approval, and handle cleanup.",
            "",
            "**You do NOT:**",
            "- Close your own surface",
            "- Remove your worktree",
            "- Close your bead",
            "- Spawn further agents without explicit instruction",
            "",
            "The master that spawned you owns your lifecycle.",
        ]

        prompt_file = claude_dir / "system-prompt.md"
        prompt_file.write_text("\n".join(parts), encoding="utf-8")
    else:
        profile_name = "claude"
        worktree = repo

    # Resolve cmux workspace
    if args.workspace:
        workspace = args.workspace
    else:
        workspace = cmux("current-workspace").strip()

    # New tab (surface) in the workspace, running claude in the worktree
    out = cmux("new-surface", "--type", "terminal", "--workspace", workspace)
    # "OK surface:39 pane:4 workspace:4" -> surface:39
    surface = next(tok for tok in out.split() if tok.startswith("surface:"))

    model_flag = f"--model {shlex.quote(args.model)} " if args.model else ""
    if args.profile:
        cmd_str = (
            f"cd {shlex.quote(str(worktree))} && "
            f"claude {model_flag}"
            f'--system-prompt "$(cat .claude/system-prompt.md)"'
        )
    else:
        cmd_str = (
            f"cd {shlex.quote(str(worktree))} && "
            f"claude {model_flag}".rstrip()
        )
    cmux("send", "--surface", surface, "--workspace", workspace, cmd_str)
    cmux("send-key", "--surface", surface, "--workspace", workspace, "enter")

    # Wait for the claude banner, then send the kickoff
    deadline = time.time() + args.timeout
    ready = False
    while time.time() < deadline:
        time.sleep(2)
        try:
            screen = cmux(
                "read-screen", "--surface", surface,
                "--workspace", workspace, "--lines", "20",
            )
        except subprocess.CalledProcessError:
            continue
        if "Claude Code" in screen:
            ready = True
            break

    if not ready:
        sys.exit(
            f"error: claude banner not seen on {surface} within {args.timeout}s — "
            f"check the tab manually; kickoff NOT sent"
        )

    time.sleep(2)  # let the input box settle
    cmux("send", "--surface", surface, "--workspace", workspace, args.kickoff)
    time.sleep(1)
    cmux("send-key", "--surface", surface, "--workspace", workspace, "enter")

    title = f"{profile_name}: {args.beads or 'ready'}"
    cmux("rename-tab", "--surface", surface, "--workspace", workspace, title)

    # Stamp session metadata onto every bead that was scoped to this spawn.
    # This makes each bead self-describing: cwd, surface, workspace, pid, profile.
    if args.beads:
        # Get PID of the claude process on the new surface via cmux top
        pid = _get_surface_pid(surface, workspace)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        note = (
            f"spawned: {ts}\n"
            f"surface: {surface}\n"
            f"workspace: {workspace}\n"
            f"cwd: {worktree}\n"
            f"profile: {profile_name}\n"
            f"pid: {pid or 'unknown'}"
        )
        for bead_id in args.beads.split():
            try:
                sh("bd", "note", bead_id, note, cwd=repo)
            except subprocess.CalledProcessError:
                pass  # bead may be a label, not an ID — best effort

    print(f"spawned:   {surface} in {workspace}")
    if args.profile:
        if branch:
            print(f"worktree:  {worktree} on {branch}")
        else:
            print(f"directory: {worktree} (no-fork)")
    print(f"scope:     {args.beads or 'bd ready'}")
    print(f"kickoff:   {args.kickoff}")


if __name__ == "__main__":
    main()
