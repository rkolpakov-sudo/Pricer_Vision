"""Virtual-environment discovery and installed-package listing."""
import json
import subprocess
from pathlib import Path

from .models import Env


def find_environments(project_root: Path) -> list[Env]:
    envs = []
    for name in ("venv", "mineru_venv"):
        root = project_root / name
        python = root / "Scripts" / "python.exe"
        if root.is_dir() and python.is_file():
            envs.append(Env(name=name, python=python))
    return envs


def list_installed(python: Path, timeout: int = 90) -> dict[str, str]:
    """Return {package_name_lower: version} for a virtual environment."""
    cmd = [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {item.get("name", "").lower(): item.get("version", "") for item in data if item.get("name")}


def venv_metadata(project_root: Path) -> dict:
    """Version info about the Python interpreter(s) — used for display/logging."""
    out = {}
    for env in find_environments(project_root):
        try:
            proc = subprocess.run(
                [str(env.python), "-c", "import platform;print(platform.python_version())"],
                capture_output=True, text=True, timeout=30,
            )
            out[env.name] = proc.stdout.strip() if proc.returncode == 0 else "?"
        except (subprocess.SubprocessError, OSError):
            out[env.name] = "?"
    return out
