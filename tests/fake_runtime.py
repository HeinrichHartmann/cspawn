"""FakeRuntime for testing — records all calls, returns canned values."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Call:
    method: str
    args: tuple
    kwargs: dict


class FakeCmux:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._workspace = "workspace:1"
        self._screen = ""
        self._surface_counter = 1

    def exec(self, *args: str) -> str:
        self.calls.append(Call("exec", args, {}))
        return ""

    def current_workspace(self) -> str:
        self.calls.append(Call("current_workspace", (), {}))
        return self._workspace

    def new_surface(self, workspace: str, type: str = "terminal") -> str:
        surface = f"surface:{self._surface_counter}"
        self._surface_counter += 1
        self.calls.append(Call("new_surface", (workspace,), {"type": type}))
        return surface

    def send(self, surface: str, workspace: str, text: str) -> None:
        self.calls.append(Call("send", (surface, workspace, text), {}))

    def send_key(self, surface: str, workspace: str, key: str) -> None:
        self.calls.append(Call("send_key", (surface, workspace, key), {}))

    def read_screen(self, surface: str, workspace: str, lines: int = 20) -> str:
        self.calls.append(Call("read_screen", (surface, workspace), {"lines": lines}))
        return self._screen

    def rename_tab(self, surface: str, workspace: str, title: str) -> None:
        self.calls.append(Call("rename_tab", (surface, workspace, title), {}))

    def top(self, workspace: str, processes: bool = False) -> str:
        self.calls.append(Call("top", (workspace,), {"processes": processes}))
        return ""

    # Helpers for test setup
    def set_workspace(self, ws: str) -> None:
        self._workspace = ws

    def set_screen(self, screen: str) -> None:
        self._screen = screen

    def called(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]


class FakeGit:
    def __init__(self, repo_root: Path) -> None:
        self.calls: list[Call] = []
        self._repo_root = repo_root

    def exec(self, *args: str, cwd: Path | None = None) -> str:
        self.calls.append(Call("exec", args, {"cwd": cwd}))
        return ""

    def repo_root(self) -> Path:
        self.calls.append(Call("repo_root", (), {}))
        return self._repo_root

    def worktree_add(self, path: Path, branch: str | None = None, cwd: Path | None = None) -> None:
        self.calls.append(Call("worktree_add", (path,), {"branch": branch, "cwd": cwd}))

    def called(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]


class FakeEnv:
    def __init__(self, workspace_id: str = "", surface_id: str = "") -> None:
        self._workspace_id = workspace_id
        self._surface_id = surface_id

    def workspace_id(self) -> str:
        return self._workspace_id

    def surface_id(self) -> str:
        return self._surface_id


class FakeRuntime:
    def __init__(self, repo_root: Path = Path("/fake/repo")) -> None:
        self.cmux = FakeCmux()
        self.git = FakeGit(repo_root)
        self.env = FakeEnv()
        self._now = 0.0

    def sleep(self, seconds: float) -> None:
        self._now += seconds

    def now(self) -> float:
        return self._now
