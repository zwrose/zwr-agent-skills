import json

import pytest
import score


# ---- helpers -------------------------------------------------------------

def _write(path, obj):
    path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
    return str(path)


def _make_fixture(tmp_path, expected, diff):
    fdir = tmp_path / "fixture"
    fdir.mkdir()
    (fdir / "expected.json").write_text(json.dumps(expected))
    (fdir / "diff.txt").write_text(diff)
    return str(fdir)


# A small synthetic diff for one file. New-file line numbers (the `+` side)
# start at 1 here. Functions span the lines shown.
SYNTH_DIFF = """diff --git a/src/app.ts b/src/app.ts
--- a/src/app.ts
+++ b/src/app.ts
@@ -0,0 +1,20 @@
+import { db } from "./db";
+
+export function getNote(id) {
+  return db.notes.findOne({ id });
+}
+
+export function classifyOrder(order) {
+  if (order.status === "open") {
+    if (order.total > 100) {
+      return "large";
+    }
+  }
+  return "small";
+}
+
+function persistNote(record) {
+  return db.notes.insert(record);
+}
+
+const x = 1;
"""

# Line numbers in the new file (1-based):
# 1  import { db } from "./db";
# 2  (blank)
# 3  export function getNote(id) {
# 4    return db.notes.findOne({ id });
# 5  }
# 6  (blank)
# 7  export function classifyOrder(order) {
# 8    if (order.status === "open") {
# 9      if (order.total > 100) {
# 10       return "large";
# 11     }
# 12   }
# 13   return "small";
# 14 }
# 15 (blank)
# 16 function persistNote(record) {
# 17   return db.notes.insert(record);
# 18 }
# 19 (blank)
# 20 const x = 1;


# ---- line-scoped slack (test case 1) -------------------------------------

def test_line_scoped_exact_match(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 4}]  # seed is line 4
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1
    assert r["recall"]["total"] == 1


def test_line_scoped_within_two_lines(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 6}]  # +2 from line 4
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1


def test_line_scoped_three_lines_off_not_matched(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 7}]  # +3 from line 4
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 0
    assert len(r["recall"]["missed"]) == 1


# ---- function-scoped span (test case 2) ----------------------------------

def test_function_scoped_match_several_lines_off(tmp_path):
    # cognitive-complexity seed on classifyOrder (declaration at line 7).
    # A finding several lines off (line 10, the deepest branch) is still inside
    # the function span window -> matched, even though it is > 2 lines away.
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "cognitive-complexity",
                           "file": "src/app.ts",
                           "lineHint": "export function classifyOrder(order) {"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 10}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1


def test_function_scoped_same_taxonomy_anywhere_in_file(tmp_path):
    # Function-scoped fallback: same file + dimension + taxonomy matches even
    # outside the +/-K window.
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "cognitive-complexity",
                           "file": "src/app.ts",
                           "lineHint": "export function classifyOrder(order) {"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 200,
                 "taxonomy": "cognitive-complexity"}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1


# ---- traps / precision (test case 3) -------------------------------------

def test_trap_flagged_counts_as_fp(tmp_path):
    expected = {"seeds": [],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "  return db.notes.insert(record);",
                           "whyNotFlagged": "context-line"}]}  # line 17
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 17}]
    r = score.score_fixture(fdir, findings)
    assert r["precision"]["traps_flagged"] == 1
    assert "src/app.ts:17" in r["precision"]["trap_hits"]


# ---- net_new (test case 4) -----------------------------------------------

def test_net_new_when_matching_neither(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    # line 20 matches neither the seed (line 4) nor any trap
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 4, "title": "seed hit"},
                {"dimension": "Code", "file": "src/app.ts", "line": 20, "title": "extra"}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1
    assert len(r["net_new"]) == 1
    assert r["net_new"][0]["line"] == 20


# ---- recall math + by_dimension (test case 5) ----------------------------

