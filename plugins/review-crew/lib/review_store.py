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

    # Candidate entry-ids in preference order (remote first), deduped.
    candidates = []
    for c in (p_remote, p_gitdir):
        if c and c not in candidates:
            candidates.append(c)
    if not candidates:
        return None

    # Resolve to a LIVE entry: the first candidate whose entry dir exists. If a
    # pointer dangles (its entry dir was deleted out of band), fall through to
    # the other; if none is live, the registration is stale -> treat as absent.
    entry_id = next(
        (c for c in candidates if os.path.isdir(os.path.join(root, "entries", c))),
        None)
    if entry_id is None:
        return None

    # Warn only on a GENUINE conflict: both keys point at live-but-different
    # entries. A mere dangling pointer (one entry dir gone) is routine self-heal,
    # not a conflict, so it stays quiet.
    if (p_remote and p_gitdir and p_remote != p_gitdir
            and os.path.isdir(os.path.join(root, "entries", p_remote))
            and os.path.isdir(os.path.join(root, "entries", p_gitdir))):
        sys.stderr.write(
            "review_store: key disagreement — both keys point at live but "
            "different entries; preferring the remote-keyed entry\n")

    # Self-heal: point both available keys at the chosen live entry. Only writes
    # when a pointer is missing or stale, so we never re-point at a dead entry.
    healed = False
    if gh and p_gitdir != entry_id:
        write_pointer(root, gh, entry_id)
        healed = True
    if rh and p_remote != entry_id:
        write_pointer(root, rh, entry_id)
        healed = True

    entry_dir = os.path.join(root, "entries", entry_id)
    if healed:
        _write_keys_json(entry_dir, ident)
    return {"entry_id": entry_id, "dir": entry_dir, "healed": healed}


def create(cwd, kind, location, root):
    """Return the path to write `kind` at `location`. Non-destructive: never
    truncates an existing profile/decisions file or overwrites an existing
    keys.json. For 'global', mints/reuses the entry and registers both pointers."""
    if location == "in-repo":
        d = os.path.join(cwd, ".claude")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, FILENAMES[kind])
    if location != "global":
        raise ValueError(f"unknown location: {location}")

    ident = derive_identifiers(cwd)
    entry_id = ident["gitdir_hash"]
    entry_dir = os.path.join(root, "entries", entry_id)
    os.makedirs(entry_dir, exist_ok=True)
    if not os.path.exists(os.path.join(entry_dir, "keys.json")):
        _write_keys_json(entry_dir, ident)
    write_pointer(root, ident["gitdir_hash"], entry_id)
    if ident["remote_hash"]:
        write_pointer(root, ident["remote_hash"], entry_id)
    return os.path.join(entry_dir, FILENAMES[kind])


def resolve(cwd, kind, root):
    """Resolve `kind`'s path. Location is keyed on the PROFILE: in-repo profile
    wins, else a global entry whose profile exists, else none. Decisions
    co-locate with the profile."""
    in_repo_profile = os.path.join(cwd, ".claude", "review-profile.md")
    if os.path.exists(in_repo_profile):
        path = os.path.join(cwd, ".claude", FILENAMES[kind])
        return {"kind": kind, "path": path, "location": "in-repo",
                "exists": os.path.exists(path), "healed": False, "entry_id": None}

    g = resolve_global(cwd, root)
    if g is not None and os.path.exists(os.path.join(g["dir"], "review-profile.md")):
        path = os.path.join(g["dir"], FILENAMES[kind])
        return {"kind": kind, "path": path, "location": "global",
                "exists": os.path.exists(path), "healed": g["healed"],
                "entry_id": g["entry_id"]}

    return {"kind": kind, "path": None, "location": "none", "exists": False,
            "healed": g["healed"] if g else False,
            "entry_id": g["entry_id"] if g else None}


def decide_location(env_value, interactive):
    """Where to create when nothing resolved. Env override wins; else interactive
    callers must ask; else (headless) default to global (zero-footprint)."""
    if env_value in ("in-repo", "global"):
        return env_value
    return "ask" if interactive else "global"


def _parse_kv(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main(argv):
    args = argv[1:]
    if not args:
        sys.stderr.write("Usage: review_store.py resolve|create|decide-location ...\n")
        return 2
    cmd = args[0]
    try:
        if cmd == "resolve":
            kind = _parse_kv(args, "--kind") or "profile"
            if kind not in FILENAMES:
                sys.stderr.write(f"bad --kind: {kind}\n")
                return 2
            sys.stdout.write(json.dumps(resolve(os.getcwd(), kind, store_root())) + "\n")
            return 0
        if cmd == "create":
            kind = _parse_kv(args, "--kind") or "profile"
            location = _parse_kv(args, "--location")
            if kind not in FILENAMES or location not in ("global", "in-repo"):
                sys.stderr.write("usage: create --kind profile|decisions --location global|in-repo\n")
                return 2
            sys.stdout.write(create(os.getcwd(), kind, location, store_root()) + "\n")
            return 0
        if cmd == "decide-location":
            interactive = _parse_kv(args, "--interactive") != "false"
            sys.stdout.write(
                decide_location(os.environ.get("REVIEW_CREW_STORAGE"), interactive) + "\n")
            return 0
    except Exception as exc:  # internal error -> non-zero exit per the failure contract
        sys.stderr.write(f"review_store error: {exc}\n")
        return 1
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
