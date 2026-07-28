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
4. Waits for the claude banner, then sends the prompt so the interactive
   session starts working instead of idling.

Usage:
    cspawn profiles                                           # list profiles (repo → $HOME)

    cspawn "implement the beads" --profile worker --beads "gax-sy6 gax-qo8"
    cspawn "research X" --no-fork --profile researcher        # run in current repo, no worktree
    cspawn          # no prompt → prints this help and exits

Profile frontmatter
-------------------
Profiles may start with a YAML frontmatter block:

    ---
    description: one-line summary
    when: conditions under which to invoke this profile
    capabilities: what the agent can do
    allowed-tools: Edit, Write, Bash(git:*), Bash(bd:*)  # passed as --allowed-tools to claude
    updated: YYYY-MM-DD
    ---

The `allowed-tools` field (comma-separated) is passed verbatim as
`--allowed-tools` to claude. Entries must match `ToolName` or `ToolName(pattern)`.
If absent, a built-in default set is used. Run `cspawn profiles` to list profiles.

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


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata_dict, body) from an optional YAML frontmatter block."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    meta: dict = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


def collect_profiles(start_dir: Path) -> list[tuple[str, Path]]:
    """Walk start_dir → $HOME collecting .agents/profiles/*.md (closer wins)."""
    home = Path.home()
    seen: dict[str, Path] = {}
    d = start_dir
    while True:
        profiles_dir = d / ".agents" / "profiles"
        if profiles_dir.is_dir():
            for p in sorted(profiles_dir.glob("*.md")):
                if p.stem not in seen:
                    seen[p.stem] = p
        if d == home or d.parent == d:
            break
        d = d.parent
    return sorted(seen.items())


