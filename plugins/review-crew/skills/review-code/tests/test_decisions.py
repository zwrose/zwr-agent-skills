import json
import os
import sys

import decisions


def run(args):
    """Invoke decisions.main; capture stdout. Returns (exit_code, stdout_text)."""
    import io
    out = io.StringIO()
    real = sys.stdout
    sys.stdout = out
    try:
        code = decisions.main(["decisions.py", *args])
    finally:
        sys.stdout = real
    return code, out.getvalue()


def rec(dimension, category, action):
    return {"dimension": dimension, "category": category, "action": action}


def read_store(path):
    with open(path) as fh:
        return json.load(fh)


# --- 1. append creates the file + accumulates records ---

def test_append_creates_file_with_schema_and_one_record(tmp_path):
    f = str(tmp_path / "review-decisions.json")
    code, _ = run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    assert code == 0
    store = read_store(f)
    assert store["schema"] == 1
    assert len(store["records"]) == 1
    assert store["records"][0]["dimension"] == "Security"


def test_second_append_yields_two_records(tmp_path):
    f = str(tmp_path / "review-decisions.json")
    run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    run(["append", f, json.dumps(rec("Perf", "n+1", "fix"))])
    store = read_store(f)
    assert len(store["records"]) == 2


# --- 2. file always parses (atomicity proxy) ---

def test_post_append_file_is_always_valid_json(tmp_path):
    f = str(tmp_path / "review-decisions.json")
    for i in range(5):
        run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
        read_store(f)  # raises if partial/corrupt
    assert len(read_store(f)["records"]) == 5
    # no leftover temp files in the dir
    leftovers = [n for n in os.listdir(tmp_path) if n != "review-decisions.json"]
    assert leftovers == []


# --- 3. skip threshold + profile routing ---

def test_two_skips_same_dim_cat_below_threshold_null(tmp_path):
    f = str(tmp_path / "d.json")
    for _ in range(2):
        run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    code, out = run(["analyze", f])
    assert code == 0
    assert json.loads(out)["proposal"] is None


def test_three_skips_same_dim_cat_proposes_profile(tmp_path):
    f = str(tmp_path / "d.json")
    for _ in range(3):
        run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    code, out = run(["analyze", f])
    prop = json.loads(out)["proposal"]
    assert prop is not None
    assert prop["target"] == "profile"
    assert prop["dimension"] == "Security"
    assert prop["category"] == "authz"
    assert isinstance(prop["text"], str) and prop["text"]
    assert isinstance(prop["signal_hash"], str) and prop["signal_hash"]


# --- 4. skips spread across categories do not qualify ---

def test_three_skips_different_categories_null(tmp_path):
    f = str(tmp_path / "d.json")
    run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    run(["append", f, json.dumps(rec("Security", "crypto", "skip"))])
    run(["append", f, json.dumps(rec("Security", "input", "skip"))])
    _, out = run(["analyze", f])
    assert json.loads(out)["proposal"] is None


# --- 5. guidance threshold + CLAUDE.md routing ---

def test_two_guidance_same_convention_proposes_claude_md(tmp_path):
    f = str(tmp_path / "d.json")
    for _ in range(2):
        run(["append", f, json.dumps(rec("Style", "use-result-type", "guidance"))])
    _, out = run(["analyze", f])
    prop = json.loads(out)["proposal"]
    assert prop is not None
    assert prop["target"] == "CLAUDE.md"
    assert prop["category"] == "use-result-type"


def test_one_guidance_below_threshold_null(tmp_path):
    f = str(tmp_path / "d.json")
    run(["append", f, json.dumps(rec("Style", "use-result-type", "guidance"))])
    _, out = run(["analyze", f])
    assert json.loads(out)["proposal"] is None


# --- 6. signal_hash stability + nudge-ack suppression ---

def test_signal_hash_stable_and_nudge_ack_suppresses(tmp_path):
    f = str(tmp_path / "d.json")
    for _ in range(3):
        run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    _, out1 = run(["analyze", f])
    h1 = json.loads(out1)["proposal"]["signal_hash"]
    _, out2 = run(["analyze", f])
    h2 = json.loads(out2)["proposal"]["signal_hash"]
    assert h1 == h2  # stable across runs

    _, out3 = run(["analyze", f, "--nudge-ack", h1])
    assert json.loads(out3)["proposal"] is None


def test_nudge_ack_among_several_hashes(tmp_path):
    f = str(tmp_path / "d.json")
    for _ in range(3):
        run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    _, out = run(["analyze", f])
    h = json.loads(out)["proposal"]["signal_hash"]
    _, out2 = run(["analyze", f, "--nudge-ack", "deadbeef," + h + ",cafef00d"])
    assert json.loads(out2)["proposal"] is None


# --- 7. corrupt / missing file: soft handling ---

def test_append_soft_handles_corrupt_file(tmp_path):
    f = str(tmp_path / "d.json")
    with open(f, "w") as fh:
        fh.write("{ this is not valid json ")
    code, _ = run(["append", f, json.dumps(rec("Security", "authz", "skip"))])
    assert code == 0
    store = read_store(f)  # must parse now
    assert store["schema"] == 1
    assert len(store["records"]) == 1


def test_analyze_missing_file_null(tmp_path):
    f = str(tmp_path / "does-not-exist.json")
    code, out = run(["analyze", f])
    assert code == 0
    assert json.loads(out)["proposal"] is None


def test_analyze_corrupt_file_null(tmp_path):
    f = str(tmp_path / "d.json")
    with open(f, "w") as fh:
        fh.write("not json at all")
    code, out = run(["analyze", f])
    assert code == 0
    assert json.loads(out)["proposal"] is None
