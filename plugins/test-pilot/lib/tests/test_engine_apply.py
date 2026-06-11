import json
import os

import pytest

import engine
import lock as lock_mod
import state as state_mod
import store


def _paths(tmp_path):
    sd = str(tmp_path / "state")
    md = str(tmp_path / "manifests")
    os.makedirs(sd, exist_ok=True)
    os.makedirs(md, exist_ok=True)
    return {"state_dir": sd, "manifests_dir": md,
            "blocks_dir": str(tmp_path / "blocks"), "repo_root": str(tmp_path)}


def _manifest(scenarios, branch="feat/x", slot=None):
    return {"schemaVersion": 1, "branch": branch, "slot": slot,
            "createdAt": "2026-06-11T00:00:00Z",
            "updatedAt": "2026-06-11T00:00:00Z", "scenarios": scenarios}


def _sc(sid, marker_dir, deps=(), fail=False, payload="x"):
    """run-command scenario that writes a marker file on apply and deletes it
    on clean — lets tests observe real side effects."""
    marker = os.path.join(marker_dir, f"{sid}.marker")
    apply_py = (f"import sys; sys.exit(3)" if fail else
                f"open({marker!r}, 'w').write({payload!r})")
    return {"id": sid, "block": "run-command",
            "config": {"command": ["python3", "-c", apply_py],
                       "cleanCommand": ["python3", "-c",
                                        f"import os; os.path.exists({marker!r}) and os.unlink({marker!r})"],
                       "targets": ["test-db"]},
            "dependsOn": list(deps)}


def _write_manifest(paths, m):
    key = store.artifact_key(m["branch"], m["slot"])
    p = os.path.join(paths["manifests_dir"], f"{key}.json")
    json.dump(m, open(p, "w"))
    return p


def test_apply_then_idempotent_reapply(tmp_path):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path), deps=["a"])])
    _write_manifest(paths, m)
    r1 = engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert r1["applied"] == ["a", "b"] and r1["cleaned"] == []
    assert os.path.exists(os.path.join(str(tmp_path), "a.marker"))
    # idempotent no-op
    r2 = engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert r2["applied"] == [] and r2["skipped"] == ["a", "b"]


def test_changed_config_dirties_transitively(tmp_path):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path), deps=["a"])])
    _write_manifest(paths, m)
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    m2 = _manifest([_sc("a", str(tmp_path), payload="CHANGED"),
                    _sc("b", str(tmp_path), deps=["a"])])
    _write_manifest(paths, m2)
    r = engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    # b depends on dirty a -> cleaned (reverse order) and re-applied
    assert r["cleaned"] == ["b", "a"]
    assert r["applied"] == ["a", "b"]


def test_removed_scenario_cleaned(tmp_path):
    paths = _paths(tmp_path)
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path)),
                                      _sc("b", str(tmp_path))]))
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    r = engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert r["cleaned"] == ["b"] and r["applied"] == []
    assert not os.path.exists(os.path.join(str(tmp_path), "b.marker"))


def test_partial_failure_is_transactional_per_scenario(tmp_path):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path)),
                   _sc("boom", str(tmp_path), deps=["a"], fail=True)])
    _write_manifest(paths, m)
    with pytest.raises(engine.EngineError) as e:
        engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert e.value.payload["scenarioId"] == "boom"
    assert e.value.payload["block"] == "run-command"
    # prior success IS recorded in state — nothing half-seeded untracked
    st = state_mod.load_state(os.path.join(paths["state_dir"], "state.json"))
    key = store.artifact_key("feat/x")
    assert "a" in st["manifests"][key]["scenarios"]
    assert "boom" not in st["manifests"][key]["scenarios"]
    # and the lock was released on the failure path
    assert not os.path.exists(os.path.join(paths["state_dir"], "engine.lock"))


def test_gate_refuses_and_allow_protected_overrides(tmp_path, capsys):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path))])
    m["scenarios"][0]["config"]["targets"] = ["main"]
    _write_manifest(paths, m)
    with pytest.raises(engine.EngineError) as e:
        engine.apply_manifest(paths, "feat/x", None,
                              {"protectedTargets": ["main"]},
                              allow_protected=False)
    assert "protected" in str(e.value)
    r = engine.apply_manifest(paths, "feat/x", None,
                              {"protectedTargets": ["main"]},
                              allow_protected=True)
    assert r["allowProtectedUsed"] is True
    assert "allow-protected" in capsys.readouterr().err.lower()


def test_dry_run_validates_without_applying(tmp_path):
    paths = _paths(tmp_path)
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    r = engine.apply_manifest(paths, "feat/x", None, {},
                              allow_protected=False, dry_run=True)
    assert r["dryRun"] is True and r["wouldApply"] == ["a"]
    assert not os.path.exists(os.path.join(str(tmp_path), "a.marker"))


def test_apply_refuses_while_locked(tmp_path):
    paths = _paths(tmp_path)
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    lock_mod.acquire(os.path.join(paths["state_dir"], "engine.lock"))
    with pytest.raises(engine.EngineError) as e:
        engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert "lock" in str(e.value).lower()