def cmd_profiles(start_dir: Path) -> None:
    profiles = collect_profiles(start_dir)
    if not profiles:
        print("No profiles found between here and $HOME.")
        return
    print()
    for name, path in profiles:
        text = path.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        try:
            display_path = Path("~") / path.relative_to(Path.home())
        except ValueError:
            display_path = path
        description = meta.get("description", "")
        when = meta.get("when", "")
        tools_display = meta.get("allowed-tools", meta.get("permissions", ""))
        updated = meta.get("updated", "")
        print(f"  \033[1m{name}\033[0m")
        if description:
            print(f"    {description}")
        if when:
            print(f"    \033[2mwhen:\033[0m  {when}")
        if tools_display:
            print(f"    \033[2mtools:\033[0m {tools_display}")
        footer = str(display_path)
        if updated:
            footer += f"  (updated: {updated})"
        print(f"    \033[2m{footer}\033[0m")
        print()


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="", help="claude model override; omit to use global settings.json default")
    ap.add_argument(
        "--profile",
        default="",
        help="profile name in .agents/profiles/ (worker, architect) or a path; "
             "omit to launch claude without a system prompt or worktree. "
             "Profiles may contain YAML frontmatter with an `allowed-tools:` key "
             "(comma-separated, e.g. 'Edit,Bash(git:*)') passed as --allowed-tools to claude. "
             "Run `cspawn profiles` to list available profiles.",
    )
    ap.add_argument(
        "--beads",
        default="",
        help='bead IDs or label to scope the agent to, e.g. "gax-cvi.1 gax-75t" or "gdoc"',
    )
    ap.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="first message sent to the agent; if omitted, help is shown and nothing is spawned",
    )
    ap.add_argument("--extra-prompt", default="", help="appended to the system prompt")
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
    ap.add_argument(
        "--here",
        action="store_true",
        help="launch claude in this tab instead of opening a new one; "
             "uses $CMUX_SURFACE_ID and $CMUX_WORKSPACE_ID from the current terminal",
    )
    args = ap.parse_args()

    if args.prompt == "profiles":
        try:
            start = Path(sh("git", "rev-parse", "--show-toplevel"))
        except subprocess.CalledProcessError:
            start = Path.cwd()
        cmd_profiles(start)
        sys.exit(0)

    if not args.prompt:
        ap.print_help()
        sys.exit(0)

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
        raw_profile = profile_path.read_text(encoding="utf-8")
        profile_meta, system_prompt = parse_frontmatter(raw_profile)

        # Resolve allowed tools from profile frontmatter, fallback to defaults.
        _TOOL_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_]*(\([^)]*\))?$")
        raw_tools = profile_meta.get("allowed-tools", profile_meta.get("permissions", ""))
        if raw_tools:
            allowed_tools = []
            for entry in raw_tools.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if not _TOOL_RE.match(entry):
                    sys.exit(f"error: invalid allowed-tools entry in profile: {entry!r}")
                allowed_tools.append(entry)
        else:
            allowed_tools = WORKTREE_PERMISSIONS["permissions"]["allow"]

        if args.no_fork:
            worktree = repo
        else:
            agent_id = secrets.token_hex(3)
            worktree = repo.parent / f"{repo.name}-{profile_name}-{agent_id}"
            sh("git", "worktree", "add", str(worktree), cwd=repo)
            # Allow the worktree's .envrc — otherwise direnv silently falls back
            # to a parent .envrc and agents run against the wrong environment.
            if (worktree / ".envrc").exists():
                sh("direnv", "allow", str(worktree))

        parts = [system_prompt, "", "## Session scope"]
        if args.no_fork:
            parts.append(f"You are running in the main checkout: {worktree}.")
        else:
            parts.append(
                f"Your worktree is already created: {worktree}. "
                f"You are running inside it. Never modify the main checkout."
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
            "You were spawned by a master agent. Your session context:",
            f"- Working directory: {worktree}",
            "- Surface: (see cmux — you are the most recently spawned surface)",
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

        system_prompt_full = "\n".join(parts)
    else:
        profile_name = "claude"
        worktree = repo

    # Resolve cmux workspace and surface
    if args.workspace:
        workspace = args.workspace
    else:
        workspace = os.environ.get("CMUX_WORKSPACE_ID") or cmux("current-workspace").strip()

    if args.here:
        surface = os.environ.get("CMUX_SURFACE_ID", "")
        if not surface:
            sys.exit("error: --here requires $CMUX_SURFACE_ID (must be run inside a cmux terminal)")
    else:
        out = cmux("new-surface", "--type", "terminal", "--workspace", workspace)
        # "OK surface:39 pane:4 workspace:4" -> surface:39
        surface = next(tok for tok in out.split() if tok.startswith("surface:"))

    claude_argv = ["claude"]
    if args.model:
        claude_argv += ["--model", args.model]
    if args.profile:
        claude_argv += ["--allowed-tools", ",".join(allowed_tools)]
        claude_argv += ["--system-prompt", system_prompt_full]
    cmd_str = f"cd {shlex.quote(str(worktree))} && " + " ".join(shlex.quote(tok) for tok in claude_argv)
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
    cmux("send", "--surface", surface, "--workspace", workspace, args.prompt)
    time.sleep(1)
    cmux("send-key", "--surface", surface, "--workspace", workspace, "enter")

    title = f"{profile_name}: {args.beads or 'ready'}"
    cmux("rename-tab", "--surface", surface, "--workspace", workspace, title)

    # Stamp session metadata onto every bead that was scoped to this spawn.
    # This makes each bead self-describing: cwd, surface, workspace, pid, profile.
    if args.beads:
        # Get PID of the claude process on the new surface via cmux top
        pid = _get_surface_pid(surface, workspace)
        ts = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
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
        if args.no_fork:
            print(f"directory: {worktree} (no-fork)")
        else:
            print(f"worktree:  {worktree}")
    print(f"scope:     {args.beads or 'bd ready'}")
    print(f"prompt:    {args.prompt}")


if __name__ == "__main__":
    main()
