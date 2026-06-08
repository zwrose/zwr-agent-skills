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
    subprocess.run(["git", "init", "-q", path], check=True,
                   capture_output=True, text=True)
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


def test_create_global_registers_both_pointers_and_keys(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    path = rs.create(repo, "profile", "global", root)
    ident = rs.derive_identifiers(repo)
    eid = ident["gitdir_hash"]
    assert path == os.path.join(root, "entries", eid, "review-profile.md")
    assert rs.read_pointer(root, ident["gitdir_hash"]) == eid
    assert rs.read_pointer(root, ident["remote_hash"]) == eid
    keys = json.load(open(os.path.join(root, "entries", eid, "keys.json")))
    assert keys["remote"] == "github.com/o/p"
    assert keys["gitdir_hash"] == eid


def test_create_global_is_non_clobbering(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    path = rs.create(repo, "profile", "global", root)
    with open(path, "w") as fh:
        fh.write("MY PROFILE")
    # second create must reuse the entry and NOT overwrite the profile
    again = rs.create(repo, "profile", "global", root)
    assert again == path
    assert open(path).read() == "MY PROFILE"


def test_create_in_repo_returns_dot_claude_and_mints_no_global(tmp_path):
    repo = _init_repo(tmp_path / "r")
    root = str(tmp_path / "store")
    path = rs.create(repo, "profile", "in-repo", root)
    assert path == os.path.join(repo, ".claude", "review-profile.md")
    assert not os.path.exists(os.path.join(root, "entries"))
    assert not os.path.exists(os.path.join(root, "keys"))


CONTRACT_KEYS = {"kind", "path", "location", "exists", "healed", "entry_id"}


def test_resolve_none_when_nothing(tmp_path):
    repo = _init_repo(tmp_path / "r")
    r = rs.resolve(repo, "profile", str(tmp_path / "store"))
    assert set(r) == CONTRACT_KEYS
    assert r["location"] == "none"
    assert r["path"] is None
    assert r["exists"] is False


def test_resolve_in_repo_wins_over_global(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    # global profile exists
    gpath = rs.create(repo, "profile", "global", root)
    open(gpath, "w").write("global")
    # in-repo profile exists too -> must win
    ipath = rs.create(repo, "profile", "in-repo", root)
    open(ipath, "w").write("inrepo")
    r = rs.resolve(repo, "profile", root)
    assert r["location"] == "in-repo"
    assert r["path"] == os.path.join(repo, ".claude", "review-profile.md")
    assert r["exists"] is True


def test_resolve_global_when_only_global(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    gpath = rs.create(repo, "profile", "global", root)
    open(gpath, "w").write("global")
    r = rs.resolve(repo, "profile", root)
    assert r["location"] == "global"
    assert r["path"] == gpath
    assert r["exists"] is True


def test_resolve_decisions_colocates_with_profile(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    gpath = rs.create(repo, "profile", "global", root)
    open(gpath, "w").write("global")
    r = rs.resolve(repo, "decisions", root)
    assert r["location"] == "global"
    assert r["path"] == os.path.join(os.path.dirname(gpath), "review-decisions.json")


@pytest.mark.parametrize("env,interactive,expected", [
    ("in-repo", True, "in-repo"),
    ("global", True, "global"),
    ("global", False, "global"),
    (None, True, "ask"),
    (None, False, "global"),       # headless default
    ("bogus", True, "ask"),        # invalid env ignored
    ("bogus", False, "global"),
])
def test_decide_location(env, interactive, expected):
    assert rs.decide_location(env, interactive) == expected


def _run_cli(args, cwd, home):
    env = dict(os.environ, HOME=str(home))
    env.pop("REVIEW_CREW_STORAGE", None)
    mod = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "review_store.py")
    return subprocess.run([sys.executable, mod, *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def test_cli_resolve_none_exits_zero_with_null_path(tmp_path):
    repo = _init_repo(tmp_path / "r")
    out = _run_cli(["resolve", "--kind", "profile"], repo, tmp_path / "home")
    assert out.returncode == 0
    payload = json.loads(out.stdout)
    assert payload["location"] == "none"
    assert payload["path"] is None


def test_cli_create_then_resolve_global(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    home = tmp_path / "home"
    cpath = _run_cli(["create", "--kind", "profile", "--location", "global"],
                     repo, home).stdout.strip()
    open(cpath, "w").write("p")
    out = _run_cli(["resolve", "--kind", "profile"], repo, home)
    assert json.loads(out.stdout)["path"] == cpath


def test_cli_unknown_command_exits_nonzero(tmp_path):
    out = _run_cli(["frobnicate"], tmp_path, tmp_path / "home")
    assert out.returncode != 0


def test_disjoint_key_writes_dont_clobber(tmp_path):
    # Concurrency safety is structural, not lock-based: distinct keys map to
    # distinct files, each written with an atomic os.replace, so two writers
    # never share a mutable file. This asserts that disjointness property (the
    # thing that makes concurrent writes safe); true parallelism isn't
    # deterministically testable.
    root = str(tmp_path / "store")
    rs.write_pointer(root, "hashA", "entryA")
    rs.write_pointer(root, "hashB", "entryB")  # different repo, disjoint file
    assert rs.read_pointer(root, "hashA") == "entryA"
    assert rs.read_pointer(root, "hashB") == "entryB"


def test_half_registered_entry_self_heals(tmp_path):
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    eid = ident["gitdir_hash"]
    os.makedirs(os.path.join(root, "entries", eid), exist_ok=True)
    rs._write_keys_json(os.path.join(root, "entries", eid), ident)
    rs.write_pointer(root, ident["gitdir_hash"], eid)  # only one pointer written
    g = rs.resolve_global(repo, root)                  # next resolve heals
    assert rs.read_pointer(root, ident["remote_hash"]) == eid


def test_gitdir_uses_pre_231_fallback(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "r")
    calls = {"n": 0}
    real = rs._run_git

    def fake(cwd, *a):
        if a == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return None  # simulate git < 2.31 not supporting the flag
        if a == ("rev-parse", "--absolute-git-dir"):
            calls["n"] += 1
            return real(cwd, *a)
        return real(cwd, *a)

    monkeypatch.setattr(rs, "_run_git", fake)
    gd = rs.get_gitdir(repo)
    assert calls["n"] == 1            # fell back
    assert os.path.isabs(gd)


def test_resolve_global_falls_back_to_live_entry_when_preferred_dangles(tmp_path):
    # remote pointer dangles (no entry dir); gitdir pointer points to a live one.
    repo = _init_repo(tmp_path / "r", remote="git@github.com:o/p.git")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    rs.write_pointer(root, ident["remote_hash"], "entry-DANGLING")
    rs.write_pointer(root, ident["gitdir_hash"], "entry-LIVE")
    os.makedirs(os.path.join(root, "entries", "entry-LIVE"), exist_ok=True)
    g = rs.resolve_global(repo, root)
    assert g["entry_id"] == "entry-LIVE"          # chose the live entry, not the dangling remote
    assert g["healed"] is True
    assert rs.read_pointer(root, ident["remote_hash"]) == "entry-LIVE"  # re-pointed at the live entry


def test_resolve_global_none_when_all_pointers_dangle(tmp_path):
    repo = _init_repo(tmp_path / "r")
    root = str(tmp_path / "store")
    ident = rs.derive_identifiers(repo)
    rs.write_pointer(root, ident["gitdir_hash"], "entry-GONE")  # no entry dir
    assert rs.resolve_global(repo, root) is None


def test_cli_decide_location(tmp_path):
    repo = _init_repo(tmp_path / "r")
    home = tmp_path / "home"
    out = _run_cli(["decide-location", "--interactive", "true"], repo, home)
    assert out.returncode == 0 and out.stdout.strip() == "ask"
    out = _run_cli(["decide-location", "--interactive", "false"], repo, home)
    assert out.stdout.strip() == "global"
    # env override wins regardless of --interactive
    mod = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "review_store.py")
    env = dict(os.environ, HOME=str(home), REVIEW_CREW_STORAGE="in-repo")
    r = subprocess.run([sys.executable, mod, "decide-location", "--interactive", "true"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.stdout.strip() == "in-repo"
