import json

import pytest

import engine


def _manifest(**over):
    m = {"schemaVersion": 1, "branch": "feat/x", "slot": None,
         "createdAt": "2026-06-11T00:00:00Z", "updatedAt": "2026-06-11T00:00:00Z",
         "scenarios": [
             {"id": "a", "block": "run-command",
              "config": {"command": ["true"], "targets": ["t"]}, "dependsOn": []},
             {"id": "b", "block": "run-command",
              "config": {"command": ["true"], "targets": ["t"]},
              "dependsOn": ["a"]},
         ]}
    m.update(over)
    return m


def _write(tmp_path, m, name="feat%2Fx.json"):
    p = str(tmp_path / name)
    json.dump(m, open(p, "w"))
    return p


def test_load_valid_manifest_and_topo_order(tmp_path):
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    assert m["branch"] == "feat/x"
    assert engine.topo_order(m["scenarios"]) == ["a", "b"]


def test_identity_from_json_never_filename(tmp_path):
    # File deliberately misnamed: JSON fields win.
    m = engine.load_manifest(_write(tmp_path, _manifest(), name="wrong-name.json"))
    assert m["branch"] == "feat/x" and m["slot"] is None


def test_unknown_schema_version_refused(tmp_path):
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, _manifest(schemaVersion=9)))
    assert "schemaVersion" in str(e.value)


def test_unreadable_manifest_fails_fast(tmp_path):
    p = str(tmp_path / "x.json")
    open(p, "w").write("{nope")
    with pytest.raises(engine.EngineError):
        engine.load_manifest(p)


def test_missing_branch_rejected(tmp_path):
    bad = _manifest()
    del bad["branch"]
    with pytest.raises(engine.EngineError):
        engine.load_manifest(_write(tmp_path, bad))


def test_duplicate_ids_rejected(tmp_path):
    bad = _manifest()
    bad["scenarios"].append(dict(bad["scenarios"][0]))
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "duplicate" in str(e.value)


def test_dangling_depends_on_is_structured_error(tmp_path):
    bad = _manifest()
    bad["scenarios"][1]["dependsOn"] = ["ghost"]
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "ghost" in str(e.value)


def test_cycle_is_structured_error_not_hang(tmp_path):
    bad = _manifest()
    bad["scenarios"][0]["dependsOn"] = ["b"]
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "cycle" in str(e.value).lower()


def test_plan_record_dangling_scenario_id(tmp_path):
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "branch": "feat/x", "slot": None,
           "createdAt": "2026-06-11T00:00:00Z",
           "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                      "scenarioIds": ["missing-id"]}]}
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    with pytest.raises(engine.EngineError) as e:
        engine.load_plan_record(p, m)
    assert "missing-id" in str(e.value)


def test_plan_record_valid(tmp_path):
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "branch": "feat/x", "slot": None,
           "createdAt": "2026-06-11T00:00:00Z",
           "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                      "scenarioIds": ["a"]}]}
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    assert engine.load_plan_record(p, m)["steps"][0]["id"] == "s1"


# r3-5-test-001(a): load_manifest rejects non-string and invalid slot fields.
def test_load_manifest_rejects_non_string_slot(tmp_path):
    bad = _manifest(slot=123)
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "non-string slot" in str(e.value)


def test_load_manifest_rejects_invalid_slot_pattern(tmp_path):
    bad = _manifest(slot="bad~slot")
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "invalid slot" in str(e.value)


# fl-code-code-002: non-dict scenario elements raise EngineError (not AttributeError).
def test_load_manifest_rejects_non_dict_scenario(tmp_path):
    bad = _manifest()
    bad["scenarios"] = ["a_string_not_a_dict"]
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "every scenario must be an object" in str(e.value)


# fl-code-code-002: non-dict step elements in plan record raise EngineError.
def test_load_plan_record_rejects_non_dict_step(tmp_path):
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "branch": "feat/x", "slot": None,
           "createdAt": "2026-06-11T00:00:00Z",
           "steps": ["a_string_not_a_dict"]}
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    with pytest.raises(engine.EngineError) as e:
        engine.load_plan_record(p, m)
    assert "every step must be an object" in str(e.value)


# r2-code-code-005: dependsOn must be a list of strings, not a string.
def test_depends_on_string_instead_of_list_is_error(tmp_path):
    bad = _manifest()
    bad["scenarios"][1]["dependsOn"] = "a"  # string, not list
    with pytest.raises(engine.EngineError) as e:
        engine.load_manifest(_write(tmp_path, bad))
    assert "dependsOn" in str(e.value)


# r2-code-code-005: scenarioIds must be a list of strings, not a string.
def test_scenario_ids_string_instead_of_list_is_error(tmp_path):
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "branch": "feat/x", "slot": None,
           "createdAt": "2026-06-11T00:00:00Z",
           "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                      "scenarioIds": "a"}]}  # string, not list
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    with pytest.raises(engine.EngineError) as e:
        engine.load_plan_record(p, m)
    assert "scenarioIds" in str(e.value)


# r3v-0-code-001: declared slot mismatch is rejected even when branch is absent from record.
def test_load_plan_record_rejects_slot_mismatch_when_branch_absent(tmp_path):
    """A plan record with no branch but a mismatched slot must raise EngineError."""
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "slot": "admin",
           "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                      "scenarioIds": ["a"]}]}
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    with pytest.raises(engine.EngineError) as e:
        engine.load_plan_record(p, m, branch="feat/x", slot="qa")
    assert "slot" in str(e.value)


# r4v-0-code-001: slot check is STRICT — a record that declares slot must match
# even when the caller's slot is None.
def test_load_plan_record_rejects_declared_slot_when_caller_slot_is_none(tmp_path):
    """Record declares slot='qa'; caller passes slot=None -> EngineError mentioning slot."""
    m = engine.load_manifest(_write(tmp_path, _manifest()))
    rec = {"schemaVersion": 1, "slot": "qa",
           "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                      "scenarioIds": ["a"]}]}
    p = str(tmp_path / "feat%2Fx.plan.json")
    json.dump(rec, open(p, "w"))
    with pytest.raises(engine.EngineError) as e:
        engine.load_plan_record(p, m, slot=None)
    assert "slot" in str(e.value)
