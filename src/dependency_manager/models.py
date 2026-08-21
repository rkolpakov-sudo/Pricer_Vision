"""Data models for the dependency manager."""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Manager(str, Enum):
    PIP = "pip"
    NPM = "npm"


class Status(str, Enum):
    CHECKING = "checking"
    UPTODATE = "uptodate"
    UPDATE = "update"
    MISSING = "missing"
    DOWNGRADE = "downgrade"
    ERROR = "error"


@dataclass
class ReqLine:
    """One parsed line of a requirements file."""

    raw: str
    kind: str  # 'pkg' | 'comment' | 'blank' | 'other'
    name: str = ""
    extras: str = ""
    op: str = ""          # ==, >=, ~=, ... or "" if unversioned
    version: str = ""     # version fragment of the constraint
    trailing_comment: str = ""
    modified: bool = False


@dataclass
class Env:
    """A Python virtual environment discovered under the project root."""

    name: str
    python: Path


@dataclass
class Dependency:
    """A dependency shown in the UI table."""

    name: str
    manager: Manager
    source_file: str | None = None     # e.g. "requirements.txt"
    manifest_spec: str = ""            # original constraint text, e.g. "PySide6>=6.6.0"
    manifest_version: str = ""         # version fragment from the manifest constraint
    manifest_op: str = ""              # operator from the manifest constraint
    installed: str | None = None
    latest: str | None = None
    available: list = field(default_factory=list)
    selected: str | None = None        # user-chosen target version
    status: Status = Status.CHECKING
    error: str = ""

    @property
    def display_manifest(self) -> str:
        return self.manifest_spec or (self.name if self.manager == Manager.NPM else "")


@dataclass
class ApplyChange:
    """A single dependency the user wants to change."""

    dependency: Dependency
    target_version: str


@dataclass
class BrowserInfo:
    """Expected vs installed state of a browser used by one backend
    (playwright → chromium revision, camoufox → bundled Firefox, nodriver → system Chrome)."""

    name: str = "playwright"      # backend: 'playwright' | 'camoufox' | 'nodriver'
    label: str = ""               # display label, e.g. "Chromium (Playwright)"
    package_version: str = ""     # package the expectation is based on (MCP / camoufox / nodriver)
    expected_rev: str = ""        # revision/version required
    installed_rev: str = ""       # revision/version found on disk
    installed: bool = False       # browser present and usable
    error: str = ""
    details: dict = field(default_factory=dict)  # extra info (tooltip)

    @property
    def up_to_date(self) -> bool:
        return bool(self.installed and self.installed_rev and self.installed_rev == self.expected_rev)