def test_recall_math_by_dimension(tmp_path):
    expected = {"seeds": [
        {"dimension": "Code", "taxonomy": "hardcoded-error-string", "file": "src/app.ts",
         "lineHint": "  return db.notes.findOne({ id });"},                 # line 4
        {"dimension": "Security", "taxonomy": "BOLA", "file": "src/app.ts",
         "lineHint": "  return db.notes.insert(record);"},                  # line 17
        {"dimension": "Security", "taxonomy": "BOPLA", "file": "src/app.ts",
         "lineHint": "const x = 1;"},                                       # line 20
    ], "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    # Hit the Code seed and one of two Security seeds.
    findings = [{"dimension": "Code", "file": "src/app.ts", "line": 4},
                {"dimension": "Security", "file": "src/app.ts", "line": 17}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 2
    assert r["recall"]["total"] == 3
    assert r["recall"]["by_dimension"]["Code"] == {"matched": 1, "total": 1}
    assert r["recall"]["by_dimension"]["Security"] == {"matched": 1, "total": 2}
    # the missed Security/BOPLA seed surfaces in missed[]
    assert len(r["recall"]["missed"]) == 1


def test_dimension_mismatch_does_not_match(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    # Right line, wrong dimension -> not a recall match (and becomes net_new).
    findings = [{"dimension": "Security", "file": "src/app.ts", "line": 4}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 0
    assert len(r["net_new"]) == 1


# ---- baseline gate (test case 6) -----------------------------------------

def test_gate_pass_when_improved(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "  return db.notes.insert(record);",
                           "whyNotFlagged": "context-line"}]}  # line 17
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    improved = [{"dimension": "Code", "file": "src/app.ts", "line": 4}]   # seed hit, no trap
    baseline = [{"dimension": "Code", "file": "src/app.ts", "line": 17}]  # missed seed, hit trap
    r = score.score_fixture(fdir, improved, baseline_findings=baseline)
    assert r["gate"] == "PASS"


def test_gate_fail_on_lost_seed(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    improved = []                                                          # lost the seed
    baseline = [{"dimension": "Code", "file": "src/app.ts", "line": 4}]    # caught it
    r = score.score_fixture(fdir, improved, baseline_findings=baseline)
    assert r["gate"] == "FAIL"


def test_gate_fail_on_new_trap(tmp_path):
    expected = {"seeds": [{"dimension": "Code", "taxonomy": "hardcoded-error-string",
                           "file": "src/app.ts",
                           "lineHint": "  return db.notes.findOne({ id });"}],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "  return db.notes.insert(record);",
                           "whyNotFlagged": "context-line"}]}  # line 17
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    improved = [{"dimension": "Code", "file": "src/app.ts", "line": 4},     # seed hit
                {"dimension": "Code", "file": "src/app.ts", "line": 17}]    # but flags a trap
    baseline = [{"dimension": "Code", "file": "src/app.ts", "line": 4}]     # seed hit, no trap
    r = score.score_fixture(fdir, improved, baseline_findings=baseline)
    assert r["gate"] == "FAIL"


def test_gate_na_without_baseline(tmp_path):
    expected = {"seeds": [], "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    r = score.score_fixture(fdir, [])
    assert r["gate"] == "n/a"


# ---- findings loading from disk ------------------------------------------

def test_load_findings_from_glob_merges_arrays(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    _write(d / "a.json", [{"dimension": "Code", "file": "x.ts", "line": 1}])
    _write(d / "b.json", [{"dimension": "Security", "file": "y.ts", "line": 2}])
    loaded = score.load_findings(str(d))
    assert len(loaded) == 2


# ---- smoke test against a real fixture -----------------------------------

def test_smoke_real_refactor_fixture(tmp_path):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    fdir = os.path.join(os.path.dirname(here), "fixtures", "refactor")
    # A perfect-recall, no-trap improved findings set citing the seed lines.
    findings = [
        {"dimension": "Architecture", "taxonomy": "AcyclicDependencies",
         "file": "src/services/billing.ts", "line": 4},
        {"dimension": "Code", "taxonomy": "cognitive-complexity",
         "file": "src/services/orders.ts", "line": 12},
        {"dimension": "Security", "taxonomy": "BFLA",
         "file": "src/handlers/admin-orders.ts", "line": 7},
        {"dimension": "Security", "taxonomy": "BOPLA",
         "file": "src/handlers/admin-orders.ts", "line": 19},
        {"dimension": "Test", "taxonomy": "mock-echo",
         "file": "src/services/orders.test.ts", "line": 13},
    ]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["total"] == 5
    assert r["recall"]["matched"] == 5
    assert r["precision"]["traps_flagged"] == 0


# ---- Failure-Mode whole-flow classes (premortem-reviewer) ------------------


@pytest.mark.parametrize("taxonomy", [
    "concurrency/race",
    "partial-failure",
    "dependency-failure",
    "resource-exhaustion",
    "migration-rollback",
])
def test_failure_mode_seed_matches_at_flow_distance(tmp_path, taxonomy):
    # Whole-flow classes are function-scoped: a correct finding citing a
    # different line of the same flow (10 lines off) must still match.
    expected = {"seeds": [{"dimension": "Failure-Mode", "taxonomy": taxonomy,
                           "file": "src/app.ts",
                           "lineHint": "export function getNote(id) {"}],  # line 3
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 13}]
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1


@pytest.mark.parametrize("taxonomy", ["detectability", "assumption-violation"])
def test_line_scoped_failure_mode_taxonomies_stay_line_scoped(tmp_path, taxonomy):
    # detectability and assumption-violation keep the exact +/-2 default
    # (they are NOT in FUNCTION_SCOPED). A finding 4 lines off must not match.
    expected = {"seeds": [{"dimension": "Failure-Mode", "taxonomy": taxonomy,
                           "file": "src/app.ts",
                           "lineHint": "export function getNote(id) {"}],  # line 3
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 7, "taxonomy": taxonomy}]  # +4 off
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 0


def test_failure_mode_seed_matches_at_exact_window_boundary(tmp_path):
    # Boundary pin: a finding exactly K=15 lines from the seed's resolved line
    # (line 3) must still match (|18 - 3| == 15 == FUNCTION_WINDOW).
    expected = {"seeds": [{"dimension": "Failure-Mode", "taxonomy": "partial-failure",
                           "file": "src/app.ts",
                           "lineHint": "export function getNote(id) {"}],  # line 3
                "traps": []}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 18}]  # exactly 15 off
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["matched"] == 1


