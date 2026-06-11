import json
import os
import subprocess
import textwrap

import engine
import store


LIB = os.path.dirname(os.path.abspath(engine.__file__))


def _setup_repo(tmp_path):
    """Git repo + global-store profile/manifest so the CLI resolves paths."""
    repo = str(tmp_path / "repo")
    subprocess.run(["git", "init", "-q", repo], check=True)
    root = str(tmp_path / "store")
    env = dict(os.environ, TEST_PILOT_STORE_ROOT=root)
    c = store.create(repo, "global", root)
    open(c["profile"], "w").write(textwrap.dedent("""\
        # profile

        ```json test-pilot-config
        {"schemaVersion": 1, "protectedTargets": ["main"]}
        ```
        """))
    marker = os.path.join(repo, "seeded.marker")
    manifest = {
        "schemaVersion": 1, "branch": "feat/x", "slot": None,
        "createdAt": "2026-06-11T00:00:00Z", "updatedAt": "2026-06-11T00:00:00Z",
        "scenarios": [{
            "id": "a", "block": "run-command",
            "config": {"command": ["python3", "-c",
                                   f"open({marker!r}, 'w').write('x')"],
                       "cleanCommand": ["python3", "-c",
                                        f"import os; os.unlink({marker!r})"],
                       "targets": ["test-db"]},
            "dependsOn": []}]}
    key = store.artifact_key("feat/x")
    json.dump(manifest, open(os.path.join(c["manifests_dir"], f"{key}.json"), "w"))
    return repo, env, marker


def _cli(repo, env, *args):
    return subprocess.run(
        ["python3", os.path.join(LIB, "engine.py"), *args, "--json"],
        capture_output=True, text=True, cwd=repo, env=env)


def test_apply_success_shape(tmp_path):
    repo, env, marker = _setup_repo(tmp_path)
    r = _cli(repo, env, "apply", "--branch", "feat/x")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["command"] == "apply"
    assert out["applied"] == ["a"] and out["key"] == "feat%2Fx"
    assert os.path.exists(marker)


def test_status_and_clean_shapes(tmp_path):
    repo, env, marker = _setup_repo(tmp_path)
    _cli(repo, env, "apply", "--branch", "feat/x")
    out = json.loads(_cli(repo, env, "status").stdout)
    assert out["entries"][0]["branch"] == "feat/x"
    out = json.loads(_cli(repo, env, "clean", "--branch", "feat/x").stdout)
    assert out["cleaned"] == ["a"]
    assert not os.path.exists(marker)


def test_error_shape_names_block_and_scenario(tmp_path):
    repo, env, _ = _setup_repo(tmp_path)
    # break the manifest: protected target without --allow-protected
    root = env["TEST_PILOT_STORE_ROOT"]
    c = store.create(repo, "global", root)
    key = store.artifact_key("feat/x")
    mp = os.path.join(c["manifests_dir"], f"{key}.json")
    m = json.load(open(mp))
    m["scenarios"][0]["config"]["targets"] = ["main"]
    json.dump(m, open(mp, "w"))
    r = _cli(repo, env, "apply", "--branch", "feat/x")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["command"] == "apply"
    assert "protected" in out["error"]
    assert out["scenarioId"] == "a"
    assert "block" in out  # null is fine; the key must exist


def test_dry_run_flag(tmp_path):
    repo, env, marker = _setup_repo(tmp_path)
    r = _cli(repo, env, "apply", "--branch", "feat/x", "--dry-run")
    out = json.loads(r.stdout)
    assert out["dryRun"] is True and out["wouldApply"] == ["a"]
    assert not os.path.exists(marker)


def test_unlock_shape(tmp_path):
    repo, env, _ = _setup_repo(tmp_path)
    out = json.loads(_cli(repo, env, "unlock").stdout)
    assert out == {"ok": True, "command": "unlock", "released": False,
                   "holder": None}
