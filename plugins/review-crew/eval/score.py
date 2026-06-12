#!/usr/bin/env python3
"""Deterministic golden-eval scorer for the review-crew A/B eval harness.

Scores a set of emitted reviewer findings against a fixture's ``expected.json``
ground truth, implementing the matching rules from ``eval/README.md`` §Scoring.

CLI
---
    python3 score.py <fixture-dir> <findings-glob-or-dir> [--baseline <dir-or-glob>]

``<fixture-dir>`` holds ``expected.json`` and ``diff.txt``. The findings input is
one or more JSON files, each a JSON array of emitted findings (every finding has
at least ``dimension``, ``file``, ``line``; optionally ``taxonomy``/``severity``/
``title``). A directory loads every ``*.json`` under it; a glob loads its matches;
a single file loads that file. The output is a JSON object printed to stdout:

    {"recall":    {"matched": N, "total": M, "by_dimension": {...}, "missed": [...]},
     "precision": {"traps_flagged": K, "trap_hits": ["file:line", ...]},
     "net_new":   [<findings matching neither a seed nor a trap>],
     "gate":      "PASS" | "FAIL" | "n/a"}

Matching rules
--------------
A seed's expected line is derived from its ``lineHint``: the lineHint text is the
text of a ``+`` line in ``diff.txt``, so we parse the diff once, map every added
line's stripped text to its new-file line number, and look the lineHint up there.
(Traps may sit on context lines, so context lines are mapped too.)

- **Line-scoped taxonomies** (everything not in FUNCTION_SCOPED) — a finding
  matches the seed iff same ``file`` AND same ``dimension`` AND the cited line is
  within **±2** of the seed's resolved line. This ±2 rule is exact.

- **Function-scoped taxonomies** (``cognitive-complexity``, ``mock-echo``,
  ``AcyclicDependencies``, ``premature-abstraction``, ``BFLA``, plus the
  Failure-Mode whole-flow classes ``concurrency/race``, ``partial-failure``,
  ``dependency-failure``, ``resource-exhaustion``, ``migration-rollback``) —
  reviewers legitimately cite the declaration, an inner branch, or an assertion
  anywhere in the symbol's body, and exact function-span extraction from a diff
  is fuzzy. So we use the README's documented generous rule: a finding matches
  iff same ``file`` AND same ``dimension`` AND either
    (a) the cited line is within **±K (K=15)** of the seed's resolved line, OR
    (b) the finding carries the same ``taxonomy`` as the seed (anywhere in file).
  Traps are matched with the same scope-aware logic (a function-scoped trap —
  ``size-only``, ``clear-non-duplicative``, or one of the Failure-Mode bait
  reasons — uses the ±K window; otherwise ±2).

Gate (only when a baseline findings set is supplied)
----------------------------------------------------
PASS iff improved recall ≥ baseline recall AND improved traps_flagged ≤ baseline
traps_flagged AND improved flags zero traps. Otherwise FAIL. With no baseline the
gate is ``"n/a"``.
"""

import glob
import json
import os
import sys

FUNCTION_SCOPED = {
    "cognitive-complexity",
    "mock-echo",
    "AcyclicDependencies",
    "premature-abstraction",
    "BFLA",
    # Failure-Mode whole-flow classes (premortem-reviewer): a correct finding
    # legitimately cites any line of the multi-step flow.
    "concurrency/race",
    "partial-failure",
    "dependency-failure",
    "resource-exhaustion",
    "migration-rollback",
}

LINE_SLACK = 2       # exact ±2 window for line-scoped taxonomies
FUNCTION_WINDOW = 15  # generous ±K window for function-scoped taxonomies

