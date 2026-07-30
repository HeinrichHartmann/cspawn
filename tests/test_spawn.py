"""Runtime unit tests using FakeRuntime."""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from cspawn.main import main
from tests.fake_runtime import FakeRuntime


def make_profile(tmp_path: Path, name: str = "worker", body: str = "You are a worker.", tools: str = "") -> Path:
    profiles_dir = tmp_path / ".agents" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    fm = f"---\ndescription: test profile\n"
    if tools:
        fm += f"allowed-tools: {tools}\n"
    fm += f"---\n\n{body}"
    p = profiles_dir / f"{name}.md"
    p.write_text(fm)
    return p


def invoke(args: list[str], rt: FakeRuntime) -> object:
    """Run the click command with a fake runtime injected."""
    runner = CliRunner()
    # Inject rt by patching get_runtime
    import cspawn.main as m
    original = m.get_runtime
    m.get_runtime = lambda: rt
    try:
        result = runner.invoke(main, args, catch_exceptions=False)
    finally:
        m.get_runtime = original
    return result


class TestNoProfile:
    def test_no_prompt_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(main, [], catch_exceptions=False)
        assert result.exit_code == 0
        assert "PROMPT" in result.output

    def test_info_flag(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--info"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Agent lifecycle" in result.output


class TestWorkspaceRouting:
    def test_uses_current_workspace_by_default(self, tmp_path):
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_workspace("workspace:3")
        rt.cmux.set_screen("Claude Code")
        result = invoke(["hello"], rt)
        assert result.exit_code == 0
        new_surface_calls = rt.cmux.called("new_surface")
        assert new_surface_calls[0].args[0] == "workspace:3"

    def test_explicit_workspace_overrides(self, tmp_path):
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        result = invoke(["hello", "--workspace", "workspace:9"], rt)
        assert result.exit_code == 0
        new_surface_calls = rt.cmux.called("new_surface")
        assert new_surface_calls[0].args[0] == "workspace:9"


class TestWorktreeCreation:
    def test_worktree_created_with_profile(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        result = invoke(["do work", "--profile", "worker"], rt)
        assert result.exit_code == 0
        wt_calls = rt.git.called("worktree_add")
        assert len(wt_calls) == 1

    def test_branch_from_beads(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker", "--beads", "abc-123 abc-456"], rt)
        wt_calls = rt.git.called("worktree_add")
        assert wt_calls[0].kwargs["branch"] == "abc-123-abc-456"

    def test_explicit_branch(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker", "--branch", "feat/my-branch"], rt)
        wt_calls = rt.git.called("worktree_add")
        assert wt_calls[0].kwargs["branch"] == "feat/my-branch"

    def test_no_branch_detached(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker", "--no-branch"], rt)
        wt_calls = rt.git.called("worktree_add")
        assert wt_calls[0].kwargs["branch"] is None

    def test_no_fork_skips_worktree(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker", "--no-fork"], rt)
        assert rt.git.called("worktree_add") == []


class TestSystemPrompt:
    def test_profile_body_in_system_prompt(self, tmp_path):
        make_profile(tmp_path, body="You are a specialist agent.")
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker"], rt)
        send_calls = rt.cmux.called("send")
        cmd = send_calls[0].args[2]
        assert "--system-prompt" in cmd
        assert "You are a specialist agent." in cmd

    def test_beads_in_system_prompt(self, tmp_path):
        make_profile(tmp_path)
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker", "--beads", "csp-abc"], rt)
        send_calls = rt.cmux.called("send")
        cmd = send_calls[0].args[2]
        assert "csp-abc" in cmd

    def test_tools_from_profile_frontmatter(self, tmp_path):
        make_profile(tmp_path, tools="Edit, Write, Bash(git:*)")
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("Claude Code")
        invoke(["do work", "--profile", "worker"], rt)
        send_calls = rt.cmux.called("send")
        cmd = send_calls[0].args[2]
        assert "--allowed-tools" in cmd
        assert "Edit,Write,Bash(git:*)" in cmd


class TestBannerTimeout:
    def test_error_when_banner_not_seen(self, tmp_path):
        rt = FakeRuntime(repo_root=tmp_path)
        rt.cmux.set_screen("")  # never shows "Claude Code"
        runner = CliRunner()
        import cspawn.main as m
        original = m.get_runtime
        m.get_runtime = lambda: rt
        try:
            result = runner.invoke(main, ["hello", "--timeout", "0"], catch_exceptions=False)
        finally:
            m.get_runtime = original
        assert result.exit_code != 0
        assert "banner not seen" in result.output


class TestHereFlag:
    def test_here_uses_surface_id(self, tmp_path):
        rt = FakeRuntime(repo_root=tmp_path)
        rt.env._surface_id = "surface:99"
        rt.cmux.set_screen("Claude Code")
        result = invoke(["hello", "--here"], rt)
        assert result.exit_code == 0
        # new_surface should NOT be called
        assert rt.cmux.called("new_surface") == []
        # send should target surface:99
        send_calls = rt.cmux.called("send")
        assert send_calls[0].args[0] == "surface:99"

    def test_here_without_surface_id_errors(self, tmp_path):
        rt = FakeRuntime(repo_root=tmp_path)
        rt.env._surface_id = ""
        runner = CliRunner()
        import cspawn.main as m
        original = m.get_runtime
        m.get_runtime = lambda: rt
        try:
            result = runner.invoke(main, ["hello", "--here"], catch_exceptions=False)
        finally:
            m.get_runtime = original
        assert result.exit_code != 0
        assert "CMUX_SURFACE_ID" in result.output
