"""Runtime dependency injection layer for cspawn.

Production code uses RealRuntime; tests inject FakeRuntime.
Each sub-object exposes a semantic API plus an exec() fallback.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def _sh(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        cmd, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class CmuxRuntime:
    def exec(self, *args: str) -> str:
        return _sh("cmux", *args)

    def current_workspace(self) -> str:
        return self.exec("current-workspace")

    def new_surface(self, workspace: str, type: str = "terminal") -> str:
        out = self.exec("new-surface", "--type", type, "--workspace", workspace)
        return next(tok for tok in out.split() if tok.startswith("surface:"))

    def send(self, surface: str, workspace: str, text: str) -> None:
        self.exec("send", "--surface", surface, "--workspace", workspace, text)

    def send_key(self, surface: str, workspace: str, key: str) -> None:
        self.exec("send-key", "--surface", surface, "--workspace", workspace, key)

    def read_screen(self, surface: str, workspace: str, lines: int = 20) -> str:
        return self.exec(
            "read-screen", "--surface", surface,
            "--workspace", workspace, "--lines", str(lines),
        )

    def rename_tab(self, surface: str, workspace: str, title: str) -> None:
        self.exec("rename-tab", "--surface", surface, "--workspace", workspace, title)

    def top(self, workspace: str, processes: bool = False) -> str:
        args = ["top", "--workspace", workspace]
        if processes:
            args.append("--processes")
        return self.exec(*args)


class GitRuntime:
    def exec(self, *args: str, cwd: Path | None = None) -> str:
        return _sh("git", *args, cwd=cwd)

    def repo_root(self) -> Path:
        return Path(self.exec("rev-parse", "--show-toplevel"))

    def worktree_add(self, path: Path, branch: str | None = None, cwd: Path | None = None) -> None:
        cmd = ["worktree", "add", str(path)]
        if branch:
            cmd += ["-b", branch]
        self.exec(*cmd, cwd=cwd)


class EnvRuntime:
    def workspace_id(self) -> str:
        return os.environ.get("CMUX_WORKSPACE_ID", "")

    def surface_id(self) -> str:
        return os.environ.get("CMUX_SURFACE_ID", "")


class Runtime:
    def __init__(self) -> None:
        self.cmux = CmuxRuntime()
        self.git = GitRuntime()
        self.env = EnvRuntime()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.time()


# Module-level singleton for production use
_real: Runtime | None = None


def get_runtime() -> Runtime:
    global _real
    if _real is None:
        _real = Runtime()
    return _real