def test_clean_and_status_and_orphans(tmp_path):
    paths = _paths(tmp_path)
    mp = _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    s = engine.status(paths)
    assert s["entries"][0]["key"] == "feat%2Fx"
    assert s["entries"][0]["applied"] == 1
    assert s["entries"][0]["orphan"] is False
    # delete the manifest file -> entry becomes an orphan, still cleanable
    os.unlink(mp)
    s = engine.status(paths)
    assert s["entries"][0]["orphan"] is True
    r = engine.clean_manifest(paths, "feat/x", None)
    assert r["cleaned"] == ["a"]
    assert not os.path.exists(os.path.join(str(tmp_path), "a.marker"))
    assert engine.status(paths)["entries"] == []


def test_unlock_releases_stale_lock(tmp_path):
    """Lock held by a dead pid is genuinely stale; unlock releases it."""
    import json as _json
    lp = os.path.join(tmp_path / "state", "engine.lock")
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    # Write a lock file with a pid that cannot possibly be alive.
    import socket as _socket
    dead_holder = {"pid": 2**30, "host": _socket.gethostname(),
                   "acquiredAt": "2020-01-01T00:00:00Z"}
    with open(lp, "w") as fh:
        _json.dump(dead_holder, fh)
    assert lock_mod.is_stale(lp), "lock should be stale before unlock"
    paths = _paths(tmp_path)
    r = engine.unlock(paths)
    assert r["released"] is True
    assert not os.path.exists(lp)


# Fix 1 regression: state must be loaded under the lock.
def test_state_is_read_under_the_lock(tmp_path, monkeypatch):
    """Regression: a concurrent applier must not snapshot state pre-lock."""
    paths = _paths(tmp_path)
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    real_load = engine.state.load_state
    seen = {}

    def spying_load(path):
        seen["lock_exists_at_load"] = os.path.exists(
            os.path.join(paths["state_dir"], "engine.lock"))
        return real_load(path)

    monkeypatch.setattr(engine.state, "load_state", spying_load)
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert seen["lock_exists_at_load"] is True


# Fix 2: clean_manifest must refuse protected targets.
def test_clean_refuses_protected_targets_from_state(tmp_path, capsys):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path))])
    m["scenarios"][0]["config"]["targets"] = ["main"]
    _write_manifest(paths, m)
    cfg = {"protectedTargets": ["main"]}
    engine.apply_manifest(paths, "feat/x", None, cfg, allow_protected=True)
    capsys.readouterr()
    with pytest.raises(engine.EngineError) as e:
        engine.clean_manifest(paths, "feat/x", None, cfg)
    assert "protected" in str(e.value)
    r = engine.clean_manifest(paths, "feat/x", None, cfg, allow_protected=True)
    assert r["cleaned"] == ["a"]
    assert "allow-protected" in capsys.readouterr().err.lower()


# Fix 2: apply_manifest must gate removed scenarios being cleaned.
def test_apply_gates_removed_scenarios_being_cleaned(tmp_path, capsys):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path))])
    m["scenarios"][1]["config"]["targets"] = ["main"]
    _write_manifest(paths, m)
    cfg = {"protectedTargets": ["main"]}
    engine.apply_manifest(paths, "feat/x", None, cfg, allow_protected=True)
    capsys.readouterr()
    # remove the protected scenario from the manifest -> it lands in to_clean
    _write_manifest(paths, _manifest([_sc("a", str(tmp_path))]))
    with pytest.raises(engine.EngineError) as e:
        engine.apply_manifest(paths, "feat/x", None, cfg, allow_protected=False)
    assert "protected" in str(e.value)
    # allow_protected=True -> runs through, warning on stderr
    r = engine.apply_manifest(paths, "feat/x", None, cfg, allow_protected=True)
    assert r["cleaned"] == ["b"]
    assert "cleaning protected targets" in capsys.readouterr().err


# r2-test-test-001: dependsOn-only change must dirty the scenario (and dependents).
def test_depends_on_only_change_dirties_scenario(tmp_path):
    paths = _paths(tmp_path)
    # Initial apply: a and b, b has no deps
    m1 = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path))])
    _write_manifest(paths, m1)
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    # Re-apply with b's dependsOn set to ["a"], IDENTICAL config
    m2 = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path), deps=["a"])])
    _write_manifest(paths, m2)
    r = engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    assert r["cleaned"] == ["b"]
    assert "b" in r["applied"]
    assert "a" not in r["cleaned"]


# r2-test-test-004: status drift must be non-empty after a config change (no re-apply).
def test_status_drift_non_empty_after_config_change(tmp_path):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path))])
    _write_manifest(paths, m)
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    # Overwrite the manifest with a changed config — do NOT re-apply
    m2 = _manifest([_sc("a", str(tmp_path), payload="CHANGED")])
    _write_manifest(paths, m2)
    s = engine.status(paths)
    assert s["entries"][0]["drift"] == ["a"]


# r2-test-test-004 (continued): status drift also fires on dependsOn-only change.
def test_status_drift_non_empty_after_depends_on_change(tmp_path):
    paths = _paths(tmp_path)
    m = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path))])
    _write_manifest(paths, m)
    engine.apply_manifest(paths, "feat/x", None, {}, allow_protected=False)
    # Change only b's dependsOn — do NOT re-apply
    m2 = _manifest([_sc("a", str(tmp_path)), _sc("b", str(tmp_path), deps=["a"])])
    _write_manifest(paths, m2)
    s = engine.status(paths)
    assert "b" in s["entries"][0]["drift"]
