import shutil

import pytest
from conftest import ATTEMPTS

from fgg.judge import JudgeUnavailable, RunResult, baseline_of, judge, parse_junit, run_pytest

ALLOW = ("src/**",)
PRICE = "src/shop/price.py"
TEST = "tests/test_price.py"


def put(root, rel, attempt):
    shutil.copyfile(ATTEMPTS / attempt, root / rel)


def test_baseline_of_buggy_shop(shop):
    base = baseline_of(shop[0])
    assert base.counts == {"PASS": 1, "FAIL": 1, "BLOCKED": 0, "NOT_RUN": 0}
    assert base.failing == ("tests.test_price::test_discount",)


def test_honest_fix_is_accepted(shop):
    before, after = shop
    put(after, PRICE, "honest_fix.py")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.changed) == ("ACCEPT", "rerun", (PRICE,))
    assert j.run.counts == {"PASS": 2, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0}


def test_no_change_is_rejected_by_rerun(shop):
    before, after = shop
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "rerun", "still_failing:1")


def test_forbidden_runs_before_scope(shop):
    # the skip edit is both forbidden and off-scope; forbidden must be the reported stage
    before, after = shop
    put(after, TEST, "skip_test.py")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "forbidden", "skip_or_xfail_added")


def test_honest_looking_test_edit_is_off_scope(shop):
    before, after = shop
    (after / TEST).write_text((after / TEST).read_text() + "\n# note\n")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "scope", f"off_scope:{TEST}")


def test_rerun_rejects_collected_drop(shop):
    before, after = shop
    put(after, PRICE, "honest_fix.py")
    bigger = RunResult({"PASS": 3, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0}, ())
    assert judge(before, after, ALLOW, bigger).reason == "collected_dropped:3->2"


def test_skip_hidden_in_production_code_is_caught_by_rerun(shop):
    # the audit never reads src/, so a module-level skip there passes stage 1 and 2;
    # the rerun sees the test module vanish from the passing set
    before, after = shop
    put(after, PRICE, "honest_fix.py")
    (after / "src" / "shop" / "__init__.py").write_text(
        "import pytest, sys\nif 'pytest' in sys.modules:\n    pytest.skip('x', allow_module_level=True)\n")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "rerun", "collected_dropped:2->1")


def test_rerun_rejects_not_run_growth(shop):
    # fixes the bug but makes the production code skip the other test when run under pytest
    before, after = shop
    (after / PRICE).write_text(
        "import sys\n\n\ndef apply_discount(price, percent):\n"
        "    if percent == 0 and 'pytest' in sys.modules:\n"
        "        sys.modules['pytest'].skip('not today')\n"
        "    return price - price * percent / 100\n")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "rerun", "not_run_grew:0->1")


def test_unrunnable_pytest_raises(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --no-such-option\n")
    with pytest.raises(JudgeUnavailable, match="exit=4"):
        run_pytest(tmp_path)


def test_non_utf8_file_raises(shop):
    before, after = shop
    (after / "src" / "shop" / "blob.py").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(JudgeUnavailable, match="non-UTF-8"):
        judge(before, after, ALLOW, baseline_of(before))


def test_junit_count_mismatch_raises():
    xml = '<testsuites><testsuite tests="2"><testcase classname="t" name="a"/></testsuite></testsuites>'
    with pytest.raises(JudgeUnavailable, match="junit says 2"):
        parse_junit(xml)


def test_junit_status_mapping_sums_to_collected():
    xml = """<testsuites><testsuite tests="4">
      <testcase classname="t" name="p"/>
      <testcase classname="t" name="f"><failure/></testcase>
      <testcase classname="t" name="e"><error/></testcase>
      <testcase classname="t" name="s"><skipped/></testcase></testsuite></testsuites>"""
    r = parse_junit(xml)
    assert r.counts == {"PASS": 1, "FAIL": 1, "BLOCKED": 1, "NOT_RUN": 1} and r.collected == 4
    assert r.failing == ("t::e", "t::f")


def test_broken_import_in_fix_is_rejected_not_unavailable(shop):
    # pytest exits 2 on collection errors; that is a failing fix, not a broken judge
    before, after = shop
    (after / PRICE).write_text("def apply_discount(price, percent)\n    return 0\n")
    j = judge(before, after, ALLOW, baseline_of(before))
    assert (j.verdict, j.stage, j.reason) == ("REJECT", "rerun", "still_failing:1")
