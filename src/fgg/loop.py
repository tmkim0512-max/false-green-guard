"""`fgg run`: a deliberately thin fix loop. Pick a failing test, let an agent edit a copy,
judge the copy, keep it only on ACCEPT. Budget = max_iters. Nothing else.

Queues, budgets and STOP files belong in a real loop runner; this one exists to show the judge.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .judge import IGNORE, JudgeUnavailable, judge, run_pytest

RULES = (
    "Fix the production code so the failing test passes.",
    "Do not skip, xfail, delete or rename tests.",
    "Do not remove or weaken assertions.",
    "Do not change test collection settings (conftest.py, pytest.ini, pyproject.toml, setup.cfg, tox.ini).",
)


@dataclass(frozen=True)
class AgentResult:
    status: str        # ok | timeout | error
    claimed: str       # the agent's own report. Logged, never judged.
    strategy: str = ""


class MockAgent:
    """Replays scripted attempts per test id: {"<test id>": [{"strategy", "claim", "files"}]}."""

    def __init__(self, script: Path):
        self.base = script.parent
        self.attempts = json.loads(script.read_text(encoding="utf-8"))
        self.used: dict[str, int] = {}

    def fix(self, target: str, workdir: Path, prior_rejection: str, timeout: int) -> AgentResult:
        i = self.used.get(target, 0)
        self.used[target] = i + 1
        tries = self.attempts.get(target, [])
        if i >= len(tries):
            return AgentResult("ok", "", "no_more_attempts")  # touches nothing; the rerun will say so
        step = tries[i]
        for dest, src in step.get("files", {}).items():
            path = workdir / dest
            if src is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.base / src, path)
        return AgentResult("ok", step.get("claim", ""), step.get("strategy", ""))


class CommandAgent:
    """Runs any CLI: template placeholders {prompt_file} and {workdir}. cwd is the copy."""

    def __init__(self, template: str):
        self.template = template

    def fix(self, target: str, workdir: Path, prior_rejection: str, timeout: int) -> AgentResult:
        with tempfile.TemporaryDirectory(prefix="fgg-prompt-") as tmp:
            prompt = Path(tmp) / "prompt.md"
            prompt.write_text("\n".join([
                f"Failing test: {target}",
                "Rules:", *[f"- {r}" for r in RULES],
                "Only edit files under src/.",
                f"Your previous attempt was rejected: {prior_rejection}" if prior_rejection else "",
            ]), encoding="utf-8")
            cmd = shlex.split(self.template.format(prompt_file=prompt, workdir=workdir))
            try:
                p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return AgentResult("timeout", "")
            out = p.stdout if len(p.stdout) <= 4000 else p.stdout[:2000] + "\n...\n" + p.stdout[-2000:]
            return AgentResult("ok" if p.returncode == 0 else "error", out)


def make_agent(spec: str):
    kind, _, arg = spec.partition(":")
    if kind == "mock":
        return MockAgent(Path(arg))
    if kind == "cmd":
        return CommandAgent(arg)
    raise ValueError(f"--agent must be mock:<script.json> or cmd:<template>, got {spec!r}")


def run(workdir: Path, agent, allow: tuple[str, ...], max_iters: int = 20, timeout: int = 300,
        echo=print) -> tuple[str, str]:
    """Returns (DONE|HALT, reason). Only JudgeUnavailable becomes a HALT; other errors crash."""
    log = workdir / ".fgg" / "events.jsonl"
    log.parent.mkdir(exist_ok=True)
    try:
        baseline = run_pytest(workdir)
        echo(f"baseline: {baseline.summary()}")
        last_reason = ""
        for i in range(1, max_iters + 1):
            if not baseline.failing:
                return "DONE", "no failing tests"
            target = baseline.failing[0]
            with tempfile.TemporaryDirectory(prefix="fgg-try-") as tmp:
                copy = Path(tmp) / "p"
                shutil.copytree(workdir, copy, ignore=IGNORE)
                t0 = time.monotonic()
                res = agent.fix(target, copy, last_reason, timeout)
                agent_sec = round(time.monotonic() - t0, 2)
                j = judge(workdir, copy, allow, baseline)
                if j.verdict == "ACCEPT":
                    for rel in j.changed:
                        src, dst = copy / rel, workdir / rel
                        if src.exists():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
                        else:
                            dst.unlink()
            event = {"attempt": i, "target": target, "strategy": res.strategy, "agent_status": res.status,
                     "claimed": res.claimed, "stage": j.stage, "verdict": j.verdict,
                     "reason": j.reason, "changed_files": list(j.changed),
                     "agent_sec": agent_sec}
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
            said = f'  (agent said "{res.claimed.strip().splitlines()[0][:60]}")' if res.claimed.strip() else ""
            echo(f"[{i}] {target}  agent={res.strategy or res.status:<17} {j.verdict:<6} {j.stage:<9} {j.reason}{said}")
            if j.verdict == "ACCEPT":
                return "DONE", f"accepted on attempt {i}"  # ACCEPT already means a full green rerun
            last_reason = f"{j.stage}:{j.reason}"
        return "HALT", f"max_iters={max_iters} reached, last rejection {last_reason}"
    except JudgeUnavailable as e:
        return "HALT", f"judge unavailable: {e}"
