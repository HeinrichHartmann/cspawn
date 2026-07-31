"""Spawn a profiled claude agent in a new cmux tab.

Source: https://github.com/HeinrichHartmann/cspawn
Run `cspawn --info` for the full manual.
"""

MANUAL = """
cspawn — spawn a profiled claude agent in a new cmux tab
=========================================================
Source: https://github.com/HeinrichHartmann/cspawn

How it works
------------
1. Resolves the profile from .agents/profiles/<name>.md (walked from
   cwd up to $HOME; closer profiles win).
2. Creates a git worktree at ../<repo>-<profile>-<id> on a new branch
   (default: bead IDs, or agent/<random>). The agent branches from HEAD.
3. Opens a new terminal tab in the current cmux workspace and starts
   claude inside the worktree with the profile as the system prompt.
4. Waits for the claude banner, then sends the prompt.

Usage
-----
    cspawn profiles                          # list available profiles
    cspawn "do the thing" --profile worker --beads "gax-sy6 gax-qo8"
    cspawn "research X"   --profile researcher --no-fork
    cspawn                                   # show help

Profile frontmatter
-------------------
Profiles may start with a YAML frontmatter block:

    ---
    description: one-line summary
    when: conditions under which to invoke this profile
    allowed-tools: Edit, Write, Bash(git:*), Bash(bd:*)
    updated: YYYY-MM-DD
    ---

The `allowed-tools` field is passed verbatim as --allowed-tools to claude.
Entries must match `ToolName` or `ToolName(pattern)`.
If absent, a built-in default set is used.

Agent lifecycle
---------------
WORKER (the spawned agent):
  - Do the work scoped to your beads.
  - When done: bd note <id> "output: <what you produced>"
  - Signal completion: bd label <id> review
  - Then stop and wait. Your master owns your lifecycle.

MASTER (the agent that called cspawn):
  - Own the full lifecycle of every surface you spawn.
  - Check for completed work: bd children <master-id> --label review
  - Review and approve or send feedback via a new message on the surface.
  - On approval:
      bd close <worker-bead-id>
      cmux close-surface --surface <surface-id>
      git worktree remove <worktree-path>
  - Never leave surfaces or worktrees running after work is accepted.
"""

import datetime
import re
import secrets
import shlex
import subprocess
from pathlib import Path

import click

from cspawn.runtime import Runtime, get_runtime

WORKTREE_PERMISSIONS_ALLOW = [
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

_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\([^)]*\))?$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
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


def print_profiles(start_dir: Path) -> None:
    items = collect_profiles(start_dir)
    if not items:
        click.echo("No profiles found between here and $HOME.")
        return
    click.echo()
    for name, path in items:
        text = path.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        try:
            display_path = Path("~") / path.relative_to(Path.home())
        except ValueError:
            display_path = path
        click.echo(f"  \033[1m{name}\033[0m")
        if desc := meta.get("description", ""):
            click.echo(f"    {desc}")
        if when := meta.get("when", ""):
            click.echo(f"    \033[2mwhen:\033[0m  {when}")
        if tools := meta.get("allowed-tools", meta.get("permissions", "")):
            click.echo(f"    \033[2mtools:\033[0m {tools}")
        footer = str(display_path)
        if updated := meta.get("updated", ""):
            footer += f"  (updated: {updated})"
        click.echo(f"    \033[2m{footer}\033[0m")
        click.echo()


