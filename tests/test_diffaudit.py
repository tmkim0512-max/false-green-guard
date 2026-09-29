from fgg.diffaudit import audit


def diff(path, removed=(), added=(), deleted=False, new=False):
    old = "/dev/null" if new else f"a/{path}"
    newp = "/dev/null" if deleted else f"b/{path}"
    body = [f"-{l}" for l in removed] + [f"+{l}" for l in added]
    return "\n".join([f"--- {old}", f"+++ {newp}",
                      f"@@ -1,{len(removed)} +1,{len(added)} @@", *body]) + "\n"


def rules(d):
    return sorted({v.rule for v in audit(d)})


# --- each neutralizing pattern is caught -------------------------------------------------

def test_skip_decorator_added():
    assert rules(diff("tests/test_a.py", added=["@pytest.mark.skip(reason='flaky')"])) == ["skip_or_xfail_added"]


def test_skipif_xfail_and_imperative_skip_added():
    d = diff("tests/test_a.py", added=["@pytest.mark.xfail", "@pytest.mark.skipif(True, reason='')",
                                       "    pytest.skip('later')"])
    assert [v.rule for v in audit(d)] == ["skip_or_xfail_added"] * 3


def test_skip_via_pytestmark_and_param_marks():
    d = diff("tests/test_a.py", added=["pytestmark = pytest.mark.skip(reason='later')",
                                       "    pytest.param(1, 2, marks=pytest.mark.xfail),"])
    assert [v.rule for v in audit(d)] == ["skip_or_xfail_added"] * 2


def test_test_file_deleted():
    assert rules(diff("tests/test_a.py", removed=["x = 1"], deleted=True)) == ["test_deleted"]


def test_test_function_removed():
    d = diff("tests/test_a.py", removed=["def test_total():", "    total()"])
    assert rules(d) == ["test_deleted"]


def test_assert_count_drops():
    d = diff("tests/test_a.py", removed=["    assert f(1) == 2"], added=["    f(1)"])
    assert rules(d) == ["assert_weakened"]


def test_trivial_assert_added():
    d = diff("tests/test_a.py", removed=["    assert f(1) == 2"], added=["    assert True"])
    assert rules(d) == ["assert_weakened"]


def test_collection_narrowed_in_each_config_file():
    for path, line in [("conftest.py", "collect_ignore = ['tests/test_a.py']"),
                       ("pytest.ini", "addopts = --deselect tests/test_a.py::test_x"),
                       ("pyproject.toml", 'testpaths = ["other"]'),
                       ("setup.cfg", "norecursedirs = tests"),
                       ("tox.ini", "addopts = -p no:randomly"),
                       (".pytest.ini", "addopts = --ignore=tests")]:
        assert rules(diff(path, added=[line])) == ["collection_narrowed"], path


def test_git_format_with_context_and_multiple_files():
    d = """diff --git a/src/p.py b/src/p.py
--- a/src/p.py
+++ b/src/p.py
@@ -1,2 +1,2 @@
 def f(x):
-    return x
+    return x + 1
diff --git a/tests/test_p.py b/tests/test_p.py
--- a/tests/test_p.py
+++ b/tests/test_p.py
@@ -1,3 +1,4 @@
 from p import f
+@pytest.mark.skip
 def test_f():
     assert f(1) == 2
"""
    [v] = audit(d)
    assert (v.rule, v.path) == ("skip_or_xfail_added", "tests/test_p.py")


# --- honest changes are not flagged (no false positives) ----------------------------------

def test_production_code_is_never_inspected():
    d = diff("src/shop/util.py", removed=["    assert x > 0"],
             added=["SKIP = 'pytest.mark.skip'", "    assert True", "collect_ignore = []"])
    assert audit(d) == []


def test_new_test_added():
    d = diff("tests/test_new.py", added=["def test_new():", "    assert g() == 3"], new=True)
    assert audit(d) == []


def test_assert_rewritten_and_test_renamed_in_place():
    d = diff("tests/test_a.py",
             removed=["def test_total():", "    assert total([1, 2]) == 3"],
             added=["def test_total():", "    assert total([1, 2]) == 3, 'sum of items'"])
    assert audit(d) == []


def test_skip_mentioned_in_strings_and_comments_is_fine():
    d = diff("tests/test_a.py", added=['    msg = "use @pytest.mark.skip sparingly"',
                                       "    # pytest.skip('x') would hide this",
                                       "    check(['@pytest.mark.xfail'])"])
    assert audit(d) == []


def test_skip_marker_removed_is_fine():
    assert audit(diff("tests/test_a.py", removed=["@pytest.mark.skip"])) == []


def test_removed_line_that_looks_like_header_is_not_a_header():
    # "--- " inside a hunk is a removed line "-- ...", not a new file header
    d = diff("tests/test_a.py", removed=["-- comment", "    assert f() == 1"], added=["    assert f() == 1"])
    assert audit(d) == []
