"""npm registry client plus detection of the version resolved by the npx cache."""
import json
import os
from pathlib import Path

import httpx

from .versioning import sort_versions

NPM_LATEST_URL = "https://registry.npmjs.org/{name}/latest"
NPM_FULL_URL = "https://registry.npmjs.org/{name}"

NPM_SCOPED = ("@playwright/mcp",)


class NpmClient:
    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(timeout=timeout)

    @staticmethod
    def _enc(name: str) -> str:
        return name.replace("/", "%2f") if name.startswith("@") else name

    def get_latest(self, name: str) -> str | None:
        try:
            r = self._client.get(NPM_LATEST_URL.format(name=self._enc(name)))
            if r.status_code != 200:
                return None
            return r.json().get("version")
        except httpx.HTTPError:
            return None

    def get_available(self, name: str) -> list[str]:
        try:
            r = self._client.get(NPM_FULL_URL.format(name=self._enc(name)))
            if r.status_code != 200:
                return []
            return sort_versions(list(r.json().get("versions", {}).keys()), style="semver")
        except httpx.HTTPError:
            return []

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


def npx_cache_paths() -> list[Path]:
    """Candidate root directories where npx keeps its per-package caches."""
    base = os.environ.get("LOCALAPPDATA")
    paths = []
    if base:
        paths.append(Path(base) / "npm-cache" / "_npx")
    npm_prefix = os.environ.get("APPDATA")
    if npm_prefix:
        paths.append(Path(npm_prefix) / "npm" / "node_modules")
    return [p for p in paths if p.exists()]


def detect_npx_package_version(name: str) -> str | None:
    """Find the newest version of `name` (e.g. '@playwright/mcp') present in the npx cache."""
    found: list[str] = []
    for root in npx_cache_paths():
        for pkg_json in root.glob(f"**/node_modules/{name}/package.json"):
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ver = data.get("version")
                if ver:
                    found.append(ver)
            except Exception:
                continue
    if not found:
        return None
    versions = sort_versions(found, style="semver")
    return versions[0]


# ── playwright browser revision support ─────────────────────────
def mcp_package_dir(version: str | None = None) -> Path | None:
    """Return the @playwright/mcp package directory for `version` (or the newest cached)."""
    matches: list[tuple[str, Path]] = []
    for root in npx_cache_paths():
        for pkg_json in root.glob("**/node_modules/@playwright/mcp/package.json"):
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            ver = data.get("version")
            if ver:
                matches.append((ver, pkg_json.parent))
    if not matches:
        return None
    if version:
        for ver, path in matches:
            if ver == version:
                return path
        return None
    top = sort_versions([v for v, _ in matches], style="semver")
    if not top:
        return None
    for ver, path in matches:
        if ver == top[0]:
            return path
    return None


def expected_browser_revisions(package_dir: Path) -> dict[str, str]:
    """name -> revision, read from the playwright-core/browsers.json of `package_dir`."""
    node_modules = package_dir.parent.parent
    for candidate in (
        node_modules / "playwright-core" / "browsers.json",
        node_modules / "@playwright" / "playwright-core" / "browsers.json",
    ):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return {}
        out = {}
        for b in data.get("browsers", []):
            if isinstance(b, dict) and b.get("name") and b.get("revision"):
                out[b["name"]] = str(b["revision"])
        return out
    return {}


def browsers_root() -> Path:
    """Directory where playwright keeps installed browsers."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ms-playwright"
    return Path.home() / "AppData" / "Local" / "ms-playwright"


def installed_browser_revisions(root: Path, names: list[str]) -> dict[str, tuple[str, bool]]:
    """name -> (revision, INSTALLATION_COMPLETE), from folder names like chromium-1237."""
    out: dict[str, tuple[str, bool]] = {}
    if not root.exists():
        return out
    try:
        entries = list(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir():
            continue
        for name in names:
            if entry.name.startswith(name + "-"):
                rev = entry.name[len(name) + 1:]
                out[name] = (rev, (entry / "INSTALLATION_COMPLETE").exists())
    return out
