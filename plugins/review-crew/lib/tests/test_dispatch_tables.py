"""Structural guard: per-agent dispatch tables and prose enumerations stay in
sync with the bundled agents and the rubric's dimension list.

- review-plan and review-code dispatch ALL bundled agents: their substitution
  tables have exactly one row per file in agents/, and their "Specialists to
  dispatch" prose enumerations name every slug.
- audit-debt intentionally dispatches only the ORIGINAL FOUR (Failure-Mode
  whole-repo sweep deferred) — guarded here so a four->five sweep cannot
  silently change it.
- Every dimension label used in a table row appears backticked in the rubric's
  Dimensions declaration.
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, "..", ".."))

ORIGINAL_FOUR = {
    "architecture-reviewer", "code-reviewer", "security-reviewer", "test-reviewer",
}

ROW_RE = re.compile(
    r"^\|\s*([a-z][a-z-]*-reviewer)\s*\|\s*([a-z-]+)\s*\|\s*([A-Za-z-]+)\s*\|",
    re.M)


def _read(rel):
    with open(os.path.join(PLUGIN, rel)) as f:
        return f.read()


def _agent_slugs():
    adir = os.path.join(PLUGIN, "agents")
    return {fn[:-3] for fn in os.listdir(adir) if fn.endswith(".md")}


def _table_rows(rel):
    return ROW_RE.findall(_read(rel))


def _rubric_dimensions():
    text = _read(os.path.join("rubric", "review-base.md"))
    m = re.search(r"\*\*Dimensions\*\*.*?:\s*((?:`[A-Za-z-]+`(?:,\s*)?)+)", text, re.S)
    assert m, "rubric Dimensions declaration not found"
    return set(re.findall(r"`([A-Za-z-]+)`", m.group(1)))


@pytest.mark.parametrize("skill", ["review-plan", "review-code"])
def test_full_crew_table_has_one_row_per_agent(skill):
    rows = _table_rows(os.path.join("skills", skill, "SKILL.md"))
    assert {slug for slug, _, _ in rows} == _agent_slugs()


def test_audit_debt_table_lists_exactly_the_original_four():
    rows = _table_rows(os.path.join("skills", "audit-debt", "SKILL.md"))
    assert {slug for slug, _, _ in rows} == ORIGINAL_FOUR


@pytest.mark.parametrize("skill,expected_slugs", [
    ("review-plan", "ALL"),
    ("review-code", "ALL"),
    ("audit-debt", "FOUR"),
])
def test_specialists_to_dispatch_prose_enumeration(skill, expected_slugs):
    text = _read(os.path.join("skills", skill, "SKILL.md"))
    want = _agent_slugs() if expected_slugs == "ALL" else ORIGINAL_FOUR
    enumerated = set(re.findall(r"^\s*-\s*`([a-z][a-z-]*-reviewer)`\s*→", text, re.M))
    assert enumerated == want


@pytest.mark.parametrize("skill", ["review-plan", "review-code", "audit-debt"])
def test_table_dimensions_exist_in_rubric(skill):
    dims = _rubric_dimensions()
    for slug, _findings, dimension in _table_rows(os.path.join("skills", skill, "SKILL.md")):
        assert dimension in dims, f"{skill}: {slug} row uses unknown dimension {dimension}"
