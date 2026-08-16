"""Unit tests for src.dependency_manager (Qt-free components)."""
import json
from pathlib import Path

import pytest

from src.dependency_manager import versioning
from src.dependency_manager.models import BrowserInfo, Dependency, Manager, Status
from src.dependency_manager.manager import DependencyManager
from src.dependency_manager.npm import (
    browsers_root,
    expected_browser_revisions,
    installed_browser_revisions,
    mcp_package_dir,
)
from src.dependency_manager.requirements import (
    parse_line,
    parse_requirements,
    render_line,
    write_requirements,
)


# ── versioning ───────────────────────────────────────────────────
class TestVersioning:
    def test_pep440_sort_latest_first(self):
        versions = ["1.2.0", "1.1.9", "2.0.0", "1.2.0rc1", "1.2.0.post1"]
        got = versioning.sort_versions(versions, style="pep440")
        assert got[0] == "2.0.0"
        assert got[-1] == "1.1.9"

    def test_semver_prerelease_after_release(self):
        versions = ["1.0.0-beta.1", "1.0.0", "0.9.9", "1.0.0-alpha"]
        got = versioning.sort_versions(versions, style="semver")
        assert got[0] == "1.0.0"
        assert got[1] == "1.0.0-beta.1"
        assert got[2] == "1.0.0-alpha"
        assert got[3] == "0.9.9"

    def test_semver_build_metadata_ignored(self):
        assert versioning.semver_key("1.2.3+build5") == versioning.semver_key("1.2.3")

    def test_semver_bad_version_sorts_last(self):
        got = versioning.sort_versions(["not-a-version", "1.0.0"], style="semver")
        assert got == ["1.0.0", "not-a-version"]


# ── requirements parsing ─────────────────────────────────────────
class TestRequirements:
    def test_parse_pkg_with_operator(self):
        line = parse_line("PySide6>=6.6.0\n")
        assert line.kind == "pkg"
        assert line.name == "PySide6"
        assert line.op == ">="
        assert line.version == "6.6.0"

    def test_parse_pkg_exact_and_extras(self):
        line = parse_line("httpx[all]==0.27.0  # http\n")
        assert line.kind == "pkg"
        assert line.extras == "[all]"
        assert line.op == "=="
        assert line.version == "0.27.0"
        assert "http" in line.trailing_comment

    def test_parse_unversioned(self):
        line = parse_line("networkx\n")
        assert line.kind == "pkg"
        assert line.op == ""
        assert line.version == ""

    def test_comments_and_blanks_preserved(self):
        for raw in ("\n", "# top comment\n", "  # indented\n"):
            line = parse_line(raw)
            assert line.kind in ("blank", "comment")

    def test_other_directives_kept(self):
        for raw in ("-r base.txt\n", "-e .\n", "git+https://github.com/x/y#egg=z\n", "--index-url https://pypi.org\n"):
            assert parse_line(raw).kind == "other"

    def test_render_replaces_version_keeps_operator(self):
        line = parse_line("PySide6>=6.6.0\n")
        out = render_line(line, new_version="6.11.0")
        assert out == "PySide6>=6.11.0\n"

    def test_render_pins_unversioned(self):
        line = parse_line("networkx\n")
        out = render_line(line, new_version="3.6.1")
        assert out == "networkx==3.6.1\n"

    def test_render_pins_compatible_operator(self):
        line = parse_line("openpyxl~=3.1\n")
        out = render_line(line, new_version="3.1.5")
        assert out == "openpyxl==3.1.5\n"

    def test_render_keeps_comment(self):
        line = parse_line("httpx>=0.27.0  # http client\n")
        out = render_line(line, new_version="0.28.1")
        assert out == "httpx>=0.28.1 # http client\n"

    def test_roundtrip_file(self, tmp_path):
        content = (
            "# core\n"
            "PySide6>=6.6.0\n"
            "httpx>=0.27.0\n"
            "\n"
            "# tools\n"
            "pytest>=8.0.0\n"
        )
        path = tmp_path / "requirements.txt"
        path.write_text(content, encoding="utf-8")
        lines = parse_requirements(path)
        write_requirements(path, lines)
        assert path.read_text(encoding="utf-8") == content

    def test_write_with_updates_preserves_order(self, tmp_path):
        content = "PySide6>=6.6.0\nhttpx>=0.27.0\n# end\n"
        path = tmp_path / "requirements.txt"
        path.write_text(content, encoding="utf-8")
        lines = parse_requirements(path)
        for line in lines:
            if line.name == "PySide6":
                out = render_line(line, new_version="6.11.0")
                lines[lines.index(line)] = parse_line(out)
        write_requirements(path, lines)
        assert path.read_text(encoding="utf-8") == "PySide6>=6.11.0\nhttpx>=0.27.0\n# end\n"


