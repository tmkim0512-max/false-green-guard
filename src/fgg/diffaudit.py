"""Rule 1: find test-neutralizing changes in a unified diff. Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

CONFIG_FILES = {"conftest.py", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
_MARK = r"pytest\.mark\.(skip|skipif|xfail)\b"
# Code positions only (decorator, statement, pytestmark, param marks=), so strings and
# comments that merely mention a skip are not flagged. The rerun still catches other forms.
SKIP_RE = re.compile(rf"^\s*(@{_MARK}|pytest\.(skip|xfail)\(|pytestmark\b.*{_MARK})|\bmarks\s*=.*{_MARK}")
ASSERT_RE = re.compile(r"^\s*assert\b")
TRIVIAL_ASSERT_RE = re.compile(r"^\s*assert\s+(True|1|not\s+False)\s*(#.*)?$")
TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")
NARROWING = ("collect_ignore", "norecursedirs", "testpaths", "--ignore", "--deselect", "-p no:")
HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


@dataclass
class FileChange:
    path: str
    deleted: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: str

    def __str__(self) -> str:
        return f"{self.rule:<22} {self.path}  {self.line.strip()}"


def _strip_prefix(p: str) -> str:
    p = p.split("\t")[0].strip()
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse(diff: str) -> list[FileChange]:
    """Split a unified diff (plain or git format) into per-file added/removed lines."""
    files: list[FileChange] = []
    cur: FileChange | None = None
    old_left = new_left = 0  # lines still expected in the current hunk
    old_path = ""
    for line in diff.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                cur.added.append(line[1:]); new_left -= 1
            elif line.startswith("-"):
                cur.removed.append(line[1:]); old_left -= 1
            elif line.startswith("\\"):
                pass  # "\ No newline at end of file"
            else:
                old_left -= 1; new_left -= 1
            continue
        if line.startswith("--- "):
            old_path = _strip_prefix(line[4:])
        elif line.startswith("+++ "):
            new_path = _strip_prefix(line[4:])
            deleted = new_path == "/dev/null"
            cur = FileChange(old_path if deleted else new_path, deleted=deleted)
            files.append(cur)
        elif (m := HUNK_RE.match(line)) and cur is not None:
            old_left = int(m.group(1) or 1)
            new_left = int(m.group(2) or 1)
    return files


def is_config(path: str) -> bool:
    return PurePosixPath(path).name in CONFIG_FILES


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return "tests" in p.parts[:-1] or p.name.startswith("test_") or p.name.endswith("_test.py")


def audit(diff: str) -> list[Violation]:
    """Return every violation of rule 1. Production code is never inspected."""
    out: list[Violation] = []
    for f in parse(diff):
        test, config = is_test(f.path), is_config(f.path)
        if not (test or config):
            continue
        out += [Violation("skip_or_xfail_added", f.path, l) for l in f.added if SKIP_RE.search(l)]
        if f.deleted and test:
            out.append(Violation("test_deleted", f.path, "(file deleted)"))
        else:
            added_defs = {m.group(1) for l in f.added if (m := TEST_DEF_RE.match(l))}
            out += [Violation("test_deleted", f.path, l) for l in f.removed
                    if (m := TEST_DEF_RE.match(l)) and m.group(1) not in added_defs]
        if not f.deleted:
            n_rm = sum(bool(ASSERT_RE.match(l)) for l in f.removed)
            n_add = sum(bool(ASSERT_RE.match(l)) for l in f.added)
            if n_rm > n_add:
                out.append(Violation("assert_weakened", f.path, f"{n_rm} assert removed, {n_add} added"))
            out += [Violation("assert_weakened", f.path, l) for l in f.added if TRIVIAL_ASSERT_RE.match(l)]
        if config:
            out += [Violation("collection_narrowed", f.path, l) for l in f.added
                    if any(tok in l for tok in NARROWING)]
    return out
