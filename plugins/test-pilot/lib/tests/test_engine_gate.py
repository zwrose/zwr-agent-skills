import textwrap

import pytest

import engine


def _profile(tmp_path, protected):
    p = str(tmp_path / "profile.md")
    open(p, "w").write(textwrap.dedent(f"""\
        # test-pilot profile

        prose here.

        ```json test-pilot-config
        {{
          "schemaVersion": 1,
          "baseUrl": "http://localhost:3000",
          "dbEnvVar": "MONGODB_URI",
          "apiBase": "http://localhost:3000/api",
          "protectedTargets": {protected},
          "browserTools": ["chrome-devtools"]
        }}
        ```
        """))
    return p


def test_load_profile_config(tmp_path):
    cfg = engine.load_profile_config(_profile(tmp_path, '["main"]'))
    assert cfg["protectedTargets"] == ["main"]


def test_profile_without_config_block_is_error(tmp_path):
    p = str(tmp_path / "profile.md")
    open(p, "w").write("# no block here\n")
    with pytest.raises(engine.EngineError) as e:
        engine.load_profile_config(p)
    assert "test-pilot-init" in str(e.value)


def test_bare_name():
    assert engine.bare_name("mongodb://localhost:27017/main") == "main"
    assert engine.bare_name("mongodb://h/db?retryWrites=true") == "db"
    assert engine.bare_name("plain-db-name") == "plain-db-name"
    assert engine.bare_name("http://h/api/") == "api"


def _scenario(target):
    return {"id": "s1", "block": "run-command",
            "config": {"command": ["true"], "targets": [target]},
            "dependsOn": []}


def test_gate_must_refuse_uri_whose_bare_name_matches():
    # Spec-pinned direction: protected `main` vs full URI MUST refuse.
    hits = engine.gate_violations(
        [_scenario("mongodb://localhost:27017/main")], {}, ["main"])
    assert hits == [("s1", "mongodb://localhost:27017/main", "main")]


def test_gate_true_near_miss_must_pass():
    # Spec-pinned direction: `maintenance` is NOT `main`.
    assert engine.gate_violations(
        [_scenario("mongodb://localhost:27017/maintenance")], {}, ["main"]) == []


def test_gate_glob_and_case_sensitivity():
    assert engine.gate_violations(
        [_scenario("mongodb://prod-host/app")], {}, ["mongodb://prod-*/*"])
    assert engine.gate_violations([_scenario("MAIN")], {}, ["main"]) == []


def test_gate_empty_patterns_never_refuses():
    assert engine.gate_violations([_scenario("anything")], {}, []) == []
    assert engine.gate_violations([_scenario("anything")], {}, None) == []