def _get_surface_pid(surface: str, workspace: str, rt: Runtime) -> str | None:
    try:
        out = rt.cmux.top(workspace, processes=True)
        for line in out.splitlines():
            if surface in line:
                for tok in line.split():
                    if tok.startswith("pid:"):
                        return tok[4:]
    except subprocess.CalledProcessError:
        pass
    return None


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("prompt", required=False, default="")
@click.option("--info", is_flag=True, help="Show full manual and exit.")
@click.option("--model", default="", help="Claude model override.")
@click.option("--profile", "-p", default="", help="Profile name or path.")
@click.option("--beads", default="", help='Bead IDs or label, e.g. "gax-cvi gax-75t".')
@click.option("--extra-prompt", default="", help="Appended to the system prompt.")
@click.option("--timeout", default=30, show_default=True, help="Seconds to wait for claude banner.")
@click.option("--workspace", default="", help="cmux workspace ref; default: cmux current-workspace.")
@click.option("--no-fork", is_flag=True, help="Run in current repo without a git worktree.")
@click.option("--branch", "-b", default="", help="Branch name for the worktree.")
@click.option("--no-branch", is_flag=True, help="Create worktree without a branch (detached HEAD).")
@click.option("--here", is_flag=True, help="Launch in this tab instead of a new one.")
@click.pass_context
def main(
    ctx: click.Context,
    prompt: str,
    info: bool,
    model: str,
    profile: str,
    beads: str,
    extra_prompt: str,
    timeout: int,
    workspace: str,
    no_fork: bool,
    branch: str,
    no_branch: bool,
    here: bool,
    rt: Runtime | None = None,
) -> None:
    """Spawn a profiled claude agent in a new cmux tab.

    PROMPT is the first message sent to the agent.
    Pass 'profiles' as PROMPT to list available profiles.
    """
    if rt is None:
        rt = get_runtime()

    if info:
        click.echo(MANUAL)
        return

    if not prompt:
        click.echo(ctx.get_help())
        return

    if prompt == "profiles":
        try:
            start = rt.git.repo_root()
        except subprocess.CalledProcessError:
            start = Path.cwd()
        print_profiles(start)
        return

    if branch and no_branch:
        raise click.UsageError("--branch and --no-branch are mutually exclusive.")

    repo = rt.git.repo_root()

    if profile:
        profile_path = Path(profile)
        if not profile_path.exists():
            home = Path.home()
            search_dir = repo
            profile_path = None
            while True:
                candidate = search_dir / ".agents" / "profiles" / f"{profile}.md"
                if candidate.exists():
                    profile_path = candidate
                    break
                if search_dir == home or search_dir.parent == search_dir:
                    break
                search_dir = search_dir.parent
        if not profile_path or not profile_path.exists():
            raise click.ClickException(f"profile not found: {profile}")
        profile_name = profile_path.stem
        raw_profile = profile_path.read_text(encoding="utf-8")
        profile_meta, system_prompt = parse_frontmatter(raw_profile)

        raw_tools = profile_meta.get("allowed-tools", profile_meta.get("permissions", ""))
        if raw_tools:
            allowed_tools = []
            for entry in raw_tools.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if not _TOOL_RE.match(entry):
                    raise click.ClickException(f"invalid allowed-tools entry: {entry!r}")
                allowed_tools.append(entry)
        else:
            allowed_tools = WORKTREE_PERMISSIONS_ALLOW

        if no_fork:
            worktree = repo
            branch_used = None
        else:
            agent_id = secrets.token_hex(3)
            if branch:
                branch_used = branch
            elif no_branch:
                branch_used = None
            elif beads:
                branch_used = beads.strip().replace(" ", "-")
            else:
                branch_used = "agent/" + "".join(
                    chr(ord("a") + b % 26) for b in secrets.token_bytes(6)
                )
            worktree = repo.parent / f"{repo.name}-{profile_name}-{agent_id}"
            rt.git.worktree_add(worktree, branch=branch_used, cwd=repo)
            if (worktree / ".envrc").exists():
                rt.git.exec("direnv", "allow", str(worktree))

        parts = [system_prompt, "", "## Session scope"]
        if no_fork:
            parts.append(f"You are running in the main checkout: {worktree}.")
        else:
            branch_info = f" on branch {branch_used}" if branch_used else ""
            parts.append(
                f"Your worktree is already created: {worktree}{branch_info}. "
                f"You are running inside it. Never modify the main checkout."
            )
        if beads:
            parts.append(
                f"Work ONLY on these beads/labels: {beads}. "
                f"Inspect them with `bd show <id>` (or `bd list -l <label>`) before starting."
            )
        else:
            parts.append("Find work with `bd ready`.")
        if extra_prompt:
            parts.append(extra_prompt)

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
        branch_used = None
        allowed_tools = []
        system_prompt_full = ""

    if not workspace:
        workspace = rt.cmux.current_workspace()

    if here:
        surface = rt.env.surface_id()
        if not surface:
            raise click.ClickException("--here requires $CMUX_SURFACE_ID (must be run inside a cmux terminal)")
    else:
        surface = rt.cmux.new_surface(workspace)

    claude_argv = ["claude"]
    if model:
        claude_argv += ["--model", model]
    if profile:
        claude_argv += ["--allowed-tools", ",".join(allowed_tools)]
        claude_argv += ["--system-prompt", system_prompt_full]
    cmd_str = f"cd {shlex.quote(str(worktree))} && " + " ".join(shlex.quote(tok) for tok in claude_argv)
    rt.cmux.send(surface, workspace, cmd_str)
    rt.cmux.send_key(surface, workspace, "enter")

    deadline = rt.now() + timeout
    ready = False
    while rt.now() < deadline:
        rt.sleep(2)
        try:
            screen = rt.cmux.read_screen(surface, workspace)
        except subprocess.CalledProcessError:
            continue
        if "Claude Code" in screen:
            ready = True
            break

    if not ready:
        raise click.ClickException(
            f"claude banner not seen on {surface} within {timeout}s — "
            f"check the tab manually; kickoff NOT sent"
        )

    rt.sleep(2)
    rt.cmux.send(surface, workspace, prompt)
    rt.sleep(1)
    rt.cmux.send_key(surface, workspace, "enter")

    title = f"{profile_name}: {beads or 'ready'}"
    rt.cmux.rename_tab(surface, workspace, title)

    if beads:
        pid = _get_surface_pid(surface, workspace, rt)
        ts = datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds")
        note = (
            f"spawned: {ts}\n"
            f"surface: {surface}\n"
            f"workspace: {workspace}\n"
            f"cwd: {worktree}\n"
            f"profile: {profile_name}\n"
            f"pid: {pid or 'unknown'}"
        )
        for bead_id in beads.split():
            try:
                rt.git.exec("bd", "note", bead_id, note, cwd=repo)
            except subprocess.CalledProcessError:
                pass

    click.echo(f"spawned:   {surface} in {workspace}")
    if profile:
        if no_fork:
            click.echo(f"directory: {worktree} (no-fork)")
        elif branch_used:
            click.echo(f"worktree:  {worktree} on {branch_used}")
        else:
            click.echo(f"worktree:  {worktree} (detached)")
    click.echo(f"scope:     {beads or 'bd ready'}")
    click.echo(f"prompt:    {prompt}")


if __name__ == "__main__":
    main()
