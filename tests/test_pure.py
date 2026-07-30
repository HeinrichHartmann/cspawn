"""Pure unit tests — no runtime, no filesystem side effects."""

import tempfile
from pathlib import Path

import pytest

from cspawn.main import _TOOL_RE, collect_profiles, parse_frontmatter


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("just body text")
        assert meta == {}
        assert body == "just body text"

    def test_valid_frontmatter(self):
        text = "---\nname: foo\ndescription: bar baz\n---\n\nbody here"
        meta, body = parse_frontmatter(text)
        assert meta["name"] == "foo"
        assert meta["description"] == "bar baz"
        assert body == "body here"

    def test_colon_in_value(self):
        text = "---\nallowed-tools: Edit, Bash(git:*)\n---\nbody"
        meta, body = parse_frontmatter(text)
        assert meta["allowed-tools"] == "Edit, Bash(git:*)"

    def test_missing_closing_fence(self):
        text = "---\nname: foo\nbody without closing fence"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_body(self):
        text = "---\nkey: val\n---\n"
        meta, body = parse_frontmatter(text)
        assert meta["key"] == "val"
        assert body == ""

    def test_strips_leading_newlines_from_body(self):
        text = "---\nkey: val\n---\n\n\nbody"
        _, body = parse_frontmatter(text)
        assert body == "body"


class TestCollectProfiles:
    def test_empty_tree(self, tmp_path):
        profiles = collect_profiles(tmp_path)
        assert profiles == []

    def test_finds_profile(self, tmp_path):
        profiles_dir = tmp_path / ".agents" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "worker.md").write_text("# worker")
        profiles = collect_profiles(tmp_path)
        assert [name for name, _ in profiles] == ["worker"]

    def test_closer_profile_wins(self, tmp_path):
        parent = tmp_path
        child = tmp_path / "subdir"
        child.mkdir()
        (parent / ".agents" / "profiles").mkdir(parents=True)
        (child / ".agents" / "profiles").mkdir(parents=True)
        (parent / ".agents" / "profiles" / "worker.md").write_text("parent")
        (child / ".agents" / "profiles" / "worker.md").write_text("child")
        profiles = collect_profiles(child)
        name, path = profiles[0]
        assert name == "worker"
        assert path.read_text() == "child"

    def test_collects_from_multiple_levels(self, tmp_path):
        parent = tmp_path
        child = tmp_path / "subdir"
        child.mkdir()
        (parent / ".agents" / "profiles").mkdir(parents=True)
        (child / ".agents" / "profiles").mkdir(parents=True)
        (parent / ".agents" / "profiles" / "architect.md").write_text("arch")
        (child / ".agents" / "profiles" / "worker.md").write_text("worker")
        profiles = collect_profiles(child)
        names = [name for name, _ in profiles]
        assert "worker" in names
        assert "architect" in names

    def test_sorted_by_name(self, tmp_path):
        profiles_dir = tmp_path / ".agents" / "profiles"
        profiles_dir.mkdir(parents=True)
        for name in ["zebra", "alpha", "mango"]:
            (profiles_dir / f"{name}.md").write_text(name)
        profiles = collect_profiles(tmp_path)
        assert [n for n, _ in profiles] == ["alpha", "mango", "zebra"]


class TestToolRe:
    @pytest.mark.parametrize("entry", [
        "Edit",
        "Write",
        "Bash",
        "Bash(git:*)",
        "Bash(git status:*)",
        "MyTool123",
    ])
    def test_valid(self, entry):
        assert _TOOL_RE.match(entry)

    @pytest.mark.parametrize("entry", [
        "",
        "123bad",
        "-bad",
        "bad entry",
    ])
    def test_invalid(self, entry):
        assert not _TOOL_RE.match(entry)