# Trap `whyNotFlagged` reasons that denote a function-scoped (whole-symbol) trap;
# all other reasons are line-scoped (match a finding only within ±2 of the line).
FUNCTION_SCOPED_TRAP_REASONS = {
    "size-only",
    "clear-non-duplicative",
    # Failure-Mode bait reasons (whole-flow traps); detection is substring
    # containment over whyNotFlagged, so bait reasons MUST carry their token.
    "profile-excluded-race",
    "retry-wrapped",
    "framework-transaction",
}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_expected(fixture_dir):
    with open(os.path.join(fixture_dir, "expected.json")) as f:
        return json.load(f)


def _findings_paths(path_or_glob):
    if os.path.isdir(path_or_glob):
        return sorted(glob.glob(os.path.join(path_or_glob, "*.json")))
    matches = sorted(glob.glob(path_or_glob))
    if matches:
        return matches
    if os.path.isfile(path_or_glob):
        return [path_or_glob]
    return []


def load_findings(path_or_glob):
    """Load and concatenate every findings JSON array under the given input."""
    findings = []
    for p in _findings_paths(path_or_glob):
        with open(p) as f:
            data = json.load(f)
        if isinstance(data, list):
            findings.extend(data)
        elif isinstance(data, dict):
            findings.append(data)
    return findings


# --------------------------------------------------------------------------
# Diff parsing: map added/context line text -> new-file line number
# --------------------------------------------------------------------------

def _parse_diff_lines(diff_text):
    """Return {file_path: {stripped_line_text: new_line_number}} from a diff.

    Maps both added (`+`) lines (seeds live here) and context lines (traps may
    live here). On a text collision within a file, the first occurrence wins.
    """
    by_file = {}
    cur_file = None
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            cur_file = None
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            cur_file = path
            by_file.setdefault(cur_file, {})
            continue
        if raw.startswith("--- "):
            continue
        if raw.startswith("@@"):
            # @@ -old,len +new,len @@
            try:
                plus = raw.split("+", 1)[1]
                new_start = int(plus.split(",", 1)[0].split(" ", 1)[0])
                new_lineno = new_start - 1
            except (IndexError, ValueError):
                new_lineno = 0
            continue
        if cur_file is None:
            continue
        if raw.startswith("+"):
            new_lineno += 1
            text = raw[1:].strip()
            by_file[cur_file].setdefault(text, new_lineno)
        elif raw.startswith("-"):
            continue  # removed lines do not advance the new-file counter
        else:
            # context line (also present in the new file)
            new_lineno += 1
            text = raw[1:].strip() if raw.startswith(" ") else raw.strip()
            by_file[cur_file].setdefault(text, new_lineno)
    return by_file


def _resolve_line(by_file, file_path, line_hint):
    """Resolve a lineHint's new-file line number, or None if not found."""
    file_map = by_file.get(file_path, {})
    return file_map.get(line_hint.strip())


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _finding_line(finding):
    line = finding.get("line")
    if line is None:
        return None
    try:
        return int(line)
    except (TypeError, ValueError):
        return None


def _matches_location(finding, file_path, target_line, function_scoped,
                      taxonomy=None, require_dimension=None):
    """Scope-aware match of one finding against a target (seed or trap).

    ``function_scoped`` selects the matching window. For function-scoped targets
    the finding also matches if it carries the same ``taxonomy`` (anywhere in the
    file); ``taxonomy`` is the target's taxonomy (None for traps, which have no
    taxonomy of their own, so only the ±K window applies).
    """
    if finding.get("file") != file_path:
        return False
    if require_dimension is not None and finding.get("dimension") != require_dimension:
        return False

    if function_scoped:
        if taxonomy is not None and finding.get("taxonomy") == taxonomy:
            return True
        fl = _finding_line(finding)
        if fl is not None and target_line is not None:
            return abs(fl - target_line) <= FUNCTION_WINDOW
        return False

    # line-scoped: exact ±2
    fl = _finding_line(finding)
    if fl is None or target_line is None:
        return False
    return abs(fl - target_line) <= LINE_SLACK


