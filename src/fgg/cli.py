"""fgg audit | judge | run. Exit codes: audit/judge 0 pass, 1 violation/reject, 3 no verdict; run 0 DONE, 2 HALT."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import loop
from .diffaudit import audit
from .judge import IGNORE, JudgeUnavailable, baseline_of, judge


def cmd_audit(a) -> int:
    diff = Path(a.diff).read_text(encoding="utf-8") if a.diff else sys.stdin.read()
    found = audit(diff)
    for v in found:
        print(v)
    print(f"fgg audit: {len(found)} violation(s)" if found else "fgg audit: clean")
    return 1 if found else 0


def cmd_judge(a) -> int:
    try:
        base = baseline_of(Path(a.before))
        print(f"baseline: {base.summary()}")
        j = judge(Path(a.before), Path(a.after), tuple(a.allow), base)
    except JudgeUnavailable as e:
        print(f"UNAVAILABLE: {e}")
        return 3
    print(f"{j.verdict} {j.stage} {j.reason}")
    print("changed: " + (", ".join(j.changed) or "(none)"))
    return 0 if j.verdict == "ACCEPT" else 1


def cmd_run(a) -> int:
    try:
        agent = loop.make_agent(a.agent)
    except (ValueError, OSError) as e:  # bad --agent spec or unreadable mock script
        print(f"fgg: {e}", file=sys.stderr)
        return 1
    work = Path(a.workdir or tempfile.mkdtemp(prefix="fgg-run-")) / "project"
    shutil.copytree(a.target, work, ignore=IGNORE)
    print(f"workdir: {work}  (the target itself is never modified)")
    status, reason = loop.run(work, agent, tuple(a.allow), a.max_iters, a.timeout)
    events = work / ".fgg" / "events.jsonl"
    print(f"{status}: {reason}" + (f"  events={events}" if events.exists() else ""))
    return 0 if status == "DONE" else 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fgg", description="Don't trust a green build you didn't verify.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("audit", help="check a unified diff (stdin or file) for test-neutralizing changes")
    s.add_argument("diff", nargs="?")
    s.set_defaults(fn=cmd_audit)
    for name, fn, hlp in (("judge", cmd_judge, "judge the change between two project directories"),
                          ("run", cmd_run, "fix loop on a copy of TARGET")):
        s = sub.add_parser(name, help=hlp)
        if name == "judge":
            s.add_argument("--before", required=True)
            s.add_argument("--after", required=True)
        else:
            s.add_argument("target")
            s.add_argument("--agent", required=True, help="mock:<script.json> or cmd:<template>")
            s.add_argument("--max-iters", type=int, default=20)
            s.add_argument("--timeout", type=int, default=300, help="agent timeout in seconds")
            s.add_argument("--workdir", help="where to put the working copy (default: new temp dir)")
        s.add_argument("--allow", action="append", default=None, help="glob of files a fix may touch (default src/**)")
        s.set_defaults(fn=fn)
    a = p.parse_args(argv)
    if getattr(a, "allow", 0) is None:
        a.allow = ["src/**"]
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
