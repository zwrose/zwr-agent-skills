#!/usr/bin/env python3
"""Resolve where a project's review-crew profile/decisions live.

Two locations, checked in order: in-repo (./.claude/) then a global per-repo
store at ~/.claude/review-crew/ keyed by BOTH the normalized origin URL and the
git-common-dir path (per-key pointer files, self-healing). See
docs/superpowers/specs/2026-06-07-review-crew-profile-storage-design.md.

All git calls use argv arrays with a timeout — never shell=True.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

FILENAMES = {"profile": "review-profile.md", "decisions": "review-decisions.json"}


def normalize_remote(url):
    """Normalize a remote URL to host/path: lowercase host, drop scheme/userinfo/
    port, strip trailing .git and slashes. Return None for empty/None."""
    if not url:
        return None
    s = url.strip()
    if not s:
        return None
    # scp-like: git@host:org/repo.git
    m = re.match(r"^[^@/]+@([^:/]+):(.+)$", s)
    if m:
        host, path = m.group(1), m.group(2)
    else:
        # scheme://[user@]host[:port]/path
        m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+)$", s)
        if m:
            host, path = m.group(1), m.group(2)
        else:
            return None
    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    return f"{host}/{path}"


def short_hash(s):
    """First 16 hex chars of sha256(s)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _run_git(cwd, *args):
    """Run git with an argv array + timeout. Return stdout (stripped) or None."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def get_remote(cwd):
    """Normalized origin URL, or None."""
    return normalize_remote(_run_git(cwd, "remote", "get-url", "origin"))


def get_gitdir(cwd):
    """realpath of the git-common-dir (shared by all worktrees). Falls back to
    `--absolute-git-dir` for git < 2.31, then to realpath(cwd) for non-git dirs."""
    out = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if out is None:
        out = _run_git(cwd, "rev-parse", "--absolute-git-dir")
    if out is None:
        out = cwd
    return os.path.realpath(out)


def derive_identifiers(cwd):
    remote = get_remote(cwd)
    gitdir = get_gitdir(cwd)
    return {
        "remote": remote,
        "gitdir": gitdir,
        "remote_hash": short_hash(remote) if remote else None,
        "gitdir_hash": short_hash(gitdir),
    }


def store_root():
    return os.path.realpath(os.path.expanduser("~/.claude/review-crew"))


def _atomic_write(path, text):
    """Write text atomically: temp file in the same dir + os.replace."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".review-store.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _keys_dir(root):
    return os.path.join(root, "keys")


def read_pointer(root, key_hash):
    p = os.path.join(_keys_dir(root), key_hash)
    try:
        with open(p) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def write_pointer(root, key_hash, entry_id):
    _atomic_write(os.path.join(_keys_dir(root), key_hash), entry_id)


def _write_keys_json(entry_dir, ident):
    _atomic_write(os.path.join(entry_dir, "keys.json"), json.dumps({
        "remote": ident["remote"],
        "gitdir": ident["gitdir"],
        "remote_hash": ident["remote_hash"],
        "gitdir_hash": ident["gitdir_hash"],
    }, indent=2))


def resolve_global(cwd, root):
    """Find the global entry for cwd via its key pointers, self-healing a
    missing/changed pointer. Return {entry_id, dir, healed} or None."""
    ident = derive_identifiers(cwd)
    rh, gh = ident["remote_hash"], ident["gitdir_hash"]
    p_remote = read_pointer(root, rh) if rh else None
    p_gitdir = read_pointer(root, gh)
    healed = False

    if p_remote and p_gitdir:
        if p_remote == p_gitdir:
            entry_id = p_remote
        else:
            sys.stderr.write(
                "review_store: key disagreement; preferring remote-keyed entry\n")
            entry_id = p_remote
            write_pointer(root, gh, entry_id)
            healed = True
    elif p_remote and not p_gitdir:
        entry_id = p_remote
        write_pointer(root, gh, entry_id)
        healed = True
    elif p_gitdir and not p_remote:
        entry_id = p_gitdir
        if rh:  # a remote exists now but has no pointer yet -> heal
            write_pointer(root, rh, entry_id)
            healed = True
    else:
        return None

    entry_dir = os.path.join(root, "entries", entry_id)
    if healed and os.path.isdir(entry_dir):
        _write_keys_json(entry_dir, ident)
    return {"entry_id": entry_id, "dir": entry_dir, "healed": healed}
