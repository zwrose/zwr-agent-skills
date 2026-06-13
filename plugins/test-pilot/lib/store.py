#!/usr/bin/env python3
"""test-pilot storage resolver + artifact key derivation.

artifact_key() is THE one key-derivation function for every artifact name
that embeds branch+slot identity (manifests, plan records, fallback files,
comment markers). Injective: % is encoded before /, and the slot delimiter ~
is illegal in git refnames, so distinct (branch, slot) pairs never collide.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def sanitize_branch(branch):
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError("empty branch name")
    return branch.replace("%", "%25").replace("/", "%2F")


def artifact_key(branch, slot=None):
    if slot is not None and not SLOT_RE.match(slot):
        raise ValueError(
            f"invalid slot {slot!r}: must match {SLOT_RE.pattern}")
    key = sanitize_branch(branch)
    return f"{key}~{slot}" if slot is not None else key


def normalize_remote(url):
    """Normalize a remote URL to host/path. None for empty/unparseable."""
    if not url:
        return None
    s = url.strip()
    if not s:
        return None
    m = re.match(r"^[^@/]+@([^:/]+):(.+)$", s)
    if m:
        host, path = m.group(1), m.group(2)
    else:
        m = re.match(
            r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+)$", s)
        if m:
            host, path = m.group(1), m.group(2)
        else:
            return None
    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path.strip('/')}"


def short_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _run_git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def get_remote(cwd):
    return normalize_remote(_run_git(cwd, "remote", "get-url", "origin"))


def get_gitdir(cwd):
    out = _run_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if out is None:
        out = _run_git(cwd, "rev-parse", "--absolute-git-dir")
    return os.path.realpath(out if out is not None else cwd)


def derive_identifiers(cwd):
    remote = get_remote(cwd)
    gitdir = get_gitdir(cwd)
    return {"remote": remote, "gitdir": gitdir,
            "remote_hash": short_hash(remote) if remote else None,
            "gitdir_hash": short_hash(gitdir)}


def get_repo_root(cwd):
    """Return the git worktree top-level for cwd (fallback: cwd itself)."""
    out = _run_git(cwd, "rev-parse", "--show-toplevel")
    if out:
        return os.path.realpath(out)
    return os.path.realpath(cwd)


def store_root():
    return os.path.realpath(os.path.expanduser(
        os.environ.get("TEST_PILOT_STORE_ROOT", "~/.claude/test-pilot")))


def atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".test-pilot-store.", suffix=".tmp")
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


def read_pointer(root, key_hash):
    try:
        with open(os.path.join(root, "keys", key_hash)) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def write_pointer(root, key_hash, entry_id):
    atomic_write(os.path.join(root, "keys", key_hash), entry_id)


def _write_keys_json(entry_dir, ident):
    atomic_write(os.path.join(entry_dir, "keys.json"),
                  json.dumps(ident, indent=2))


def resolve_global(cwd, root):
    """Find the live global entry for cwd via key pointers (remote preferred),
    self-healing dangling/stale pointers. Same algorithm as review_store."""
    ident = derive_identifiers(cwd)
    rh, gh = ident["remote_hash"], ident["gitdir_hash"]
    p_remote = read_pointer(root, rh) if rh else None
    p_gitdir = read_pointer(root, gh)
    candidates = []
    for c in (p_remote, p_gitdir):
        if c and c not in candidates:
            candidates.append(c)
    entry_id = next(
        (c for c in candidates
         if os.path.isdir(os.path.join(root, "entries", c))), None)
    if entry_id is None:
        return None

    # Warn on a GENUINE conflict: both pointers point at live-but-different entries.
    if (p_remote and p_gitdir and p_remote != p_gitdir
            and os.path.isdir(os.path.join(root, "entries", p_remote))
            and os.path.isdir(os.path.join(root, "entries", p_gitdir))):
        sys.stderr.write(
            "test_pilot store: key disagreement — both keys point at live but "
            "different entries; preferring the remote-keyed entry\n")

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


def _entry_dirs(entry_dir):
    return {"blocks_dir": os.path.join(entry_dir, "blocks"),
            "manifests_dir": os.path.join(entry_dir, "manifests"),
            "plans_dir": os.path.join(entry_dir, "plans"),
            "state_dir": os.path.join(entry_dir, "state")}


def resolve(cwd, root):
    """Resolve all artifact locations. Location keys on the PROFILE: in-repo
    profile wins, else a global entry whose profile exists, else none.
    plans_dir/state_dir ALWAYS point into the global entry (machine-local)."""
    repo_root = get_repo_root(cwd)
    ident = derive_identifiers(cwd)
    g = resolve_global(cwd, root)
    entry_id = g["entry_id"] if g else ident["gitdir_hash"]
    entry_dir = os.path.join(root, "entries", entry_id)
    machine = {k: v for k, v in _entry_dirs(entry_dir).items()
               if k in ("plans_dir", "state_dir")}

    in_repo = os.path.join(repo_root, ".claude", "test-pilot")
    if os.path.exists(os.path.join(in_repo, "profile.md")):
        return {"location": "in-repo", "exists": True, "entry_id": entry_id,
                "profile": os.path.join(in_repo, "profile.md"),
                "blocks_dir": os.path.join(in_repo, "blocks"),
                "manifests_dir": os.path.join(in_repo, "manifests"),
                **machine}
    if g is not None and os.path.exists(os.path.join(g["dir"], "profile.md")):
        d = _entry_dirs(g["dir"])
        return {"location": "global", "exists": True, "entry_id": g["entry_id"],
                "profile": os.path.join(g["dir"], "profile.md"), **d}
    return {"location": "none", "exists": False, "entry_id": entry_id,
            "profile": None, "blocks_dir": None, "manifests_dir": None,
            **machine}


def create(cwd, location, root):
    """Create the directory skeleton for `location` and ALWAYS mint the global
    entry (state/plans live there in both modes). Non-destructive. Returns the
    same dict shape as resolve()."""
    repo_root = get_repo_root(cwd)
    ident = derive_identifiers(cwd)
    # Reuse an existing live entry if one already exists (avoids orphaning
    # applied state when a second clone creates a fresh gitdir-hash entry).
    existing = resolve_global(cwd, root)
    if existing is not None:
        entry_id = existing["entry_id"]
        entry_dir = existing["dir"]
    else:
        entry_id = ident["gitdir_hash"]
        entry_dir = os.path.join(root, "entries", entry_id)
    os.makedirs(entry_dir, exist_ok=True)
    if not os.path.exists(os.path.join(entry_dir, "keys.json")):
        _write_keys_json(entry_dir, ident)
    write_pointer(root, ident["gitdir_hash"], entry_id)
    if ident["remote_hash"]:
        write_pointer(root, ident["remote_hash"], entry_id)
    d = _entry_dirs(entry_dir)
    os.makedirs(d["plans_dir"], exist_ok=True)
    os.makedirs(d["state_dir"], exist_ok=True)

    if location == "in-repo":
        base = os.path.join(repo_root, ".claude", "test-pilot")
        blocks, manifests = (os.path.join(base, "blocks"),
                             os.path.join(base, "manifests"))
        os.makedirs(blocks, exist_ok=True)
        os.makedirs(manifests, exist_ok=True)
        profile = os.path.join(base, "profile.md")
    elif location == "global":
        os.makedirs(d["blocks_dir"], exist_ok=True)
        os.makedirs(d["manifests_dir"], exist_ok=True)
        blocks, manifests = d["blocks_dir"], d["manifests_dir"]
        profile = os.path.join(entry_dir, "profile.md")
    else:
        raise ValueError(f"unknown location: {location}")
    return {"location": location, "exists": os.path.exists(profile),
            "entry_id": entry_id, "profile": profile, "blocks_dir": blocks,
            "manifests_dir": manifests, "plans_dir": d["plans_dir"],
            "state_dir": d["state_dir"]}


def decide_location(env_value, interactive):
    if env_value in ("in-repo", "global"):
        return env_value
    return "ask" if interactive else "global"


def _parse_kv(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return default


def main(argv):
    args = argv[1:]
    if not args:
        sys.stderr.write(
            "Usage: store.py resolve|create|decide-location|key ...\n")
        return 2
    cmd = args[0]
    try:
        if cmd == "resolve":
            sys.stdout.write(json.dumps(resolve(os.getcwd(), store_root())) + "\n")
            return 0
        if cmd == "create":
            location = _parse_kv(args, "--location")
            if location not in ("global", "in-repo"):
                sys.stderr.write("usage: create --location global|in-repo\n")
                return 2
            sys.stdout.write(
                json.dumps(create(os.getcwd(), location, store_root())) + "\n")
            return 0
        if cmd == "decide-location":
            interactive = _parse_kv(args, "--interactive") != "false"
            sys.stdout.write(decide_location(
                os.environ.get("TEST_PILOT_STORAGE"), interactive) + "\n")
            return 0
        if cmd == "key":
            branch = _parse_kv(args, "--branch")
            if not branch:
                sys.stderr.write("usage: key --branch B [--slot S]\n")
                return 2
            sys.stdout.write(artifact_key(branch, _parse_kv(args, "--slot")) + "\n")
            return 0
    except Exception as exc:
        sys.stderr.write(f"store error: {exc}\n")
        return 1
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
