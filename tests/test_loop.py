import json
import shutil
import subprocess
import sys

from conftest import ATTEMPTS, ROOT, SHOP

from fgg import cli, loop

CHEATER = ROOT / "examples" / "mock-agent" / "cheater.json"
HONEST = ROOT / "examples" / "mock-agent" / "honest.json"
ALLOW = ("src/**",)


def work(tmp_path):
    w = tmp_path / "w"
    shutil.copytree(SHOP, w)
    return w


def events(w):
    return [json.loads(l) for l in (w / ".fgg" / "events.jsonl").read_text().splitlines()]


def test_cheater_every_trick_rejected_then_honest_fix_done(tmp_path):
    w = work(tmp_path)
    status, _ = loop.run(w, loop.MockAgent(CHEATER), ALLOW, echo=lambda *_: None)
    assert status == "DONE"
    got = [(e["strategy"], e["verdict"], e["stage"], e["reason"]) for e in events(w)]
    assert got[:5] == [
        ("skip_test", "REJECT", "forbidden", "skip_or_xfail_added"),
        ("claims_fixed", "REJECT", "rerun", "still_failing:1"),
        ("weaken_assert", "REJECT", "forbidden", "assert_weakened"),
        ("delete_test", "REJECT", "forbidden", "assert_weakened,test_deleted"),
        ("narrow_collect", "REJECT", "forbidden", "collection_narrowed"),
    ]
    assert got[5][:3] == ("honest_fix", "ACCEPT", "rerun")
    # only the accepted change reached the working copy
    assert (w / "src/shop/price.py").read_text() == (ATTEMPTS / "honest_fix.py").read_text()
    assert (w / "tests/test_price.py").read_text() == (SHOP / "tests/test_price.py").read_text()
    assert not (w / "conftest.py").exists()


def test_false_claim_is_recorded_but_not_believed(tmp_path):
    w = work(tmp_path)
    loop.run(w, loop.MockAgent(CHEATER), ALLOW, max_iters=2, echo=lambda *_: None)
    e = events(w)[1]
    assert e["claimed"] == "Fixed the discount bug. All tests pass." and e["verdict"] == "REJECT"


def test_budget_exhausted_halts(tmp_path):
    w = work(tmp_path)
    status, reason = loop.run(w, loop.MockAgent(CHEATER), ALLOW, max_iters=3, echo=lambda *_: None)
    assert status == "HALT" and reason.startswith("max_iters=3 reached")
    assert (w / "src/shop/price.py").read_text() == (SHOP / "src/shop/price.py").read_text()


def test_honest_done_first_try(tmp_path):
    w = work(tmp_path)
    assert loop.run(w, loop.MockAgent(HONEST), ALLOW, echo=lambda *_: None)[0] == "DONE"
    assert len(events(w)) == 1


def test_command_agent_honest_fix(tmp_path):
    w = work(tmp_path)
    fixer = tmp_path / "fixer.py"
    fixer.write_text("import pathlib, sys\n"
                     "p = pathlib.Path('src/shop/price.py')\n"
                     "p.write_text(p.read_text().replace('price * percent', 'price * percent / 100'))\n"
                     "print('patched; prompt had', len(open(sys.argv[1]).read()), 'chars')\n")
    agent = loop.CommandAgent(f"{sys.executable} {fixer} {{prompt_file}}")
    status, _ = loop.run(w, agent, ALLOW, echo=lambda *_: None)
    assert status == "DONE" and events(w)[0]["claimed"].startswith("patched")


def test_command_agent_timeout_is_rejected_not_accepted(tmp_path):
    w = work(tmp_path)
    agent = loop.CommandAgent(f"{sys.executable} -c 'import time; time.sleep(5)'")
    status, _ = loop.run(w, agent, ALLOW, max_iters=1, timeout=1, echo=lambda *_: None)
    e = events(w)[0]
    assert status == "HALT" and e["agent_status"] == "timeout" and e["verdict"] == "REJECT"


def test_judge_unavailable_halts_with_cause(tmp_path):
    w = work(tmp_path)
    (w / "pytest.ini").write_text("[pytest]\naddopts = --no-such-option\n")
    status, reason = loop.run(w, loop.MockAgent(HONEST), ALLOW, echo=lambda *_: None)
    assert status == "HALT" and reason.startswith("judge unavailable: pytest exit=4")


# --- CLI exit codes -------------------------------------------------------------------------

def test_cli_audit_exit_codes(tmp_path):
    bad = tmp_path / "bad.diff"
    bad.write_text("--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1,0 +1,1 @@\n+@pytest.mark.skip\n")
    good = tmp_path / "good.diff"
    good.write_text("--- a/src/a.py\n+++ b/src/a.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n")
    assert cli.main(["audit", str(bad)]) == 1
    assert cli.main(["audit", str(good)]) == 0


def test_cli_judge_exit_codes(shop, tmp_path):
    before, after = shop
    assert cli.main(["judge", "--before", str(before), "--after", str(after)]) == 1
    shutil.copyfile(ATTEMPTS / "honest_fix.py", after / "src/shop/price.py")
    assert cli.main(["judge", "--before", str(before), "--after", str(after)]) == 0
    (after / "pytest.ini").write_text("[pytest]\naddopts = --no-such-option\n")
    assert cli.main(["judge", "--before", str(after), "--after", str(after)]) == 3


def test_cli_run_never_touches_target(tmp_path):
    target = work(tmp_path)
    snapshot = (target / "src/shop/price.py").read_text()
    code = subprocess.run([sys.executable, "-m", "fgg.cli", "run", str(target), "--agent", f"mock:{HONEST}",
                           "--workdir", str(tmp_path / "out")], capture_output=True, text=True).returncode
    assert code == 0 and (target / "src/shop/price.py").read_text() == snapshot
    assert not (target / ".fgg").exists()


def test_cli_run_with_relative_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", str(SHOP), "--agent", f"mock:{HONEST}", "--workdir", "rel-out"]) == 0
