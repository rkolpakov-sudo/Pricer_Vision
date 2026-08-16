"""Parse and rewrite requirements.txt while preserving comments, blank lines and ordering."""
import re
from pathlib import Path

from .models import ReqLine

# name[(extras)]  op  version  # comment
_PKG_RE = re.compile(
    r"^\s*"
    r"([A-Za-z0-9][A-Za-z0-9._\-]*)"
    r"(\[[^\]\s]+\])?"
    r"\s*"
    r"(==|>=|<=|!=|~=|===|>|<)?"
    r"\s*"
    r"([^#\s]+)?"
    r"\s*(#.*)?$"
)


def _split_comment(line: str) -> tuple:
    """Split a line into (code, comment). Respects URLs with '#' rare; keep simple."""
    idx = line.find("#")
    if idx == -1:
        return line.rstrip(), ""
    return line[:idx].rstrip(), line[idx:]


def parse_line(line: str) -> ReqLine:
    stripped = line.strip()
    if not stripped:
        return ReqLine(raw=line, kind="blank")
    if stripped.startswith("#"):
        return ReqLine(raw=line, kind="comment")

    # Non-package directives (options, includes, editable, VCS links, paths)
    if (
        stripped.startswith(("-r ", "--", "-e ", "-c ", "-f ", "-i "))
        or "://" in stripped
        or "git+" in stripped
        or stripped.startswith(".\\")
        or stripped.startswith("./")
        or stripped.endswith((".whl", ".tar.gz", ".zip"))
    ):
        return ReqLine(raw=line, kind="other")

    code, comment = _split_comment(line)
    m = _PKG_RE.match(code)
    if not m:
        return ReqLine(raw=line, kind="other")

    op = m.group(3) or ""
    ver = m.group(4) or ""
    return ReqLine(
        raw=line,
        kind="pkg",
        name=m.group(1),
        extras=m.group(2) or "",
        op=op,
        version=ver,
        trailing_comment=comment,
    )


def render_pkg(name: str, extras: str, op: str, version: str, comment: str) -> str:
    spec = ""
    if version:
        spec = f"{op}{version}" if op else version
    line = f"{name}{extras}{spec}"
    if comment:
        line += f" {comment}"
    return line.rstrip() + "\n"


def render_line(line: ReqLine, new_version: str | None = None) -> str:
    """Re-render a parsed line, optionally replacing the version fragment.

    Unchanged lines are returned verbatim (byte-for-byte round trip). Lines
    flagged as `modified` (or a requested `new_version`) are rendered in a
    canonical compact form.
    """
    if line.kind == "pkg":
        if new_version is not None:
            op = "==" if line.op in ("", "~=", "!=") else line.op
            return render_pkg(line.name, line.extras, op, new_version, line.trailing_comment)
        if line.modified:
            return render_pkg(line.name, line.extras, line.op, line.version, line.trailing_comment)
        return line.raw if line.raw.endswith("\n") else line.raw + "\n"
    return line.raw if line.raw.endswith("\n") else line.raw + "\n"


def parse_requirements(path: Path) -> list[ReqLine]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return [parse_line(line) for line in f]


def write_requirements(path: Path, lines: list[ReqLine]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(render_line(line) for line in lines)
