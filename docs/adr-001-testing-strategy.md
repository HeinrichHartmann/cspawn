# ADR-001: Testing Strategy for cspawn

## Status
Accepted

## Context

`cspawn` is a CLI tool that orchestrates git worktrees, cmux terminal sessions,
and claude agent processes. Almost all production behavior involves side effects:
shelling out to `cmux`, `git`, `direnv`, and `claude`. This makes naive unit
testing fragile (mock every subprocess call) and integration testing expensive
(requires a live cmux instance).

## Decision

Introduce a `Runtime` dependency injection layer with a semantic API. The spawn
logic receives a `Runtime` instance rather than calling global functions directly.
Tests inject a `FakeRuntime`; production uses `RealRuntime`.

### Runtime structure

```python
runtime.cmux.exec(*args)              # fallback / escape hatch
runtime.cmux.new_surface(workspace, type="terminal") -> str   # returns surface id
runtime.cmux.send(surface, workspace, text)
runtime.cmux.send_key(surface, workspace, key)
runtime.cmux.read_screen(surface, workspace, lines=20) -> str
runtime.cmux.rename_tab(surface, workspace, title)
runtime.cmux.current_workspace() -> str

runtime.git.exec(*args, cwd=None)     # fallback
runtime.git.repo_root() -> Path
runtime.git.worktree_add(path, branch=None, cwd=None)

runtime.env.workspace_id() -> str     # $CMUX_WORKSPACE_ID
runtime.env.surface_id() -> str       # $CMUX_SURFACE_ID

runtime.claude.spawn(cwd, model, allowed_tools, system_prompt) -> str  # surface id
```

Each sub-object (`cmux`, `git`, `env`) is its own class. The `RealRuntime`
wraps actual subprocess calls. The `FakeRuntime` records calls and returns
canned values — assertions are at the semantic level.

### Test layers

1. **Pure unit tests** (no runtime needed):
   - `parse_frontmatter` — edge cases, malformed YAML
   - `collect_profiles` — shadowing, walk order, missing dirs
   - Branch name generation
   - Tool entry validation

2. **Runtime unit tests** (FakeRuntime):
   - Spawn with a profile: correct cmux calls, correct system prompt content
   - Workspace routing: cmux.current_workspace() used when env var absent
   - Worktree creation: git.worktree_add called with correct branch
   - `--no-fork`: no worktree_add, cwd is repo root
   - `--no-branch`: worktree_add called without branch arg
   - Banner timeout: error raised when read_screen never returns "Claude Code"

3. **Integration tests** (real git, skip in CI without cmux):
   - Profile resolution with a real temp directory tree
   - `git worktree add` round-trip

## Consequences

- Spawn logic must be refactored to accept a `runtime` parameter
- `FakeRuntime` becomes the primary testing surface — no subprocess mocking
- Pure functions stay standalone (no injection needed)
- New cmux commands can be added to the semantic API incrementally;
  `exec()` serves as the fallback until promoted
