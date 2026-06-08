#!/usr/bin/env python3
"""Decide whether the review auto-fix loop is stuck and should halt.

Faithful port of circuit-breaker.ts. `rounds` are chronological (round 1 first);
each round's findings are that round's compiled findings with deliberately-skipped
findings already removed, so a skipped finding never counts toward recurrence or
progress. Un-sensitive by design: normal 2-3 round convergence never trips it.
"""
import json
import os
import re
import sys

BLOCKING = {"Critical", "Important"}

_NON_WORD = re.compile(r"[^\w\s]", re.ASCII)   # JS \w is ASCII-only — match it
_WS = re.compile(r"\s+", re.ASCII)


def normalize_title(title):
    t = title.lower()
    t = _NON_WORD.sub("", t)
    t = _WS.sub(" ", t)
    return t.strip()


def finding_identity(finding):
    return f"{finding.get('file') or ''}::{normalize_title(finding.get('title') or '')}"


def _blocking(round_findings):
    return [f for f in round_findings["findings"] if f["severity"] in BLOCKING]


def check_circuit_breaker(rounds, max_rounds):
    n = len(rounds)
    if n == 0:
        return {"halt": False, "reason": None, "detail": "no rounds yet"}

    latest_blocking = _blocking(rounds[n - 1])

    # Criterion 3: max iterations (only halts while blocking findings remain).
    if n >= max_rounds and len(latest_blocking) > 0:
        return {
            "halt": True,
            "reason": "max-iterations",
            "detail": (f"Reached {max_rounds} rounds; the latest review still showed "
                       f"{len(latest_blocking)} blocking finding(s) (the final round's "
                       f"fixes are committed but not yet re-reviewed)."),
        }

    # Criterion 1: recurring finding across the two most recent rounds.
    if n >= 2:
        prev_ids = {finding_identity(f) for f in _blocking(rounds[n - 2])}
        recurring = [f for f in latest_blocking if finding_identity(f) in prev_ids]
        if recurring:
            ids = "; ".join(finding_identity(f) for f in recurring)
            return {
                "halt": True,
                "reason": "recurring-finding",
                "detail": f"{len(recurring)} blocking finding(s) recurred after a fix was committed: {ids}",
            }

    # Criterion 2: no net progress across two consecutive round-transitions.
    if n >= 3:
        c_n = len(_blocking(rounds[n - 1]))
        c_n1 = len(_blocking(rounds[n - 2]))
        c_n2 = len(_blocking(rounds[n - 3]))
        if c_n > 0 and c_n >= c_n1 and c_n1 >= c_n2:
            return {
                "halt": True,
                "reason": "no-net-progress",
                "detail": f"Blocking-finding count did not decrease over two rounds ({c_n2} → {c_n1} → {c_n}).",
            }

    return {"halt": False, "reason": None, "detail": "progressing"}


def load_rounds(session_dir):
    """Read round-N/compiled.json for every round in numeric order; remove any
    finding identity that was skipped in ANY round's resolutions.json."""
    entries = []
    for name in os.listdir(session_dir):
        if os.path.isdir(os.path.join(session_dir, name)) and re.fullmatch(r"round-\d+", name):
            entries.append((name, int(name[len("round-"):])))
    entries.sort(key=lambda e: e[1])

    skipped = set()
    for name, _num in entries:
        rp = os.path.join(session_dir, name, "resolutions.json")
        if not os.path.exists(rp):
            continue
        with open(rp) as fh:
            res = json.load(fh)
        for r in res.get("resolutions", []):
            if r.get("action") == "skip":
                skipped.add(finding_identity({"file": r.get("file") or "", "title": r.get("title") or ""}))

    rounds = []
    for name, num in entries:
        cp = os.path.join(session_dir, name, "compiled.json")
        if not os.path.exists(cp):
            continue
        with open(cp) as fh:
            compiled = json.load(fh)
        findings = [f for f in compiled["findings"] if finding_identity(f) not in skipped]
        rounds.append({"round": num, "findings": findings})
    return {"rounds": rounds, "skipped": skipped}


def main(argv):
    args = argv[1:]
    if not args:
        sys.stderr.write("Usage: circuit_breaker.py <session-dir> [max-rounds=7]\n")
        return 2
    session_dir = args[0]
    max_rounds = int(args[1]) if len(args) > 1 else 7
    result = check_circuit_breaker(load_rounds(session_dir)["rounds"], max_rounds)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