def _seed_id(seed):
    return seed.get("taxonomy") or seed.get("title") or seed.get("lineHint", "?")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _score_one(expected, by_file, findings):
    """Return (recall, precision, net_new, used_finding_ids) for one variant."""
    seeds = expected.get("seeds", [])
    traps = expected.get("traps", [])

    # track which findings explained a seed or a trap (for net_new)
    explained = [False] * len(findings)

    matched = 0
    missed = []
    by_dimension = {}
    for seed in seeds:
        dim = seed.get("dimension")
        by_dimension.setdefault(dim, {"matched": 0, "total": 0})
        by_dimension[dim]["total"] += 1

        target = _resolve_line(by_file, seed.get("file", ""), seed.get("lineHint", ""))
        taxonomy = seed.get("taxonomy")
        func_scoped = taxonomy in FUNCTION_SCOPED
        hit = False
        for i, finding in enumerate(findings):
            if _matches_location(finding, seed.get("file"), target, func_scoped,
                                 taxonomy=taxonomy, require_dimension=dim):
                explained[i] = True
                hit = True
        if hit:
            matched += 1
            by_dimension[dim]["matched"] += 1
        else:
            missed.append(_seed_id(seed))

    trap_hits = []
    for trap in traps:
        target = _resolve_line(by_file, trap.get("file", ""), trap.get("lineHint", ""))
        # A trap has no dimension; the README matches "some emitted finding" on
        # location regardless of dimension. Scope is the trap's own nature, read
        # from whyNotFlagged: reasons in FUNCTION_SCOPED_TRAP_REASONS are whole-symbol
        # (function-scoped) traps; everything else is line-scoped (±2).
        func_scoped = any(r in (trap.get("whyNotFlagged") or "")
                          for r in FUNCTION_SCOPED_TRAP_REASONS)
        for i, finding in enumerate(findings):
            if _matches_location(finding, trap.get("file"), target, func_scoped):
                explained[i] = True
                key = "%s:%s" % (trap.get("file"), target if target is not None else "?")
                if key not in trap_hits:
                    trap_hits.append(key)

    net_new = [findings[i] for i in range(len(findings)) if not explained[i]]

    recall = {
        "matched": matched,
        "total": len(seeds),
        "by_dimension": by_dimension,
        "missed": missed,
    }
    precision = {
        "traps_flagged": len(trap_hits),
        "trap_hits": trap_hits,
    }
    return recall, precision, net_new


def score_fixture(fixture_dir, findings, baseline_findings=None):
    """Score findings against a fixture. Returns the result dict."""
    expected = load_expected(fixture_dir)
    diff_path = os.path.join(fixture_dir, "diff.txt")
    diff_text = ""
    if os.path.isfile(diff_path):
        with open(diff_path) as f:
            diff_text = f.read()
    by_file = _parse_diff_lines(diff_text)

    recall, precision, net_new = _score_one(expected, by_file, findings)

    if baseline_findings is None:
        gate = "n/a"
    else:
        b_recall, b_precision, _ = _score_one(expected, by_file, baseline_findings)
        improved_ok = (
            recall["matched"] >= b_recall["matched"]
            and precision["traps_flagged"] <= b_precision["traps_flagged"]
            and precision["traps_flagged"] == 0
        )
        gate = "PASS" if improved_ok else "FAIL"

    return {
        "recall": recall,
        "precision": precision,
        "net_new": net_new,
        "gate": gate,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    baseline = None
    if "--baseline" in argv:
        i = argv.index("--baseline")
        baseline = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        sys.stderr.write(
            "usage: score.py <fixture-dir> <findings-glob-or-dir> "
            "[--baseline <dir-or-glob>]\n")
        return 2

    fixture_dir, findings_input = argv[0], argv[1]
    findings = load_findings(findings_input)
    baseline_findings = load_findings(baseline) if baseline else None
    result = score_fixture(fixture_dir, findings, baseline_findings=baseline_findings)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