# ── status logic ─────────────────────────────────────────────────
class TestStatus:
    @staticmethod
    def dep(installed=None, latest=None, manager=Manager.PIP):
        d = Dependency(name="demo", manager=manager)
        d.installed = installed
        d.latest = latest
        return d

    def test_uptodate(self):
        assert DependencyManager._status(self.dep("1.2.3", "1.2.3")) == Status.UPTODATE

    def test_update(self):
        assert DependencyManager._status(self.dep("1.2.3", "2.0.0")) == Status.UPDATE

    def test_downgrade(self):
        assert DependencyManager._status(self.dep("2.5.0", "2.0.0")) == Status.DOWNGRADE

    def test_missing(self):
        assert DependencyManager._status(self.dep(None, "2.0.0")) == Status.MISSING

    def test_error_when_no_latest_and_not_installed(self):
        assert DependencyManager._status(self.dep(None, None)) == Status.ERROR

    def test_semver_compare_for_npm(self):
        d = self.dep("1.0.0-beta.1", "1.0.0", manager=Manager.NPM)
        assert DependencyManager._status(d) == Status.UPDATE

    def test_semver_equal(self):
        d = self.dep("0.0.79", "0.0.79", manager=Manager.NPM)
        assert DependencyManager._status(d) == Status.UPTODATE


# ── manager environment discovery ────────────────────────────────
class TestEnvironments:
    def test_find_environments_picks_existing(self, tmp_path):
        (tmp_path / "venv" / "Scripts").mkdir(parents=True)
        (tmp_path / "venv" / "Scripts" / "python.exe").write_text("")
        envs = DependencyManager(tmp_path).environments()
        assert [e.name for e in envs] == ["venv"]

    def test_load_manifest_parses_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(
            "PySide6>=6.6.0\nhttpx>=0.27.0\n# comment\n", encoding="utf-8"
        )
        mgr = DependencyManager(tmp_path)
        mgr.close()
        deps = mgr.load_manifest()
        names = {d.name for d in deps}
        assert "PySide6" in names
        assert "httpx" in names
        assert any(d.manager == Manager.NPM for d in deps)  # @playwright/mcp system dep


# ── playwright browser revision support ─────────────────────────
def _make_package(npx_root: Path, version: str) -> Path:
    pkg_dir = npx_root / f"cache-{version}" / "node_modules" / "@playwright" / "mcp"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "@playwright/mcp", "version": version}), encoding="utf-8"
    )
    return pkg_dir


def _make_browsers_json(pkg_dir: Path, revisions: dict[str, str]):
    node_modules = pkg_dir.parent.parent
    (node_modules / "playwright-core").mkdir(parents=True, exist_ok=True)
    (node_modules / "playwright-core" / "browsers.json").write_text(
        json.dumps({"comment": "", "browsers": [
            {"name": name, "revision": rev} for name, rev in revisions.items()
        ]}),
        encoding="utf-8",
    )


