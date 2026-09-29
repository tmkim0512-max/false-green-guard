"""Rules 1-3 applied to two directories: forbidden diff -> scope -> isolated rerun.

The fixer's own report is never an input here. Only the diff and a fresh pytest run.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .diffaudit import audit

IGNORE = shutil.ignore_patterns(".git", ".fgg", ".venv", "__pycache__", ".pytest_cache", "*.pyc")
IGNORED_DIRS = {".git", ".fgg", ".venv", "__pycache__", ".pytest_cache"}
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")


class JudgeUnavailable(Exception):
    """The judge could not reach a verdict. Callers must stop, never treat this as a pass."""


@dataclass(frozen=True)
class RunResult:
    counts: dict[str, int]      # PASS / FAIL / BLOCKED / NOT_RUN, sums to `collected`
    failing: tuple[str, ...]    # ids of FAIL + BLOCKED tests, sorted

    @property
    def collected(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        return f"{self.collected} collected: " + " ".join(f"{k}={self.counts[k]}" for k in STATUSES)


@dataclass(frozen=True)
class Judgement:
    verdict: str                # ACCEPT | REJECT
    stage: str                  # forbidden | scope | rerun
    reason: str
    changed: tuple[str, ...] = ()
    run: RunResult | None = None


def run_pytest(project: Path, timeout: int = 600) -> RunResult:
    """Run pytest in `project` and parse junit xml. Run it on a throwaway copy."""
    project = project.resolve()
    with tempfile.TemporaryDirectory(prefix="fgg-junit-") as tmp:
        xml = Path(tmp) / "junit.xml"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(project / "src"), str(project)]),
               "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
                cwd=project, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise JudgeUnavailable(f"pytest timed out after {timeout}s · fix: raise --timeout or find the hanging test")
        # 0 passed, 1 failed, 2 collection errors (listed in junit), 5 nothing collected.
        # 3 internal error / 4 usage error, or no junit file: there is no verdict to read.
        if proc.returncode not in (0, 1, 2, 5) or not xml.exists():
            tail = (proc.stdout + proc.stderr).strip()[-500:]
            raise JudgeUnavailable(f"pytest exit={proc.returncode} · fix: make pytest runnable in {project}\n{tail}")
        return parse_junit(xml.read_text(encoding="utf-8"))


def parse_junit(text: str) -> RunResult:
    root = ET.fromstring(text)
    counts = dict.fromkeys(STATUSES, 0)
    failing, declared = [], 0
    for suite in root.iter("testsuite"):
        declared += int(suite.get("tests", 0))
    for case in root.iter("testcase"):
        tid = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None:
            status = "FAIL"
        elif case.find("error") is not None:
            status = "BLOCKED"  # setup/teardown or collection error: never reached the assertion
        elif case.find("skipped") is not None:
            status = "NOT_RUN"
        else:
            status = "PASS"
        counts[status] += 1
        if status in ("FAIL", "BLOCKED"):
            failing.append(tid)
    if sum(counts.values()) != declared:
        raise JudgeUnavailable(f"junit says {declared} tests but lists {sum(counts.values())} · fix: check the pytest junit plugin")
    return RunResult(counts, tuple(sorted(failing)))


def _files(root: Path) -> set[str]:
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        out |= {(Path(dirpath) / f).relative_to(root).as_posix() for f in filenames if not f.endswith(".pyc")}
    return out


def _read(path: Path, rel: str) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        raise JudgeUnavailable(f"cannot diff non-UTF-8 file {rel} · fix: keep binary files out of the project tree")
    return [l if l.endswith("\n") else l + "\n" for l in lines]  # keep diff lines separable


def diff_dirs(before: Path, after: Path) -> tuple[str, list[str]]:
    """Unified diff of two trees (git-style a/ b/ paths) and the sorted list of changed files."""
    parts, changed = [], []
    for rel in sorted(_files(before) | _files(after)):
        b, a = before / rel, after / rel
        if b.exists() and a.exists() and b.read_bytes() == a.read_bytes():
            continue
        changed.append(rel)
        old = _read(b, rel) if b.exists() else []
        new = _read(a, rel) if a.exists() else []
        parts += difflib.unified_diff(old, new, f"a/{rel}" if b.exists() else "/dev/null",
                                      f"b/{rel}" if a.exists() else "/dev/null")
    return "".join(parts), changed


def judge(before: Path, after: Path, allow: tuple[str, ...], baseline: RunResult) -> Judgement:
    """Order is fixed: forbidden -> scope -> rerun. The first rejection wins."""
    diff, changed = diff_dirs(before, after)
    ch = tuple(changed)
    violations = audit(diff)
    if violations:
        return Judgement("REJECT", "forbidden", ",".join(sorted({v.rule for v in violations})), ch)
    off = [c for c in changed if not any(fnmatch.fnmatch(c, g) for g in allow)]
    if off:
        return Judgement("REJECT", "scope", "off_scope:" + ",".join(off[:5]), ch)
    with tempfile.TemporaryDirectory(prefix="fgg-rerun-") as tmp:
        copy = Path(tmp) / "p"
        shutil.copytree(after, copy, ignore=IGNORE)
        run = run_pytest(copy)
    bad = run.counts["FAIL"] + run.counts["BLOCKED"]
    if bad:
        return Judgement("REJECT", "rerun", f"still_failing:{bad}", ch, run)
    if run.collected < baseline.collected:
        return Judgement("REJECT", "rerun", f"collected_dropped:{baseline.collected}->{run.collected}", ch, run)
    if run.counts["NOT_RUN"] > baseline.counts["NOT_RUN"]:
        return Judgement("REJECT", "rerun", f"not_run_grew:{baseline.counts['NOT_RUN']}->{run.counts['NOT_RUN']}", ch, run)
    return Judgement("ACCEPT", "rerun", run.summary(), ch, run)


def baseline_of(project: Path) -> RunResult:
    with tempfile.TemporaryDirectory(prefix="fgg-base-") as tmp:
        copy = Path(tmp) / "p"
        shutil.copytree(project, copy, ignore=IGNORE)
        return run_pytest(copy)
