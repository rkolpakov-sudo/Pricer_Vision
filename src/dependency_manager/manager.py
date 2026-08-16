"""Orchestration: load manifest, check versions, apply changes, rollback.

Kept Qt-free so it can be unit-tested without a QApplication.
"""
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..config_loader import get_deps_config, save_deps_config
from . import versioning
from .envs import find_environments, list_installed
from .models import ApplyChange, BrowserInfo, Dependency, Env, Manager, ReqLine, Status
from .npm import (
    NpmClient,
    browsers_root,
    detect_npx_package_version,
    expected_browser_revisions,
    installed_browser_revisions,
    mcp_package_dir,
)
from .pypi import PyPIClient
from .requirements import parse_requirements, write_requirements

logger = logging.getLogger("pricer.deps")

PLAYWRIGHT_MCP = "@playwright/mcp"
BACKUP_SUFFIX = ".depsbak"


class DependencyManager:
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.requirements_path = self.root / "requirements.txt"
        self._pypi = PyPIClient()
        self._npm = NpmClient()

    # ── discovery ────────────────────────────────────────────────
    def environments(self) -> list[Env]:
        return find_environments(self.root)

    def load_manifest(self) -> list[Dependency]:
        deps: list[Dependency] = []
        if self.requirements_path.exists():
            lines = parse_requirements(self.requirements_path)
            for line in lines:
                if line.kind != "pkg":
                    continue
                spec = f"{line.name}{line.extras}"
                if line.version:
                    spec += f"{line.op}{line.version}"
                deps.append(Dependency(
                    name=line.name,
                    manager=Manager.PIP,
                    source_file="requirements.txt",
                    manifest_spec=spec,
                    manifest_version=line.version,
                    manifest_op=line.op,
                ))
        # system npm dependency
        deps.append(self._playwright_dep())
        return deps

    def _playwright_dep(self) -> Dependency:
        cfg = get_deps_config()
        pin = (cfg.get("playwright_mcp") or {}).get("version") or None
        spec = f"{PLAYWRIGHT_MCP}@{pin}" if pin else PLAYWRIGHT_MCP
        return Dependency(
            name=PLAYWRIGHT_MCP,
            manager=Manager.NPM,
            source_file="config/settings.yaml",
            manifest_spec=spec,
            manifest_version=pin or "",
            manifest_op="@" if pin else "",
            installed=pin or detect_npx_package_version(PLAYWRIGHT_MCP),
        )

    def close(self):
        self._pypi.close()
        self._npm.close()

    # ── check ────────────────────────────────────────────────────
    def check(self, deps: list[Dependency], env: Env, on_progress=None) -> list[Dependency]:
        installed = list_installed(env.python)
        total = len(deps)
        for i, dep in enumerate(deps, start=1):
            try:
                self._check_one(dep, installed)
            except Exception as e:  # noqa: BLE001
                logger.exception("check failed for %s", dep.name)
                dep.status = Status.ERROR
                dep.error = str(e)
            if on_progress:
                on_progress(i, total, f"Проверка {dep.name} ({i}/{total})")
        return deps

    def _check_one(self, dep: Dependency, installed: dict[str, str]):
        dep.status = Status.CHECKING
        dep.error = ""
        if dep.manager == Manager.PIP:
            dep.installed = installed.get(dep.name.lower())
            dep.latest = self._pypi.get_latest(dep.name)
            dep.available = self._pypi.get_available(dep.name)[:40]
        else:
            if not dep.installed:
                dep.installed = detect_npx_package_version(dep.name)
            dep.latest = self._npm.get_latest(dep.name)
            dep.available = self._npm.get_available(dep.name)[:40]
        dep.status = self._status(dep)

    @staticmethod
    def _status(dep: Dependency) -> Status:
        if not dep.latest:
            return Status.ERROR if not dep.installed else Status.UPTODATE
        if not dep.installed:
            return Status.MISSING
        style = "semver" if dep.manager == Manager.NPM else "pep440"
        key = versioning.semver_key if style == "semver" else versioning.pep440_key
        ki, kl = key(dep.installed), key(dep.latest)
        if ki is None or kl is None:
            return Status.UPTODATE if dep.installed == dep.latest else Status.UPDATE
        if ki == kl:
            return Status.UPTODATE
        if ki < kl:
            return Status.UPDATE
        return Status.DOWNGRADE

    # ── playwright browser ───────────────────────────────────────
    def _active_mcp_version(self) -> str | None:
        cfg = get_deps_config()
        return (cfg.get("playwright_mcp") or {}).get("version") or None

    def browser_status(self) -> BrowserInfo:
        """Expected vs installed chromium revision for the active @playwright/mcp."""
        pin = self._active_mcp_version()
        ref_dir = mcp_package_dir(pin) if pin else mcp_package_dir()
        if ref_dir is None:
            return BrowserInfo(
                package_version=pin or "",
                error=f"пакет {PLAYWRIGHT_MCP} не найден в кэше npx",
            )
        try:
            pkg_ver = json.loads((ref_dir / "package.json").read_text(encoding="utf-8")).get("version", "")
        except Exception:  # noqa: BLE001
            pkg_ver = pin or ""

        info = BrowserInfo(package_version=pkg_ver)
        revs = expected_browser_revisions(ref_dir)
        if not revs:
            info.error = "browsers.json не найден для playwright-core"
            return info

        installed = installed_browser_revisions(browsers_root(), list(revs))
        info.expected_rev = revs.get("chromium", "")
        if "chromium" in installed:
            rev, complete = installed["chromium"]
            info.installed_rev = rev
            info.installed = complete
        for name, rev in sorted(revs.items()):
            if name == "chromium":
                continue
            state = "установлен" if installed.get(name) and installed[name][1] else "не установлен"
            info.details[f"{name} (ожидается r{rev})"] = state
        return info

    def update_browser(self, on_log=None, on_progress=None) -> tuple[bool, str]:
        """Run `playwright install chromium` for the active @playwright/mcp version."""
        log = on_log or (lambda m: None)
        npx = self._find_npx()
        pin = self._active_mcp_version()
        pkg_spec = f"{PLAYWRIGHT_MCP}@{pin}" if pin else PLAYWRIGHT_MCP

        log(f"Установка браузера Chromium: npx -y {pkg_spec} install-browser chromium")
        if on_progress:
            on_progress(1, 1, "Установка браузера Chromium…")
        ok = self._run_npx(npx, [pkg_spec, "install-browser", "chromium"], log, timeout=900)
        if ok is None:
            return False, "npx не запустился"
        if ok:
            return True, "Chromium установлен и соответствует ожидаемой ревизии"
        return False, "install-browser завершился с ошибкой — см. лог"

    # ── apply ────────────────────────────────────────────────────
    def backup(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.requirements_path.with_suffix(f".txt{BACKUP_SUFFIX}.{ts}")
        shutil.copy2(self.requirements_path, backup)
        return backup

    def apply(self, changes: list[ApplyChange], env: Env,
              on_log=None, on_progress=None, install_browser: bool = False) -> dict:
        """Apply selected changes. Returns a result dict with 'backup', 'installed', 'summary'."""
        log = on_log or (lambda m: None)
        progress = on_progress or (lambda d, t, m: None)

        pip_changes = [c for c in changes if c.dependency.manager == Manager.PIP]
        npm_changes = [c for c in changes if c.dependency.manager == Manager.NPM]

        backup: Path | None = None
        try:
            if pip_changes:
                backup = self.backup()
                log(f"Бэкап: {backup.name}")

                lines = parse_requirements(self.requirements_path)
                by_name = {}
                for line in lines:
                    if line.kind == "pkg":
                        by_name.setdefault(line.name.lower(), line)
                for ch in pip_changes:
                    target = by_name.get(ch.dependency.name.lower())
                    if target is not None:
                        new_line = self._render_with_version(target, ch.target_version)
                        lines[lines.index(target)] = new_line
                        log(f"requirements.txt: {target.name} -> {ch.target_version}")
                write_requirements(self.requirements_path, lines)

                progress(1, 2, f"Установка {len(pip_changes)} пакетов в {env.name}...")
                self._pip_install([(c.dependency.name, c.target_version) for c in pip_changes], env, log)

            if npm_changes:
                progress(1, 2, "Обновление @playwright/mcp...")
                for ch in npm_changes:
                    self._apply_npm(ch.dependency, ch.target_version, log, install_browser)

            progress(2, 2, "Готово")
            return {
                "backup": backup,
                "installed": [c.dependency.name for c in changes],
                "summary": f"Обновлено: {', '.join(c.dependency.name for c in changes)}",
            }
        except Exception as e:  # noqa: BLE001
            if backup is not None and backup.exists():
                shutil.copy2(backup, self.requirements_path)
                log(f"Откат requirements.txt из {backup.name}")
            raise RuntimeError(f"Ошибка применения обновления: {e}") from e

    @staticmethod
    def _render_with_version(line: ReqLine, version: str) -> ReqLine:
        if line.op == "~=" or line.op == "!=" or not line.op:
            line.op = "=="
        line.version = version
        line.modified = True
        return line

    def _pip_install(self, specs, env: Env, log) -> None:
        cmd = [str(env.python), "-m", "pip", "install", "--disable-pip-version-check"]
        cmd += [f"{name}=={ver}" for name, ver in specs]
        log(f"$ {env.name}: {' '.join(cmd[len(str(env.python)):])}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        for ln in (proc.stdout or "").splitlines():
            if ln.strip():
                log(ln)
        if proc.stderr:
            for ln in (proc.stderr or "").splitlines():
                if ln.strip():
                    log(f"[stderr] {ln}")
        if proc.returncode != 0:
            raise RuntimeError(f"pip install завершился с кодом {proc.returncode}")

    def _apply_npm(self, dep: Dependency, version: str, log, install_browser: bool) -> None:
        log(f"Пин @playwright/mcp@{version} в config/settings.yaml")
        cfg = get_deps_config()
        cfg.setdefault("playwright_mcp", {})["version"] = version
        save_deps_config(cfg)

        npx = self._find_npx()
        pkg_spec = f"{PLAYWRIGHT_MCP}@{version}"

        log(f"Загрузка {pkg_spec} в кэш npx...")
        cache_ok = self._run_npx(npx, [pkg_spec, "--version"], log, timeout=240)
        if cache_ok is None:
            log("[warn] Не удалось прогреть кэш npx — пакет будет загружен при следующем запуске")

        if install_browser:
            log("Установка браузера Chromium для новой версии...")
            self._run_npx(npx, [pkg_spec, "install-browser", "chromium"], log, timeout=900)
        dep.installed = version

    def _find_npx(self):
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if not npx:
            raise RuntimeError("npx не найден в PATH — обновление @playwright/mcp невозможно")
        return npx

    @staticmethod
    def _run_npx(npx, args: list, log, timeout: int) -> bool | None:
        """Return True on success, False on failure, None if the executable could not start."""
        try:
            proc = subprocess.run(
                [npx, "-y", *args],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log(f"[warn] Таймаут ({timeout}s) для npx {args[0]}")
            return False
        except OSError as e:
            log(f"[warn] npx не запустился: {e}")
            return None
        out = (proc.stdout or "") + (proc.stderr or "")
        for ln in out.splitlines():
            if ln.strip():
                log(ln)
        return proc.returncode == 0

    def rollback(self, backup: Path) -> None:
        if backup.exists():
            shutil.copy2(backup, self.requirements_path)
            backup.unlink(missing_ok=True)
            return True
        return False