class TestBrowserRevisions:
    def test_mcp_package_dir_newest(self, tmp_path):
        for v in ("0.0.70", "0.0.79"):
            _make_package(tmp_path, v)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.npm.npx_cache_paths", lambda: [tmp_path])
            assert mcp_package_dir().name == "mcp"
            assert json.loads((mcp_package_dir() / "package.json").read_text())["version"] == "0.0.79"

    def test_mcp_package_dir_filter_by_version(self, tmp_path):
        _make_package(tmp_path, "0.0.75")
        _make_package(tmp_path, "0.0.79")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.npm.npx_cache_paths", lambda: [tmp_path])
            assert json.loads((mcp_package_dir("0.0.75") / "package.json").read_text())["version"] == "0.0.75"
            assert mcp_package_dir("9.9.9") is None

    def test_expected_browser_revisions(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.79")
        _make_browsers_json(pkg, {"chromium": "1237", "ffmpeg": "1011"})
        revs = expected_browser_revisions(pkg)
        assert revs == {"chromium": "1237", "ffmpeg": "1011"}

    def test_expected_browser_revisions_missing(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.79")
        assert expected_browser_revisions(pkg) == {}

    def test_installed_browser_revisions(self, tmp_path):
        for name, rev in (("chromium", "1223"), ("chromium_headless_shell", "1223")):
            d = tmp_path / f"{name}-{rev}"
            d.mkdir()
            (d / "INSTALLATION_COMPLETE").write_text("")
        (tmp_path / "ffmpeg-1011").mkdir()
        got = installed_browser_revisions(tmp_path, ["chromium", "chromium_headless_shell"])
        assert got["chromium"] == ("1223", True)
        assert got["chromium_headless_shell"] == ("1223", True)
        assert "ffmpeg" not in got

    def test_browsers_root_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
        assert browsers_root() == tmp_path

    def test_browser_status_uptodate(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.79")
        _make_browsers_json(pkg, {"chromium": "1237", "ffmpeg": "1011"})
        (tmp_path / "chromium-1237").mkdir()
        (tmp_path / "chromium-1237" / "INSTALLATION_COMPLETE").write_text("")
        (tmp_path / "ffmpeg-1011").mkdir()
        mgr = DependencyManager(tmp_path)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.manager.mcp_package_dir", lambda v=None: pkg)
            mp.setattr("src.dependency_manager.manager.browsers_root", lambda: tmp_path)
            info = mgr.browser_status()
        assert info.up_to_date
        assert info.package_version == "0.0.79"
        assert any("ffmpeg" in k for k in info.details)
        mgr.close()

    def test_browser_status_outdated(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.79")
        _make_browsers_json(pkg, {"chromium": "1237"})
        (tmp_path / "chromium-1223").mkdir()
        (tmp_path / "chromium-1223" / "INSTALLATION_COMPLETE").write_text("")
        mgr = DependencyManager(tmp_path)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.manager.mcp_package_dir", lambda v=None: pkg)
            mp.setattr("src.dependency_manager.manager.browsers_root", lambda: tmp_path)
            info = mgr.browser_status()
        assert not info.up_to_date
        assert info.installed_rev == "1223"
        assert info.expected_rev == "1237"
        mgr.close()

    def test_browser_status_not_installed(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.79")
        _make_browsers_json(pkg, {"chromium": "1237"})
        mgr = DependencyManager(tmp_path)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.manager.mcp_package_dir", lambda v=None: pkg)
            mp.setattr("src.dependency_manager.manager.browsers_root", lambda: tmp_path)
            info = mgr.browser_status()
        assert info.expected_rev == "1237"
        assert info.installed_rev == ""
        assert not info.installed
        assert not info.up_to_date
        mgr.close()

    def test_browser_status_no_package(self, tmp_path):
        mgr = DependencyManager(tmp_path)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.manager.mcp_package_dir", lambda v=None: None)
            info = mgr.browser_status()
        assert info.error
        assert not info.up_to_date
        mgr.close()

    def test_browser_status_respects_pin(self, tmp_path):
        pkg = _make_package(tmp_path, "0.0.75")
        _make_browsers_json(pkg, {"chromium": "1200"})
        (tmp_path / "chromium-1200").mkdir()
        (tmp_path / "chromium-1200" / "INSTALLATION_COMPLETE").write_text("")
        mgr = DependencyManager(tmp_path)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("src.dependency_manager.manager.get_deps_config",
                       lambda: {"playwright_mcp": {"version": "0.0.75"}})
            mp.setattr("src.dependency_manager.manager.mcp_package_dir",
                       lambda v=None: pkg if v == "0.0.75" else None)
            mp.setattr("src.dependency_manager.manager.browsers_root", lambda: tmp_path)
            info = mgr.browser_status()
        assert info.package_version == "0.0.75"
        assert info.up_to_date
        mgr.close()

    def test_update_browser_uses_pin(self, tmp_path, monkeypatch):
        mgr = DependencyManager(tmp_path)
        seen = {}
        monkeypatch.setattr("src.dependency_manager.manager.get_deps_config",
                            lambda: {"playwright_mcp": {"version": "0.0.75"}})
        monkeypatch.setattr("src.dependency_manager.manager.DependencyManager._find_npx",
                            lambda self: "npx.cmd")

        def fake_run(npx, args, log, timeout):
            seen["args"] = args
            return True

        monkeypatch.setattr(mgr, "_run_npx", fake_run)
        ok, msg = mgr.update_browser()
        assert ok
        assert seen["args"][0] == "@playwright/mcp@0.0.75"
        assert seen["args"][1] == "install-browser"
        assert seen["args"][2] == "chromium"
        mgr.close()

    def test_update_browser_unpinned(self, tmp_path, monkeypatch):
        mgr = DependencyManager(tmp_path)
        seen = {}
        monkeypatch.setattr("src.dependency_manager.manager.get_deps_config", lambda: {})
        monkeypatch.setattr("src.dependency_manager.manager.DependencyManager._find_npx",
                            lambda self: "npx.cmd")

        def fake_run(npx, args, log, timeout):
            seen["args"] = args
            return True

        monkeypatch.setattr(mgr, "_run_npx", fake_run)
        ok, msg = mgr.update_browser()
        assert ok
        assert seen["args"][0] == "@playwright/mcp"
        mgr.close()
