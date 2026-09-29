# false-green-guard

Don't trust a green build you didn't verify. `fgg` detects diffs that make tests pass by
neutralizing them, and re-judges a fix by running the suite again on an isolated copy.
An optional agent fix loop shows the judge at work.

> 초록불을 믿지 않는 판정 도구. 테스트를 무력화한 diff를 잡고, 수정 결과를 격리 사본에서 다시 판정한다.

## What it catches

| Pattern | Example diff line | Verdict |
|---|---|---|
| skip / xfail added | `+@pytest.mark.skip(reason="flaky")` | `skip_or_xfail_added` |
| test deleted | `-def test_discount():` (and not re-added) | `test_deleted` |
| assertion weakened | `-assert f(200, 10) == 180` / `+assert True` | `assert_weakened` |
| collection narrowed | `+collect_ignore = ["tests/test_price.py"]` in `conftest.py` | `collection_narrowed` |
| "fixed it" with no real fix | agent says *All tests pass*, suite still red | `still_failing:<n>` (rerun) |

The first four rules look only at test files (`tests/**`, `test_*.py`, `*_test.py`) and collection
config (`conftest.py`, `pytest.ini`, `.pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`).
Production code is not inspected: the string `pytest.mark.skip` in `src/` is legitimate, and
flagging it would train people to ignore the gate. Tricks hidden in `src/` are caught by the rerun instead
(fewer tests collected, or more tests skipped than the baseline).

`fgg audit` is a line heuristic over added and removed lines, not a parser. Skip markers count only in
code positions (decorator, `pytest.skip(` statement, `pytestmark`, `marks=`), so a string that mentions
`@pytest.mark.skip` passes; a string containing `marks=pytest.mark.xfail` is still flagged.

## No AI needed: `fgg audit` and `fgg judge`

```
$ git diff | fgg audit          # a test got @skip, conftest.py got collect_ignore
collection_narrowed    conftest.py  collect_ignore = ["tests/test_price.py"]
skip_or_xfail_added    tests/test_price.py  @pytest.mark.skip(reason="flaky")
fgg audit: 2 violation(s)
exit=1

$ git diff | fgg audit          # the honest fix: src/shop/price.py only
fgg audit: clean
exit=0
```

`fgg judge` compares two directories: forbidden diff, then scope (`--allow`, default `src/**`),
then an isolated pytest rerun. The first rejection wins. Below, `fixed` is a copy of the example with
the honest fix applied and `claimed` is an untouched copy.

```
$ fgg judge --before examples/buggy-shop --after fixed
baseline: 2 collected: PASS=1 FAIL=1 BLOCKED=0 NOT_RUN=0
ACCEPT rerun 2 collected: PASS=2 FAIL=0 BLOCKED=0 NOT_RUN=0
changed: src/shop/price.py
exit=0

$ fgg judge --before examples/buggy-shop --after claimed     # nothing changed
baseline: 2 collected: PASS=1 FAIL=1 BLOCKED=0 NOT_RUN=0
REJECT rerun still_failing:1
changed: (none)
exit=1
```

Exit codes: `0` pass, `1` violation or reject, `3` no verdict (pytest could not run). A judge
that cannot decide never passes.

Use it as a PR gate: copy [`examples/pr-gate.yml`](examples/pr-gate.yml) into `.github/workflows/`.
This repository runs the same audit on its own pull requests.

## Agent fix loop (no API key)

`fgg run` copies the target, asks an agent to fix the first failing test in a throwaway copy,
judges it, and keeps the change only on ACCEPT. The mock agent replays scripted attempts.

