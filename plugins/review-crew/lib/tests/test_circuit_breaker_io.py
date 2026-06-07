import json
import os
from circuit_breaker import load_rounds


def imp(title, file="src/a.ts"):
    return {"id": "x-001", "severity": "Important", "dimension": "Code",
            "title": title, "file": file, "line": 1, "body": "", "suggestion": None}


def write_round(d, n, findings, resolutions=None):
    rd = os.path.join(d, f"round-{n}")
    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, "compiled.json"), "w") as fh:
        json.dump({"findings": findings}, fh)
    if resolutions is not None:
        recs = [{"id": "x", **r} for r in resolutions]
        with open(os.path.join(rd, "resolutions.json"), "w") as fh:
            json.dump({"resolutions": recs}, fh)


def test_loads_rounds_in_numeric_order(tmp_path):
    d = str(tmp_path)
    write_round(d, 2, [imp("b")])
    write_round(d, 1, [imp("a")])
    write_round(d, 10, [imp("c")])
    result = load_rounds(d)
    assert [r["round"] for r in result["rounds"]] == [1, 2, 10]


def test_excludes_skipped_identities_across_rounds(tmp_path):
    d = str(tmp_path)
    write_round(d, 1, [imp("Missing filter")],
                [{"file": "src/a.ts", "title": "Missing filter", "action": "skip"}])
    write_round(d, 2, [imp("Missing filter"), imp("Other bug")])
    result = load_rounds(d)
    assert [f["title"] for f in result["rounds"][1]["findings"]] == ["Other bug"]


def test_keeps_fix_resolutions(tmp_path):
    d = str(tmp_path)
    write_round(d, 1, [imp("Keep me")],
                [{"file": "src/a.ts", "title": "Keep me", "action": "fix"}])
    result = load_rounds(d)
    assert len(result["rounds"][0]["findings"]) == 1
