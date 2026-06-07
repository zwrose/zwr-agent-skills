import json
import os
import subprocess
import sys

import pytest

import review_store as rs


@pytest.mark.parametrize("url,expected", [
    ("git@github.com:org/repo.git", "github.com/org/repo"),
    ("https://github.com/org/repo.git", "github.com/org/repo"),
    ("https://user@github.com/org/repo/", "github.com/org/repo"),
    ("ssh://git@github.com:22/org/repo.git", "github.com/org/repo"),
    ("https://GitHub.com/Org/Repo.git", "github.com/Org/Repo"),
    ("", None),
    (None, None),
])
def test_normalize_remote(url, expected):
    assert rs.normalize_remote(url) == expected


def test_short_hash_is_stable_16_hex():
    h = rs.short_hash("github.com/org/repo")
    assert h == rs.short_hash("github.com/org/repo")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
    assert rs.short_hash("a") != rs.short_hash("b")


def _git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True,
                   capture_output=True, text=True)


def _init_repo(path, remote=None):
    path = str(path)
    _git(path, "init", "-q") if False else subprocess.run(
        ["git", "init", "-q", path], check=True, capture_output=True, text=True)
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


def test_derive_identifiers_with_remote(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:org/repo.git")
    ident = rs.derive_identifiers(repo)
    assert ident["remote"] == "github.com/org/repo"
    assert ident["remote_hash"] == rs.short_hash("github.com/org/repo")
    assert os.path.isabs(ident["gitdir"])
    assert ident["gitdir"] == os.path.realpath(ident["gitdir"])
    assert ident["gitdir_hash"] == rs.short_hash(ident["gitdir"])


def test_derive_identifiers_no_remote(tmp_path):
    repo = _init_repo(tmp_path / "r")
    ident = rs.derive_identifiers(repo)
    assert ident["remote"] is None
    assert ident["remote_hash"] is None
    assert ident["gitdir_hash"]  # always present


def test_get_gitdir_non_git_falls_back_to_cwd(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert rs.get_gitdir(str(d)) == os.path.realpath(str(d))


def test_worktrees_collapse_to_one_gitdir(tmp_path):
    repo = _init_repo(tmp_path / "main", remote=None)
    # need one commit to add a worktree
    (tmp_path / "main" / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-qm", "init")
    wt = str(tmp_path / "wt")
    _git(repo, "worktree", "add", "-q", wt)
    assert rs.get_gitdir(repo) == rs.get_gitdir(wt)


def test_store_root_is_realpathed(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = rs.store_root()
    assert root == os.path.realpath(root)
    assert root.endswith(os.path.join(".claude", "review-crew"))


def test_pointer_round_trip(tmp_path):
    root = str(tmp_path / "store")
    assert rs.read_pointer(root, "abc123") is None
    rs.write_pointer(root, "abc123", "entry-xyz")
    assert rs.read_pointer(root, "abc123") == "entry-xyz"
    # overwrite is atomic + last-write-wins for a single key
    rs.write_pointer(root, "abc123", "entry-2")
    assert rs.read_pointer(root, "abc123") == "entry-2"


def _register(root, ident, entry_id):
    """Manually register an entry's pointers + keys.json for test setup."""
    entry_dir = os.path.join(root, "entries", entry_id)
    os.makedirs(entry_dir, exist_ok=True)
    rs._write_keys_json(entry_dir, ident)
    rs.write_pointer(root, ident["gitdir_hash"], entry_id)
    if ident["remote_hash"]:
        rs.write_pointer(root, ident["remote_hash"], entry_id)


def test_resolve_global_both_pointers_equal(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    _register(root, ident, ident["gitdir_hash"])
    g = rs.resolve_global(repo, root)
    assert g["entry_id"] == ident["gitdir_hash"]
    assert g["healed"] is False


def test_resolve_global_none_when_unregistered(tmp_path):
    repo = _init_repo(tmp_path / "r")
    assert rs.resolve_global(repo, str(tmp_path / "store")) is None


def test_self_heal_when_gitdir_pointer_missing(tmp_path):
    # remote pointer present, gitdir pointer absent -> heal writes gitdir pointer
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    eid = ident["gitdir_hash"]
    os.makedirs(os.path.join(root, "entries", eid), exist_ok=True)
    rs._write_keys_json(os.path.join(root, "entries", eid), ident)
    rs.write_pointer(root, ident["remote_hash"], eid)   # only remote pointer
    g = rs.resolve_global(repo, root)
    assert g["entry_id"] == eid
    assert g["healed"] is True
    assert rs.read_pointer(root, ident["gitdir_hash"]) == eid  # healed


def test_self_heal_when_remote_pointer_missing(tmp_path):
    # gitdir pointer present, remote now exists but its pointer absent -> heal
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    eid = ident["gitdir_hash"]
    os.makedirs(os.path.join(root, "entries", eid), exist_ok=True)
    rs._write_keys_json(os.path.join(root, "entries", eid), ident)
    rs.write_pointer(root, ident["gitdir_hash"], eid)   # only gitdir pointer
    g = rs.resolve_global(repo, root)
    assert g["healed"] is True
    assert rs.read_pointer(root, ident["remote_hash"]) == eid  # healed


def test_disagreement_prefers_remote(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    rs.write_pointer(root, ident["remote_hash"], "entry-REMOTE")
    rs.write_pointer(root, ident["gitdir_hash"], "entry-GITDIR")
    os.makedirs(os.path.join(root, "entries", "entry-REMOTE"), exist_ok=True)
    g = rs.resolve_global(repo, root)
    assert g["entry_id"] == "entry-REMOTE"
    assert g["healed"] is True
    assert rs.read_pointer(root, ident["gitdir_hash"]) == "entry-REMOTE"  # re-pointed