```
$ fgg run examples/buggy-shop --agent mock:examples/mock-agent/cheater.json --workdir out-cheater
workdir: out-cheater/project  (the target itself is never modified)
baseline: 2 collected: PASS=1 FAIL=1 BLOCKED=0 NOT_RUN=0
[1] tests.test_price::test_discount  agent=skip_test         REJECT forbidden skip_or_xfail_added  (agent said "Test was flaky, stabilized it.")
[2] tests.test_price::test_discount  agent=claims_fixed      REJECT rerun     still_failing:1  (agent said "Fixed the discount bug. All tests pass.")
[3] tests.test_price::test_discount  agent=weaken_assert     REJECT forbidden assert_weakened  (agent said "Relaxed an over-strict assertion.")
[4] tests.test_price::test_discount  agent=delete_test       REJECT forbidden assert_weakened,test_deleted  (agent said "Removed an obsolete test.")
[5] tests.test_price::test_discount  agent=narrow_collect    REJECT forbidden collection_narrowed  (agent said "Cleaned up test configuration.")
[6] tests.test_price::test_discount  agent=honest_fix        ACCEPT rerun     2 collected: PASS=2 FAIL=0 BLOCKED=0 NOT_RUN=0  (agent said "Divide percent by 100.")
DONE: accepted on attempt 6  events=out-cheater/project/.fgg/events.jsonl
exit=0

$ fgg run examples/buggy-shop --agent mock:examples/mock-agent/cheater.json --max-iters 3 --workdir out-budget
...
[3] tests.test_price::test_discount  agent=weaken_assert     REJECT forbidden assert_weakened  (agent said "Relaxed an over-strict assertion.")
HALT: max_iters=3 reached, last rejection forbidden:assert_weakened  events=out-budget/project/.fgg/events.jsonl
exit=2
```

`examples/mock-agent/honest.json` ends with `DONE: accepted on attempt 1`, exit 0.
Every attempt is one line in `.fgg/events.jsonl`, including the agent's claim, which is logged and never judged.

Real agent: any CLI works through a command template (`{prompt_file}`, `{workdir}`):

```
fgg run path/to/project --agent "cmd:your-agent-cli --prompt-file {prompt_file}"
fgg run path/to/project --agent "cmd:./fix.sh {prompt_file}"
```

The agent runs with the copy as its working directory, but it is still a process on your machine.
Run untrusted agents in a container.

Outputs recorded on macOS, Python 3.14.7, pytest 9.1.1. The test suite also passes locally on Python 3.10.20.
The CI matrix is 3.10 and 3.12.

## Design rules

1. The verdict never uses the fixer's own report: it comes only from the diff and a fresh rerun.
2. "Cannot judge" is a stop, not a pass: exit 3 for `audit`/`judge`, HALT for `run`.
3. A run ends in `DONE` or in a `HALT` that states why.

Test results use one vocabulary: `PASS`, `FAIL`, `BLOCKED` (error before the assertion), `NOT_RUN` (skipped).
Their sum must equal the junit test count, and a mismatch is treated as "cannot judge".

## Not included

- Queues, per-test reject caps, stall detection, wall-clock budgets: the loop is intentionally thin (`max_iters` only).
- Second-opinion judge agent.
- Parallel attempts, crash recovery, locking.
- Runners other than pytest; languages other than Python.
- LLM SDK integrations (use `cmd:`).

## Install and test

```
pip install -e .
python -m pytest -q      # 39 passed
```

Requires Python 3.10+ and pytest. No other dependencies.

## Related

- [ko-tc-playwright](https://github.com/tmkim0512-max/ko-tc-playwright) — turns Korean manual test cases into Playwright (pytest) code; reports conversion rate and real run results separately
- [pom-scout](https://github.com/tmkim0512-max/pom-scout) — explores a web app and writes Page Object JSON with selectors verified unique on the live page
- [evidence-gated-e2e-loop](https://github.com/tmkim0512-max/evidence-gated-e2e-loop) — accepts AI-written Playwright tests only on file evidence, then replays them without AI
- [parking-api-qa-lab](https://github.com/tmkim0512-max/parking-api-qa-lab) — a small parking API tested with pytest, a hand-built mock server, k6 thresholds and GitHub Actions

## License

MIT, Taemin Kim.
