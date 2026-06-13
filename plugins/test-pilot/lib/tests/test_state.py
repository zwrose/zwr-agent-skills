import json
import os

import pytest

import state


def test_missing_file_yields_fresh_state(tmp_path):
    s = state.load_state(str(tmp_path / "state.json"))
    assert s == {"schemaVersion": 1, "manifests": {}}


def test_round_trip(tmp_path):
    p = str(tmp_path / "state.json")
    s = state.fresh_state()
    s["manifests"]["feat%2Fx"] = {"branch": "feat/x", "slot": None,
                                  "applyOrder": [], "scenarios": {}}
    state.save_state(p, s)
    assert state.load_state(p) == s


def test_corrupt_file_is_structured_error_naming_file(tmp_path):
    p = str(tmp_path / "state.json")
    open(p, "w").write("{truncated")
    with pytest.raises(state.StateError) as e:
        state.load_state(p)
    assert p in str(e.value)            # names the file
    assert "re-run" in str(e.value)     # carries a recovery hint
    assert open(p).read() == "{truncated"  # no silent reset


def test_unknown_schema_version_refused(tmp_path):
    p = str(tmp_path / "state.json")
    json.dump({"schemaVersion": 99, "manifests": {}}, open(p, "w"))
    with pytest.raises(state.StateError) as e:
        state.load_state(p)
    assert "schemaVersion" in str(e.value)
