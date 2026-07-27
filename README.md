# cspawn

Spawn profiled Claude Code agents in isolated git worktrees via [cmux](https://github.com/gastownhall/cmux).

Each invocation:
1. Reads an agent profile from `.agents/profiles/<name>.md`
2. Creates a fresh git worktree on a new branch forked from `main`
3. Writes scoped `settings.local.json` permissions into the worktree
4. Opens a new terminal tab in the current cmux workspace, starts `claude` inside the worktree
5. Waits for the Claude banner, then sends a kickoff message

## Install

```sh
uv tool install git+https://github.com/heinrichhartmann/cspawn
```

## Usage

```sh
cspawn --profile worker --beads "gax-sy6 gax-qo8"
cspawn --profile worker --beads "gdoc"          # by label
cspawn --profile architect --model sonnet
cspawn --profile worker --extra-prompt "Focus on the auth module only."
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | required | Profile name in `.agents/profiles/` or a file path |
| `--model` | `opus` | Claude model to use |
| `--beads` | | Bead IDs or label to scope the agent to |
| `--extra-prompt` | | Text appended to the system prompt |
| `--kickoff` | `go! Follow your session scope.` | First message sent to Claude |
| `--timeout` | `30` | Seconds to wait for Claude banner |
| `--workspace` | auto | cmux workspace ref (e.g. `workspace:4`) |

## Requirements

- [`cmux`](https://github.com/gastownhall/cmux) in `PATH`
- `direnv` (optional — used if `.envrc` exists in the repo)
- `git` with worktree support
- `claude` CLI ([Claude Code](https://claude.ai/code))
