"""Version parsing and comparison helpers for both PEP440 (PyPI) and semver (npm)."""
import re
from functools import cmp_to_key

from packaging.version import InvalidVersion, Version


def pep440_key(value: str):
    try:
        return Version(value)
    except InvalidVersion:
        return None


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$")


def semver_key(value: str):
    """Return a sortable tuple for an npm semver string.

    Pre-releases sort before the corresponding release. Unparseable values get
    the lowest key so they land at the end of a descending (latest-first) list.
    """
    if not isinstance(value, str):
        return (0,)
    m = _SEMVER_RE.match(value.strip())
    if not m:
        return (0,)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4) or ""
    if not pre:
        # release: second field 1 sorts after pre-releases (field 0)
        return (major, minor, patch, 1, "")
    return (major, minor, patch, 0, pre)


def sort_versions(versions, style: str = "pep440") -> list:
    """Sort version strings. `style` is 'pep440' or 'semver'. Descending (latest first)."""
    if style == "semver":
        key = semver_key
    else:
        key = pep440_key

    def cmp(a, b):
        ka, kb = key(a), key(b)
        if ka is None and kb is None:
            return 0
        if ka is None:
            return -1
        if kb is None:
            return 1
        return (ka > kb) - (ka < kb)

    return sorted(versions, key=cmp_to_key(cmp), reverse=True)
