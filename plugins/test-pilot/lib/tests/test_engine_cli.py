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


# Fix 5: validate-plan subcommand.
def _setup_repo_with_plan(tmp_path, steps):
    """Like _setup_repo but also writes a plan record JSON next to the manifest."""
    repo, env, marker = _setup_repo(tmp_path)
    root = env["TEST_PILOT_STORE_ROOT"]
    c = store.create(repo, "global", root)
    key = store.artifact_key("feat/x")
    plan_path = os.path.join(c["manifests_dir"], f"{key}.plan.json")
    json.dump({"schemaVersion": 1, "steps": steps}, open(plan_path, "w"))
    return repo, env, marker, key


def test_validate_plan_ok(tmp_path):
    steps = [{"id": "s1", "instruction": "Open the page", "expected": "Page loads",
               "scenarioIds": ["a"]}]
    repo, env, _, key = _setup_repo_with_plan(tmp_path, steps)
    r = _cli(repo, env, "validate-plan", "--branch", "feat/x")
    assert r.returncode == 0, r.stdout
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["command"] == "validate-plan"
    assert out["steps"] == 1 and out["key"] == key


def test_validate_plan_dangling_scenario_id(tmp_path):
    # Step references scenario "z" which does not exist in the manifest.
    steps = [{"id": "s1", "instruction": "Do something", "expected": "Works",
               "scenarioIds": ["z"]}]
    repo, env, _, key = _setup_repo_with_plan(tmp_path, steps)
    r = _cli(repo, env, "validate-plan", "--branch", "feat/x")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert "missing" in out["error"]


# fl-code-code-003: validate-plan must apply the same branch/slot identity check
# as apply_manifest — a manifest with wrong JSON branch must be rejected.
def test_validate_plan_rejects_mismatched_branch(tmp_path):
    repo, env, _, key = _setup_repo_with_plan(
        tmp_path, [{"id": "s1", "instruction": "x", "expected": "y",
                    "scenarioIds": ["a"]}])
    root = env["TEST_PILOT_STORE_ROOT"]
    c = store.create(repo, "global", root)
    mp = os.path.join(c["manifests_dir"], f"{key}.json")
    m = json.load(open(mp))
    # Corrupt the JSON to declare a different branch.
    m["branch"] = "wrong-branch"
    json.dump(m, open(mp, "w"))
    r = _cli(repo, env, "validate-plan", "--branch", "feat/x")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert "identity" in out["error"] or "declares branch" in out["error"]


# r2v-code-code-003: validate-plan must reject a plan record whose declared
# branch/slot fields disagree with the requested pair.
def test_validate_plan_rejects_mismatched_plan_record_branch(tmp_path):
    """Plan record declares wrong branch -> EngineError with identity message."""
    repo, env, _, key = _setup_repo_with_plan(
        tmp_path, [{"id": "s1", "instruction": "x", "expected": "y",
                    "scenarioIds": ["a"]}])
    root = env["TEST_PILOT_STORE_ROOT"]
    c = store.create(repo, "global", root)
    plan_path = os.path.join(c["manifests_dir"], f"{key}.plan.json")
    rec = json.load(open(plan_path))
    # Inject a mismatched branch into the plan record.
    rec["branch"] = "wrong-branch"
    rec["slot"] = None
    json.dump(rec, open(plan_path, "w"))
    r = _cli(repo, env, "validate-plan", "--branch", "feat/x")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert "identity" in out["error"] or "declares branch" in out["error"]


# r2-test-test-003: invalid slot must produce structured JSON error (exit 1).
def test_invalid_slot_produces_json_error(tmp_path):
    repo, env, _ = _setup_repo(tmp_path)
    r = _cli(repo, env, "apply", "--branch", "feat/x", "--slot", "bad~slot")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert out["command"] == "apply"
    assert "slot" in out["error"].lower()


# r3v-4-test-002: validate-plan must reject a plan record whose declared slot disagrees
# with the requested slot, even when the branch matches.
def test_validate_plan_rejects_mismatched_plan_record_slot(tmp_path):
    """Plan record declares wrong slot (branch correct) -> EngineError mentioning slot."""
    repo, env, _, key = _setup_repo_with_plan(
        tmp_path, [{"id": "s1", "instruction": "x", "expected": "y",
                    "scenarioIds": ["a"]}])
    # Need matching manifest+plan key for slot=qa; write them for the qa slot.
    root = env["TEST_PILOT_STORE_ROOT"]
    c = store.create(repo, "global", root)
    qa_key = store.artifact_key("feat/x", "qa")
    # Copy the manifest to the qa-slotted path.
    orig_mp = os.path.join(c["manifests_dir"], f"{key}.json")
    m = json.load(open(orig_mp))
    m["slot"] = "qa"
    qa_mp = os.path.join(c["manifests_dir"], f"{qa_key}.json")
    json.dump(m, open(qa_mp, "w"))
    # Write the plan record with wrong slot.
    qa_plan_path = os.path.join(c["manifests_dir"], f"{qa_key}.plan.json")
    qa_rec = {"schemaVersion": 1, "branch": "feat/x", "slot": "other",
              "steps": [{"id": "s1", "instruction": "x", "expected": "y",
                         "scenarioIds": ["a"]}]}
    json.dump(qa_rec, open(qa_plan_path, "w"))
    r = _cli(repo, env, "validate-plan", "--branch", "feat/x", "--slot", "qa")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert "slot" in out["error"]
