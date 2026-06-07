"""Tests for repo_doctor.py — staleness + degraded-path self-check.

Hermetic: each test builds its own --root tmp dir (with a fake verify binary and,
where needed, a real `git init` tmp repo) and writes a minimal profile provenance
block. Nothing depends on the real review-crew repo's deps or git state.
"""
import json
import os
import shutil
import subprocess

import pytest

import repo_doctor


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def write_profile(
    tmp_path,
    *,
    schema=repo_doctor.SUPPORTED_SCHEMA,
    plugin="review-crew@0.1.0",
    rubric_version=2,
    nudge_ack="{}",
    dep_set=None,
    default_branch="main",
    forge="github",
):
    """Write a minimal review-profile.md provenance block and return its path."""
    if dep_set is None:
        dep_set = []
    deps = ", ".join(dep_set)
    body = (
        "<!-- review-profile · managed by review-crew · schema {schema} -->\n"
        "<!-- provenance -->\n"
        "schema: {schema}\n"
        "plugin: {plugin}\n"
        "rubric-version: {rubric}\n"
        "generated: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "status: stable\n"
        "nudge-ack: {nudge}\n"
        "signals:\n"
        "  dep-set: [{deps}]\n"
        "  default-branch: {branch}\n"
        "  forge: {forge}\n"
        "<!-- end provenance -->\n"
        "\n## Verify\ncommand: {verify}\n"
    ).format(
        schema=schema,
        plugin=plugin,
        rubric=rubric_version,
        nudge=nudge_ack,
        deps=deps,
        branch=default_branch,
        forge=forge,
        verify="verifybin --run",
    )
    p = tmp_path / "review-profile.md"
    p.write_text(body)
    return str(p)


def make_root(tmp_path, *, src_dirs=None, deps=None, verify_present=True,
              git_branch="main"):
    """Build a fake project root.

    - creates src dirs
    - writes a package.json with the given top-level deps
    - puts a fake `verifybin` on a local bin dir and prepends it to PATH (caller
      passes the returned env into run())
    - optionally `git init` with a real branch so default-branch resolves
    """
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    for d in (src_dirs or []):
        (root / d).mkdir(parents=True, exist_ok=True)

    if deps is not None:
        pkg = {"dependencies": {name: "1.0.0" for name in deps}}
        (root / "package.json").write_text(json.dumps(pkg))

    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    if verify_present:
        vb = bindir / "verifybin"
        vb.write_text("#!/bin/sh\nexit 0\n")
        vb.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")

    if git_branch:
        subprocess.run(["git", "init", "-q", "-b", git_branch, str(root)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q",
                        "--allow-empty", "-m", "init"],
                       check=True, capture_output=True)
    return str(root), env


def run(profile, plugin_ver, rubric_ver, root, env, capsys):
    argv = ["repo_doctor.py", profile, plugin_ver, str(rubric_ver),
            "--root", root]
    rc = repo_doctor.main(argv, env=env)
    out = capsys.readouterr().out
    return rc, json.loads(out)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_clean_profile_no_drift(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a", "b"])
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a", "b"],
                            default_branch="main")
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert res["ok"] is True
    assert res["readable"] is True
    assert res["drift"] == []
    assert res["message"] is None
    assert res["signal_hash"] == ""


def test_rubric_version_behind(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 3, root, env, capsys)
    assert rc == 0
    assert any("rubric-version" in d for d in res["drift"])
    assert res["message"] is not None
    assert res["signal_hash"] != ""


def test_schema_behind(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    profile = write_profile(tmp_path, schema=repo_doctor.SUPPORTED_SCHEMA - 1,
                            rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert any("schema" in d for d in res["drift"])


def test_three_added_deps_drifts(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"],
                          deps=["a", "b", "c", "d"])
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert any("dep" in d.lower() for d in res["drift"])


def test_two_added_deps_no_drift(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a", "b", "c"])
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert not any("dep" in d.lower() for d in res["drift"])


def test_new_src_dir_drifts(tmp_path, capsys):
    # live has src+lib; profile detection only knew src (no lib dir back then →
    # we model "prior" by the profile not carrying lib; new dir present live).
    root, env = make_root(tmp_path, src_dirs=["src", "lib"], deps=["a"])
    # Profile written when only `src` existed: encode prior src-dirs in profile.
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    # Remove lib from "prior" by writing a marker file the doctor reads? Instead
    # we rely on the doctor comparing live src-dirs against a recorded set; since
    # the minimal profile records none, we assert that adding a brand-new dir not
    # previously seen drifts. To make this meaningful, the profile DOES record
    # src-dirs; patch it to include only src.
    txt = open(profile).read().replace(
        "  forge: github\n",
        "  forge: github\n  src-dirs: [src]\n",
    )
    open(profile, "w").write(txt)
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert any("src" in d.lower() or "dir" in d.lower() for d in res["drift"])


def test_verify_binary_absent_drifts(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"],
                          verify_present=False)
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert any("verify" in d.lower() for d in res["drift"])


def test_default_branch_unresolvable_drifts(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"],
                          git_branch="main")
    # profile claims default-branch "release" which does not exist in the repo
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"],
                            default_branch="release")
    rc, res = run(profile, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert any("branch" in d.lower() for d in res["drift"])


def test_missing_profile_soft_fail(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    missing = str(tmp_path / "does-not-exist.md")
    rc, res = run(missing, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert res["ok"] is False
    assert res["readable"] is False
    assert res["drift"] == []
    assert res["signal_hash"] == ""
    assert res["nudge_acked"] is False
    assert "unreadable" in res["message"]


def test_unparseable_profile_soft_fail(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    bad = tmp_path / "bad.md"
    bad.write_text("this is not a provenance block at all\n")
    rc, res = run(str(bad), "review-crew@0.1.0", 2, root, env, capsys)
    assert rc == 0
    assert res["readable"] is False
    assert res["ok"] is False


def test_nudge_acked_true(tmp_path, capsys):
    # First run with drift to learn the signal_hash, then bake it into nudge-ack.
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc, res = run(profile, "review-crew@0.1.0", 3, root, env, capsys)
    h = res["signal_hash"]
    assert h != ""
    assert res["nudge_acked"] is False

    profile2 = write_profile(tmp_path, rubric_version=2, dep_set=["a"],
                             nudge_ack="{%r: true}" % h)
    rc2, res2 = run(profile2, "review-crew@0.1.0", 3, root, env, capsys)
    assert rc2 == 0
    assert res2["signal_hash"] == h
    assert res2["nudge_acked"] is True


def test_signal_hash_stable_and_empty(tmp_path, capsys):
    root, env = make_root(tmp_path, src_dirs=["src"], deps=["a"])
    drift_profile = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rc1, r1 = run(drift_profile, "review-crew@0.1.0", 3, root, env, capsys)
    rc2, r2 = run(drift_profile, "review-crew@0.1.0", 3, root, env, capsys)
    assert r1["signal_hash"] == r2["signal_hash"] != ""

    clean = write_profile(tmp_path, rubric_version=2, dep_set=["a"])
    rcc, rc_res = run(clean, "review-crew@0.1.0", 2, root, env, capsys)
    assert rc_res["drift"] == []
    assert rc_res["signal_hash"] == ""
