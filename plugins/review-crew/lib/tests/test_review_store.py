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