@pytest.mark.parametrize("reason_token", [
    "profile-excluded-race",
    "retry-wrapped",
    "framework-transaction",
])
def test_failure_mode_trap_reason_is_function_scoped(tmp_path, reason_token):
    # A bait trap whose whyNotFlagged carries a Failure-Mode scope token uses
    # the +/-15 window: a finding 10 lines off still counts as a trap hit.
    expected = {"seeds": [],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "export function getNote(id) {",  # line 3
                           "whyNotFlagged": reason_token + " — guarded, see CLAUDE.md"}]}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 13}]
    r = score.score_fixture(fdir, findings)
    assert r["precision"]["traps_flagged"] == 1


def test_token_less_trap_reason_stays_line_scoped(tmp_path):
    # A prose reason with NO scope token silently degrades to +/-2 — the spec
    # requires bait reasons to carry their token; this test pins the behavior
    # that makes that requirement load-bearing.
    expected = {"seeds": [],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "export function getNote(id) {",  # line 3
                           "whyNotFlagged": "this is fine because reasons"}]}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 13}]
    r = score.score_fixture(fdir, findings)
    assert r["precision"]["traps_flagged"] == 0


def test_finding_outside_all_windows_lands_net_new(tmp_path):
    # Negative boundary: 16+ lines from a function-scoped trap is outside the
    # +/-15 window — the FP lands in net_new, NOT traps.
    expected = {"seeds": [],
                "traps": [{"file": "src/app.ts",
                           "lineHint": "import { db } from \"./db\";",  # line 1
                           "whyNotFlagged": "framework-transaction — atomic"}]}
    fdir = _make_fixture(tmp_path, expected, SYNTH_DIFF)
    findings = [{"dimension": "Failure-Mode", "file": "src/app.ts", "line": 17}]  # 16 off
    r = score.score_fixture(fdir, findings)
    assert r["precision"]["traps_flagged"] == 0
    assert len(r["net_new"]) == 1


# ---- real-fixture liveness smokes (failure-modes fixtures) -----------------

def _real_fixture(name):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "fixtures", name)


def test_smoke_failure_modes_perfect_recall():
    # Liveness: every seed's lineHint resolves, and a finding citing each
    # resolved line exactly scores matched == total. An unresolvable seed
    # would fail loudly here (missed[]), before any agent ever runs.
    fdir = _real_fixture("failure-modes")
    expected = score.load_expected(fdir)
    with open(fdir + "/diff.txt") as f:
        by_file = score._parse_diff_lines(f.read())
    findings = []
    for seed in expected["seeds"]:
        line = score._resolve_line(by_file, seed["file"], seed["lineHint"])
        assert line is not None, f"seed lineHint does not resolve: {seed['lineHint']!r}"
        findings.append({"dimension": seed["dimension"],
                         "file": seed["file"], "line": line})
    r = score.score_fixture(fdir, findings)
    assert r["recall"]["total"] == 5
    assert {s["taxonomy"] for s in expected["seeds"]} == {
        "concurrency/race", "partial-failure", "dependency-failure",
        "resource-exhaustion", "migration-rollback",
    }
    assert r["recall"]["matched"] == r["recall"]["total"] == len(expected["seeds"])
    assert r["precision"]["traps_flagged"] == 0


def test_smoke_bait_fixture_traps_are_live():
    # Liveness: every bait trap's lineHint resolves AND fires when a finding
    # is placed on it. traps_flagged == 0 is otherwise vacuously satisfiable
    # by a dead trap whose lineHint never matches (no warning from score.py).
    fdir = _real_fixture("failure-modes-bait")
    expected = score.load_expected(fdir)
    assert expected["seeds"] == []
    with open(fdir + "/diff.txt") as f:
        by_file = score._parse_diff_lines(f.read())
    findings = []
    for trap in expected["traps"]:
        line = score._resolve_line(by_file, trap["file"], trap["lineHint"])
        assert line is not None, f"trap lineHint does not resolve: {trap['lineHint']!r}"
        findings.append({"dimension": "Failure-Mode", "file": trap["file"], "line": line})
    assert len(expected["traps"]) == 3
    r = score.score_fixture(fdir, findings)
    assert r["precision"]["traps_flagged"] == len(expected["traps"])
