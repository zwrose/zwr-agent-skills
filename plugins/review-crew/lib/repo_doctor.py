#!/usr/bin/env python3
"""Staleness + degraded-path self-check for a review-crew project profile.

Each review skill runs this at Setup. It re-detects the live signals the way
review-init does (see review-init/SKILL.md Step 1), compares them to the
profile's provenance block, and reports *material drift* as JSON.

FAIL SOFT: this must NEVER crash the orchestrator. Any unreadable/unparseable
profile, or any internal exception, becomes a soft-fail result and exit 0. It
never raises and never returns a nonzero exit code from a real check.

CLI:
    python3 repo_doctor.py <profile-path> <plugin-version> <rubric-version> [--root <dir>]

Output (stdout, JSON):
    { "ok": true, "readable": true, "drift": [...],
      "signal_hash": "<hex or ''>", "nudge_acked": <bool>,
      "message": "<one-line nudge or null>" }

Material drift is ANY of:
  - profile rubric-version < the engine's <rubric-version> arg;
  - profile schema < SUPPORTED_SCHEMA (this script supports schema 1);
  - >= DEP_THRESHOLD added top-level deps (set-diff of live vs profile; only
    ADDED deps count);
  - a new top-level source dir not reflected in the profile's recorded src-dirs;
  - the verify command no longer resolves (its binary is absent on PATH);
  - the profile's default-branch no longer resolves (git rev-parse --verify).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# Highest profile schema this script understands. A profile with schema lower
# than this is behind and counts as material drift (the engine grew newer
# fields). Bump in lockstep with the review-init profile template `schema:`.
SUPPORTED_SCHEMA = 1

# Number of ADDED top-level deps that constitutes material drift.
DEP_THRESHOLD = 3

# Source dirs review-init probes for (Step 1: ls -d src lib app).
SRC_DIR_CANDIDATES = ("src", "lib", "app")

_INT = re.compile(r"-?\d+")


def _parse_provenance(text):
    """Parse the provenance block. Returns a dict or raises on a clearly
    non-profile file. Keys: schema, plugin, rubric-version, nudge-ack (dict),
    dep-set (list), default-branch, forge, src-dirs (list or None)."""
    if "schema:" not in text:
        raise ValueError("no provenance block")

    out = {
        "schema": None,
        "plugin": None,
        "rubric-version": None,
        "nudge-ack": {},
        "dep-set": [],
        "default-branch": None,
        "forge": None,
        "src-dirs": None,
        "verify-command": None,
    }

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("schema:"):
            out["schema"] = _to_int(line.split(":", 1)[1])
        elif line.startswith("plugin:"):
            out["plugin"] = line.split(":", 1)[1].strip()
        elif line.startswith("rubric-version:"):
            out["rubric-version"] = _to_int(line.split(":", 1)[1])
        elif line.startswith("nudge-ack:"):
            out["nudge-ack"] = _parse_ack_map(line.split(":", 1)[1])
        elif line.startswith("dep-set:"):
            out["dep-set"] = _parse_list(line.split(":", 1)[1])
        elif line.startswith("default-branch:"):
            out["default-branch"] = line.split(":", 1)[1].strip() or None
        elif line.startswith("forge:"):
            out["forge"] = line.split(":", 1)[1].strip() or None
        elif line.startswith("src-dirs:"):
            out["src-dirs"] = _parse_list(line.split(":", 1)[1])
        elif line.startswith("command:"):
            # the ## Verify command line (first one wins)
            if out["verify-command"] is None:
                out["verify-command"] = line.split(":", 1)[1].strip() or None

    if out["schema"] is None:
        raise ValueError("provenance block missing schema")
    return out


def _to_int(s):
    m = _INT.search(s)
    return int(m.group(0)) if m else None


def _parse_list(s):
    """Parse a YAML-ish inline list `[a, b@2, c]` → ['a', 'b@2', 'c']."""
    s = s.strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    items = [tok.strip() for tok in s.split(",")]
    return [t for t in items if t]


def _parse_ack_map(s):
    """Parse the nudge-ack map. Accepts `{}` and the keys-as-strings forms the
    profile might carry (`{'<hash>': true, ...}` or `{<hash>: true}`). We only
    need the SET of acked signal-hashes, so collect every hex-ish key."""
    keys = {}
    s = s.strip()
    if not s or s == "{}":
        return {}
    inner = s
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]
    for pair in inner.split(","):
        if ":" not in pair:
            continue
        k = pair.split(":", 1)[0].strip().strip("'\"")
        if k:
            keys[k] = True
    return keys


def _norm_dep(name):
    """Compare deps by base name (drop any @major suffix review-init records)."""
    return name.split("@", 1)[0].strip()


def _detect_live_deps(root):
    """Live top-level dependency names, the way review-init reads them.

    Reads package.json dependencies + devDependencies (JS) only. Other ecosystems
    (pyproject.toml / Cargo.toml / go.mod) are not yet covered, so the dep-drift
    signal is JS-only in v1 (polyglot dep-sets are a v1.1 concern per the spec); the
    other five drift signals remain stack-neutral. Returns a set of base names.
    Missing/unparseable manifests → empty set (no false drift)."""
    deps = set()
    pkg = os.path.join(root, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg) as fh:
                data = json.load(fh)
            for key in ("dependencies", "devDependencies"):
                for name in (data.get(key) or {}):
                    deps.add(_norm_dep(name))
        except Exception:
            pass
    return deps


def _detect_live_src_dirs(root):
    return {d for d in SRC_DIR_CANDIDATES if os.path.isdir(os.path.join(root, d))}


def _verify_binary(verify_command):
    """First token of the verify command — the binary that must resolve."""
    if not verify_command:
        return None
    parts = verify_command.split()
    return parts[0] if parts else None


def _binary_resolves(binary, env):
    """True if the binary is on PATH. Package-manager wrappers (npm/pnpm/yarn/
    make/python3) are treated as resolvable iff the wrapper itself is on PATH —
    we cannot cheaply resolve their subcommands here."""
    if not binary:
        # No verify command recorded → nothing to resolve, not a degradation.
        return True
    path = env.get("PATH")
    return shutil.which(binary, path=path) is not None


def _branch_resolves(root, branch, env):
    """True if `git rev-parse --verify <branch>` succeeds under root."""
    if not branch:
        return True
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "--verify", "--quiet", branch],
            capture_output=True, env=env, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        # No git / timeout: cannot confirm a failure, so do not invent drift.
        return True


def _compute_drift(prof, engine_plugin_ver, engine_rubric_ver, root, env):
    drift = []

    # 1. rubric-version behind the engine.
    pr = prof["rubric-version"]
    if pr is not None and engine_rubric_ver is not None and pr < engine_rubric_ver:
        drift.append(
            "rubric-version advanced %d→%d" % (pr, engine_rubric_ver)
        )

    # 2. schema behind what this script supports.
    sc = prof["schema"]
    if sc is not None and sc < SUPPORTED_SCHEMA:
        drift.append(
            "schema behind: profile schema %d < supported %d"
            % (sc, SUPPORTED_SCHEMA)
        )

    # 3. >= DEP_THRESHOLD ADDED top-level deps.
    profile_deps = {_norm_dep(d) for d in prof["dep-set"]}
    live_deps = _detect_live_deps(root)
    if live_deps:  # only judge when we could actually read a manifest
        added = live_deps - profile_deps
        if len(added) >= DEP_THRESHOLD:
            drift.append(
                "%d added top-level deps since last profile" % len(added)
            )

    # 4. new top-level source dir not reflected in the recorded src-dirs.
    recorded = prof["src-dirs"]
    if recorded is not None:
        recorded_set = {d.strip() for d in recorded}
        live_src = _detect_live_src_dirs(root)
        new_dirs = sorted(live_src - recorded_set)
        if new_dirs:
            drift.append("new top-level source dir: %s" % ", ".join(new_dirs))

    # 5. verify command no longer resolves.
    binary = _verify_binary(prof["verify-command"])
    if not _binary_resolves(binary, env):
        drift.append("verify command no longer resolves: %s" % binary)

    # 6. default-branch no longer resolves.
    branch = prof["default-branch"]
    if not _branch_resolves(root, branch, env):
        drift.append("default-branch no longer resolves: %s" % branch)

    return drift


def _hash_drift(drift):
    if not drift:
        return ""
    joined = "\n".join(sorted(drift))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _soft_fail():
    return {
        "ok": False,
        "readable": False,
        "drift": [],
        "signal_hash": "",
        "nudge_acked": False,
        "message": "profile unreadable — re-run review-init",
    }


def doctor(profile_path, plugin_ver, rubric_ver, root, env):
    """Core check. Returns the result dict. Catches everything → soft-fail."""
    try:
        with open(profile_path) as fh:
            text = fh.read()
    except Exception:
        return _soft_fail()

    try:
        prof = _parse_provenance(text)
    except Exception:
        return _soft_fail()

    try:
        drift = _compute_drift(prof, plugin_ver, rubric_ver, root, env)
        signal_hash = _hash_drift(drift)
        nudge_acked = bool(signal_hash) and signal_hash in prof["nudge-ack"]
        message = None
        if drift:
            message = "review-crew profile drift: " + "; ".join(drift) \
                + " — consider re-running review-init"
        return {
            "ok": True,
            "readable": True,
            "drift": drift,
            "signal_hash": signal_hash,
            "nudge_acked": nudge_acked,
            "message": message,
        }
    except Exception:
        # Internal failure mid-check → degrade to a readable-but-soft result so
        # the orchestrator is never blocked.
        return {
            "ok": False,
            "readable": True,
            "drift": [],
            "signal_hash": "",
            "nudge_acked": False,
            "message": "profile self-check failed — proceeding without staleness data",
        }


def main(argv, env=None):
    if env is None:
        env = os.environ
    try:
        args = argv[1:]
        root = "."
        positional = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--root":
                root = args[i + 1] if i + 1 < len(args) else "."
                i += 2
                continue
            positional.append(a)
            i += 1

        if len(positional) < 3:
            sys.stderr.write(
                "Usage: repo_doctor.py <profile-path> <plugin-version> "
                "<rubric-version> [--root <dir>]\n"
            )
            # Usage error is the one case we report nonzero (no profile given),
            # but per spec we still must not crash the orchestrator; emit a
            # soft-fail JSON and exit 0.
            sys.stdout.write(json.dumps(_soft_fail()) + "\n")
            return 0

        profile_path, plugin_ver, rubric_raw = positional[0], positional[1], positional[2]
        rubric_ver = _to_int(rubric_raw)

        result = doctor(profile_path, plugin_ver, rubric_ver, root, env)
    except Exception:
        result = _soft_fail()

    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
