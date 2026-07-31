---
name: cspawn
description: Spawn one or more profiled Claude agents in isolated git worktrees via cmux terminal tabs
allowed-tools: Bash(cspawn:*), Bash(bd:*), Bash(cmux:*), Bash(git worktree:*), Bash(git status:*), Bash(git log:*)
---

# cspawn Skill

Use this skill when asked to spawn an agent, fan out work to agents, or distribute tasks across parallel Claude workers.

## When to Use

- "Spawn an agent to work on X"
- "Fan this out to a worker"
- "Spawn agents for each of these beads"
- "Parallelize this work"
- "Create a worker for bead <id>"
- Any time you need a subordinate agent to run independently

## Prerequisites

- `cspawn` is installed (`cspawn --help`)
- `cmux` is running and `$CMUX_WORKSPACE_ID` / `$CMUX_SURFACE_ID` are set
- A profile exists for the task (run `cspawn profiles` to see available profiles)
- Beads issues are created for the work to be delegated (use `bd create` if needed)

## Core Concepts

### Roles
- **Master** (you): spawns workers, owns their lifecycle, reviews output, handles cleanup
- **Worker** (spawned agent): does scoped work, signals `review` when done, waits for master

### Profiles
Profiles live in `.agents/profiles/<name>.md` (walked from cwd to `$HOME`). Each has:
- A system prompt body (how the agent should behave)
- Optional frontmatter: `allowed-tools`, `description`, `when`, `updated`

Run `cspawn profiles` to see what's available from the current directory.

## Spawning a Single Agent

```bash
# Basic spawn with profile and bead scope
cspawn "do the work" --profile worker --beads "bd-abc bd-xyz"

# With explicit branch name
cspawn "implement feature" --profile developer --beads "bd-abc" --branch "feat/my-feature"

# Without a worktree (run in main checkout)
cspawn "review this PR" --profile reviewer --no-fork

# In current tab instead of a new one
cspawn "quick task" --profile worker --here
```

### Choosing a Profile
- Use `cspawn profiles` to list available profiles with their descriptions
- Match the profile to the task: `worker` for implementation, `researcher` for analysis, etc.
- When in doubt, pick the closest match — the system prompt can be augmented with `--extra-prompt`

### Branch Strategy
- Default: branch named after bead IDs (e.g. `bd-abc-bd-xyz`), or random `agent/xxxxxx`
- `--branch <name>`: explicit branch
- `--no-branch`: detached HEAD (useful for read-only work)
- `--no-fork`: no worktree at all, runs in main checkout

## Spawning Multiple Agents (Fan-Out)

When distributing work across many beads, spawn one agent per unit of work:

```bash
# First ensure the beads exist and are ready
bd list --status=open

# Spawn in a loop (each gets its own worktree and branch)
for id in bd-aa1 bd-bb2 bd-cc3; do
  cspawn "work on your assigned bead" --profile worker --beads "$id"
done
```

For large fan-outs, check cmux capacity first — each spawn opens a terminal tab.

## Monitoring Workers

```bash
# Check which beads are flagged review (worker signalled done)
bd children <master-bead-id> --label review

# See what's running in cmux
cmux top --workspace <workspace-ref>

# Read a worker's terminal screen
cmux read-screen --surface <surface-id> --workspace <workspace-ref>
```

## Reviewing and Closing Workers

When a worker signals `review`:

1. Read their output: `bd show <worker-bead-id>`
2. Check the worktree changes: `git -C <worktree-path> diff main`
3. If approved:
   ```bash
   bd close <worker-bead-id>
   cmux close-surface --surface <surface-id>
   git worktree remove <worktree-path>
   ```
4. If feedback needed: send a message to the surface via `cmux send`

**Never leave surfaces or worktrees running after work is accepted.**

## Master Lifecycle Checklist

Before declaring a fan-out task complete, verify:

```
[ ] All worker beads are closed
[ ] All cmux surfaces from this session are closed
[ ] All temporary worktrees are removed
[ ] Your master bead notes reference the outcome
```

```bash
# Quick health check
bd children <master-id>           # should all be closed
cmux top --workspace <workspace>  # no stray surfaces
git worktree list                 # no leftover worktrees
```

## Worked Example

**Task**: "Implement fixes for beads bd-111, bd-222, bd-333 in parallel"

```bash
# 1. Verify beads exist and are ready
bd show bd-111; bd show bd-222; bd show bd-333

# 2. Check available profiles
cspawn profiles

# 3. Fan out — each worker gets its own worktree
cspawn "implement the fix described in your bead" --profile worker --beads bd-111
cspawn "implement the fix described in your bead" --profile worker --beads bd-222
cspawn "implement the fix described in your bead" --profile worker --beads bd-333

# 4. Poll for completion
bd children <master-id> --label review

# 5. Review each, then clean up approved work
bd close bd-111
cmux close-surface --surface surface:12
git worktree remove ../myrepo-worker-a1b2c3

# ... repeat for bd-222, bd-333
```

## Error Handling

| Problem | Fix |
|---------|-----|
| `profile not found` | Run `cspawn profiles` to see available names |
| `claude banner not seen` | Tab may have failed to start; check the tab manually |
| `--here requires $CMUX_SURFACE_ID` | You're not inside a cmux terminal |
| Worker unresponsive | Read screen with `cmux read-screen`; send a nudge via `cmux send` |
| Worktree conflicts | Check `git worktree list` — remove stale entries with `git worktree prune` |

## Full Reference

```bash
cspawn --info    # Full manual
cspawn --help    # Argument reference
cspawn profiles  # List available profiles from current directory
```
