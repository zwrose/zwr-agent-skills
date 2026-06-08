"""Determinate, fence-aware guard over the four review-crew SKILL.md files.

Rule 1: neither path literal may appear INSIDE a fenced code block.
Rule 2: the literal profile existence test must not appear at all in the three
        review skills (anti-regression for the resolver-driven guard fix).

Assumptions (intentionally narrow — the skills only use these forms):
  - The fence parser (`_lines_in_fences`) recognizes ``` fences only, not ~~~.
  - Rule 2 targets only the `[ -f review-profile.md ]` existence-test form
    (via `pat`); it is not a general literal-in-prose check.
These rules guard fenced code blocks and the existence-test form; they do NOT
police descriptive prose, which legitimately still mentions the literals.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))

PATH_LITERALS = (".claude/review-profile.md", ".claude/review-decisions.json")
REVIEW_SKILLS = ("review-code", "review-plan", "audit-debt")
ALL_SKILLS = REVIEW_SKILLS + ("review-init",)


def _lines_in_fences(text):
    """Yield (lineno, line) for lines inside ``` fenced blocks."""
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            yield i, line


def _read(skill):
    with open(os.path.join(SKILLS, skill, "SKILL.md")) as fh:
        return fh.read()


def test_rule1_no_path_literal_inside_fences():
    offenders = []
    for skill in ALL_SKILLS:
        for lineno, line in _lines_in_fences(_read(skill)):
            for lit in PATH_LITERALS:
                if lit in line:
                    offenders.append(f"{skill}/SKILL.md:{lineno}: {lit}")
    assert not offenders, "path literal inside a fence:\n" + "\n".join(offenders)


def test_rule2_no_literal_existence_test_in_review_skills():
    pat = re.compile(r"\[\s*!?\s*-f\s+\.claude/review-profile\.md\s*\]")
    offenders = []
    for skill in REVIEW_SKILLS:
        for i, line in enumerate(_read(skill).splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{skill}/SKILL.md:{i}")
    assert not offenders, "literal existence test still present:\n" + "\n".join(offenders)
