"""Structural guard: the Failure-Mode taxonomy class names stay in sync between
the premortem-reviewer's taxonomy table (the human-authored source of truth) and
the eval scorer's matching sets.

The seven Failure-Mode class names are duplicated verbatim across the agent file,
``eval/score.py``, ``eval/README.md`` (twice), and the failure-modes fixture. A
rename in the agent table would silently desync ``score.py``'s verbatim taxonomy
matching (the function-scoped ±15 window keys on these exact strings), so this
test pins the relationship the way ``test_dispatch_tables.py`` pins the dispatch
tables:

- The agent's WHOLE-FLOW classes are exactly ``score.py``'s ``FUNCTION_SCOPED``
  additions (``FUNCTION_SCOPED`` minus its five pre-Failure-Mode members).
- The two LINE-SCOPED classes (``detectability``, ``assumption-violation``)
  appear in the agent table but NOT in ``FUNCTION_SCOPED`` (they match at the
  exact ±2 window, not the generous ±15 one).

Both files are parsed as text (no import) to match the dependency-free style of
the sibling structural guards in this directory.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, "..", ".."))

# The members of score.py's FUNCTION_SCOPED that predate the Failure-Mode
# reviewer. The whole-flow Failure-Mode classes are everything ELSE in the set.
PRE_FAILURE_MODE_FUNCTION_SCOPED = {
    "cognitive-complexity",
    "mock-echo",
    "AcyclicDependencies",
    "premature-abstraction",
    "BFLA",
}

# Failure-Mode classes that are intentionally LINE-scoped (±2), so they must be
# present in the agent's taxonomy table but absent from FUNCTION_SCOPED.
LINE_SCOPED_FAILURE_CLASSES = {"detectability", "assumption-violation"}


def _read(rel):
    with open(os.path.join(PLUGIN, rel)) as f:
        return f.read()


def _premortem_taxonomy():
    """The set of class names in the premortem agent's `## Failure-class
    taxonomy` table (first cell of each row, a backticked token)."""
    text = _read(os.path.join("agents", "premortem-reviewer.md"))
    m = re.search(r"## Failure-class taxonomy\n(.*?)(?:\n## |\Z)", text, re.S)
    assert m, "premortem-reviewer.md: `## Failure-class taxonomy` section not found"
    classes = set(re.findall(r"^\|\s*`([a-z][a-z/-]*)`\s*\|", m.group(1), re.M))
    assert classes, "no backticked class names parsed from the taxonomy table"
    return classes


def _function_scoped():
    """The set of taxonomy strings in score.py's FUNCTION_SCOPED literal."""
    text = _read(os.path.join("eval", "score.py"))
    m = re.search(r"^FUNCTION_SCOPED = \{(.*?)^\}", text, re.M | re.S)
    assert m, "score.py: FUNCTION_SCOPED literal not found"
    members = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert members, "no string members parsed from FUNCTION_SCOPED"
    return members


def test_pre_failure_mode_members_still_present():
    # The whole-flow set is derived by subtracting these five; if one were
    # removed the subtraction would silently mismeasure, so pin them explicitly.
    assert PRE_FAILURE_MODE_FUNCTION_SCOPED <= _function_scoped()


def test_whole_flow_classes_match_agent_and_scorer():
    # The agent's taxonomy table = the scorer's function-scoped Failure-Mode
    # additions PLUS the two intentionally line-scoped classes. One desync
    # (a rename on either side, or a class moved between scopes) fails this.
    table = _premortem_taxonomy()
    function_scoped_additions = _function_scoped() - PRE_FAILURE_MODE_FUNCTION_SCOPED
    assert table == function_scoped_additions | LINE_SCOPED_FAILURE_CLASSES


def test_line_scoped_classes_in_table_but_not_function_scoped():
    table = _premortem_taxonomy()
    function_scoped = _function_scoped()
    for cls in LINE_SCOPED_FAILURE_CLASSES:
        assert cls in table, f"{cls} missing from the premortem taxonomy table"
        assert cls not in function_scoped, f"{cls} must stay line-scoped (not in FUNCTION_SCOPED)"
