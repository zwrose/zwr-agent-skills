#!/usr/bin/env python3
"""test-pilot engine: manifest/plan-record validation, protected-target gate,
and apply/clean/status/unlock orchestration. CLI contract is JSON (--json).

Run as a script from this directory; sibling modules (store, state, lock,
blocks) import directly because the script dir is on sys.path.
"""
import fnmatch
import hashlib
import json
import os
import re
import sys
import time

import blocks
import lock
import state
import store

MANIFEST_SCHEMA_VERSION = 1
PLAN_RECORD_SCHEMA_VERSION = 1


class EngineError(Exception):
    """Structured engine failure; payload feeds the --json error contract."""

    def __init__(self, message, **payload):
        self.payload = {"error": message, **payload}
        super().__init__(message)


def _load_json(path, what):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(f"unreadable {what} {path}: {exc}") from exc


def load_manifest(path):
    m = _load_json(path, "manifest")
    v = m.get("schemaVersion")
    if v != MANIFEST_SCHEMA_VERSION:
        raise EngineError(
            f"manifest {path} has schemaVersion {v!r}; this engine supports "
            f"{MANIFEST_SCHEMA_VERSION}. Update the test-pilot plugin or "
            f"regenerate the manifest.")
    if not isinstance(m.get("branch"), str) or not m["branch"].strip():
        raise EngineError(f"manifest {path} is missing the `branch` field "
                          f"(identity lives in the JSON, not the filename)")
    slot_val = m.get("slot")
    if slot_val is not None:
        if not isinstance(slot_val, str):
            raise EngineError(
                f"manifest {path} has a non-string slot {slot_val!r}; "
                f"slot must be a string")
        if not store.SLOT_RE.match(slot_val):
            raise EngineError(
                f"manifest {path} has an invalid slot {slot_val!r}")
    scenarios = m.get("scenarios")
    if not isinstance(scenarios, list):
        raise EngineError(f"manifest {path}: `scenarios` must be a list")
    if any(not isinstance(sc, dict) for sc in scenarios):
        raise EngineError(
            f"manifest {path}: every scenario must be an object (dict), "
            f"not a string or number")
    ids = [sc.get("id") for sc in scenarios]
    if any(not isinstance(i, str) or not i for i in ids):
        raise EngineError(f"manifest {path}: every scenario needs a string id")
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise EngineError(f"manifest {path}: duplicate scenario ids {sorted(dupes)}")
    id_set = set(ids)
    for sc in scenarios:
        if not isinstance(sc.get("block"), str) or not sc["block"]:
            raise EngineError(
                f"scenario {sc['id']!r} needs a `block`", scenarioId=sc["id"])
        if not isinstance(sc.get("config"), dict):
            raise EngineError(
                f"scenario {sc['id']!r} needs a `config` object",
                scenarioId=sc["id"])
        deps_val = sc.get("dependsOn", [])
        if not isinstance(deps_val, list) or not all(
                isinstance(d, str) for d in deps_val):
            raise EngineError(
                f"scenario {sc['id']!r} `dependsOn` must be a list of strings",
                scenarioId=sc["id"])
        for dep in deps_val:
            if dep not in id_set:
                raise EngineError(
                    f"scenario {sc['id']!r} dependsOn unknown scenario "
                    f"{dep!r}", scenarioId=sc["id"])
    topo_order(scenarios)  # raises on cycles
    return m


def topo_order(scenarios):
    """Kahn's algorithm. Returns ids in dependency order; cycle -> EngineError."""
    deps = {sc["id"]: set(sc.get("dependsOn", [])) for sc in scenarios}
    order = []
    ready = sorted(i for i, d in deps.items() if not d)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for i in sorted(deps):
            if n in deps[i]:
                deps[i].discard(n)
                if not deps[i] and i not in order and i not in ready:
                    ready.append(i)
    if len(order) != len(deps):
        stuck = sorted(set(deps) - set(order))
        raise EngineError(f"dependsOn cycle involving scenarios {stuck}")
    return order


def load_plan_record(path, manifest, branch=None, slot=None):
    """Load and validate a plan record.

    When branch and/or slot are supplied, each declared field in the plan
    record is cross-checked independently — the same invariant enforced on the
    manifest by _check_manifest_identity.  A field absent from the record is
    treated as informational-only and is not checked; a field present in the
    record must match the corresponding requested value (even when the
    requested value is None — a record that declares a slot must match the
    caller's slot exactly, so slot=None raises EngineError if the record
    carries any slot at all).
    """
    rec = _load_json(path, "plan record")
    v = rec.get("schemaVersion")
    if v != PLAN_RECORD_SCHEMA_VERSION:
        raise EngineError(
            f"plan record {path} has schemaVersion {v!r}; this engine "
            f"supports {PLAN_RECORD_SCHEMA_VERSION}.")
    # Identity cross-check: each declared field is checked independently.
    # If a field is absent from the record it is skipped (not checked).
    # The slot check is STRICT: if the record declares a slot it must match
    # the requested slot regardless of whether slot is None.
    if branch is not None and "branch" in rec and rec["branch"] != branch:
        raise EngineError(
            f"plan record at {path} declares branch={rec['branch']!r}, not "
            f"{branch!r} — identity lives in the JSON")
    if "slot" in rec and rec.get("slot") != slot:
        raise EngineError(
            f"plan record at {path} declares slot={rec['slot']!r}, not "
            f"{slot!r} — identity lives in the JSON")
    if not isinstance(rec.get("steps"), list):
        raise EngineError(
            f"plan record {path}: missing or non-list `steps` field")
    if any(not isinstance(step, dict) for step in rec["steps"]):
        raise EngineError(
            f"plan record {path}: every step must be an object (dict), "
            f"not a string or number")
    ids = {sc["id"] for sc in manifest["scenarios"]}
    for step in rec["steps"]:
        for f in ("id", "instruction", "expected"):
            if not isinstance(step.get(f), str) or not step[f]:
                raise EngineError(
                    f"plan record {path}: step missing `{f}`",
                    step=step.get("id"))
        sids_val = step.get("scenarioIds", [])
        if not isinstance(sids_val, list) or not all(
                isinstance(s, str) for s in sids_val):
            raise EngineError(
                f"plan record {path}: step {step['id']!r} `scenarioIds` must "
                f"be a list of strings", step=step["id"])
        missing = [s for s in sids_val if s not in ids]
        if missing:
            raise EngineError(
                f"plan record {path}: step {step['id']!r} references missing "
                f"scenario id(s) {missing} — regenerate the plan, do not "
                f"treat this as an app bug", step=step["id"])
    return rec


_CONFIG_RE = re.compile(r"```json\s+test-pilot-config\s*\n(.*?)\n```", re.S)


def load_profile_config(profile_path):
    """Parse the one machine-readable fenced block out of profile.md."""
    try:
        with open(profile_path) as _fh:
            text = _fh.read()
    except OSError as exc:
        raise EngineError(f"cannot read profile {profile_path}: {exc}") from exc
    m = _CONFIG_RE.search(text)
    if not m:
        raise EngineError(
            f"profile {profile_path} has no ```json test-pilot-config block; "
            f"re-run test-pilot-init to regenerate it")
    try:
        cfg = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise EngineError(
            f"profile config block in {profile_path} is invalid JSON: {exc}"
        ) from exc
    return cfg


def bare_name(target):
    """Last path segment of a URI-ish target, query/fragment stripped."""
    t = target.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return t.rsplit("/", 1)[-1] if "/" in t else t


def gate_violations(scenarios, project_blocks, protected_patterns):
    """[(scenario_id, target, pattern)] for every declared target whose full
    string OR bare name matches a protected pattern (fnmatchcase)."""
    hits = []
    for sc in scenarios:
        targets = blocks.block_targets(sc["block"], sc.get("config", {}),
                                       project_blocks)
        for target in targets:
            for pat in protected_patterns or []:
                if (fnmatch.fnmatchcase(target, pat)
                        or fnmatch.fnmatchcase(bare_name(target), pat)):
                    hits.append((sc["id"], target, pat))
    return hits


def config_hash(config):
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def scenario_hash(sc):
    """Hash for dirty detection: covers both config and dependsOn."""
    return config_hash({"config": sc.get("config", {}),
                        "dependsOn": sorted(sc.get("dependsOn", []))})


def _scenario_dirty(rec, sc):
    """True when a state record no longer matches the desired scenario."""
    return (rec["block"] != sc["block"]
            or rec.get("scenarioHash") != scenario_hash(sc)
            or rec["configHash"] != config_hash(sc["config"]))


def _warn_protected(verb, hits):
    """Emit the standard --ALLOW-PROTECTED warning for a list of hits."""
    sys.stderr.write(
        f"⚠ --ALLOW-PROTECTED: {verb} protected targets: "
        + ", ".join(f"{t} (pattern {p}, scenario {s})"
                    for s, t, p in hits)
        + "\n")


def _gate_or_raise(hits, allow_protected, clean_hits=None):
    """Raise EngineError if hits and not allow_protected; otherwise warn on
    clean_hits (when allow_protected). clean_hits defaults to hits."""
    if clean_hits is None:
        clean_hits = hits
    if hits and not allow_protected:
        sid, target, pat = hits[0]
        raise EngineError(
            f"protected-target refusal: scenario {sid!r} declares target "
            f"{target!r} matching protected pattern {pat!r}. Pass "
            f"--allow-protected ONLY if the user explicitly instructed it.",
            scenarioId=sid, block=None)
    if allow_protected and clean_hits:
        _warn_protected("cleaning", clean_hits)


def _plan_and_gate(manifest, mstate, project_blocks, profile_cfg, allow_protected):
    """Plan changes and run gate on both new targets and scheduled-clean targets.
    Returns (to_clean, to_apply, skipped, all_hits)."""
    to_clean, to_apply, skipped = plan_changes(manifest, mstate)
    clean_pseudo = [{"id": sid, "block": mstate["scenarios"][sid]["block"],
                     "config": mstate["scenarios"][sid]["config"]}
                    for sid in to_clean if sid in mstate["scenarios"]]
    clean_hits = gate_violations(clean_pseudo, project_blocks,
                                 (profile_cfg or {}).get("protectedTargets"))
    scenarios_hits = gate_violations(manifest["scenarios"], project_blocks,
                                     (profile_cfg or {}).get("protectedTargets"))
    all_hits = scenarios_hits + [h for h in clean_hits if h not in scenarios_hits]
    _gate_or_raise(all_hits, allow_protected, clean_hits=clean_hits)
    if allow_protected and scenarios_hits:
        _warn_protected("writing to", scenarios_hits)
    return to_clean, to_apply, skipped, all_hits


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _state_path(paths):
    return os.path.join(paths["state_dir"], "state.json")


def _lock_path(paths):
    return os.path.join(paths["state_dir"], "engine.lock")


def _manifest_path(paths, key):
    return os.path.join(paths["manifests_dir"], f"{key}.json")


def _ctx(paths, profile_cfg):
    return {"repoRoot": paths["repo_root"],
            "baseUrl": profile_cfg.get("baseUrl"),
            "apiBase": profile_cfg.get("apiBase"),
            "dbEnvVar": profile_cfg.get("dbEnvVar")}


def _dependents(scenarios):
    """id -> set of ids that (transitively) depend on it."""
    direct = {sc["id"]: set() for sc in scenarios}
    for sc in scenarios:
        for dep in sc.get("dependsOn", []):
            direct[dep].add(sc["id"])

    def walk(i, acc):
        for d in direct.get(i, ()):
            if d not in acc:
                acc.add(d)
                walk(d, acc)
        return acc
    return {i: walk(i, set()) for i in direct}


def plan_changes(manifest, mstate):
    """(to_clean ids in reverse-apply order, to_apply ids in topo order,
    skipped ids). Changed block/config -> dirty; dependents of dirty are
    transitively dirty; scenarios missing from the manifest are removed."""
    order = topo_order(manifest["scenarios"])
    desired = {sc["id"]: sc for sc in manifest["scenarios"]}
    existing = mstate.get("scenarios", {})
    removed = set(existing) - set(desired)
    dirty = set()
    for sid, sc in desired.items():
        rec = existing.get(sid)
        if rec and _scenario_dirty(rec, sc):
            dirty.add(sid)
    deps = _dependents(manifest["scenarios"])
    for sid in list(dirty):
        dirty |= {d for d in deps.get(sid, ()) if d in existing}
    to_clean_set = removed | dirty
    apply_order_old = mstate.get("applyOrder", [])
    to_clean = [i for i in reversed(apply_order_old) if i in to_clean_set]
    to_apply = [i for i in order if i not in existing or i in dirty]
    skipped = [i for i in order if i in existing and i not in dirty]
    return to_clean, to_apply, skipped


def _check_manifest_identity(manifest, path, branch, slot):
    """Raise EngineError if the manifest's declared branch/slot != the requested pair."""
    if manifest["branch"] != branch or manifest.get("slot") != slot:
        raise EngineError(
            f"manifest at {path} declares branch="
            f"{manifest['branch']!r} slot={manifest.get('slot')!r}, not "
            f"({branch!r}, {slot!r}) — identity lives in the JSON")


def apply_manifest(paths, branch, slot, profile_cfg, allow_protected,
                   dry_run=False):
    key = store.artifact_key(branch, slot)
    mp = _manifest_path(paths, key)
    manifest = load_manifest(mp)
    _check_manifest_identity(manifest, mp, branch, slot)
    project_blocks = blocks.discover_blocks(paths["blocks_dir"])
    st_path = _state_path(paths)

    if dry_run:
        # Lock-free read is acceptable: dry_run never saves state.
        st = state.load_state(st_path)
        mstate = st["manifests"].get(key, {"branch": branch, "slot": slot,
                                           "applyOrder": [], "scenarios": {}})
        to_clean, to_apply, skipped, all_hits = _plan_and_gate(
            manifest, mstate, project_blocks, profile_cfg, allow_protected)
        return {"ok": True, "command": "apply", "key": key, "dryRun": True,
                "wouldClean": to_clean, "wouldApply": to_apply,
                "skipped": skipped,
                "allowProtectedUsed": bool(all_hits and allow_protected)}

    lp = _lock_path(paths)
    try:
        lock.acquire(lp)
    except lock.LockHeld as exc:
        raise EngineError(
            f"engine lock is held ({exc.holder}); if the holder is dead, "
            f"run `engine.py unlock`") from exc
    try:
        # State is read INSIDE the lock so concurrent applies see fresh data.
        st = state.load_state(st_path)
        mstate = st["manifests"].get(key, {"branch": branch, "slot": slot,
                                           "applyOrder": [], "scenarios": {}})
        to_clean, to_apply, skipped, all_hits = _plan_and_gate(
            manifest, mstate, project_blocks, profile_cfg, allow_protected)
        ctx = _ctx(paths, profile_cfg)
        desired = {sc["id"]: sc for sc in manifest["scenarios"]}
        for sid in to_clean:
            rec = mstate["scenarios"][sid]
            _run_and_raise(rec["block"], "clean", rec["config"], ctx,
                           project_blocks, sid, result=rec.get("result"))
            del mstate["scenarios"][sid]
            mstate["applyOrder"] = [i for i in mstate["applyOrder"] if i != sid]
            st["manifests"][key] = mstate
            state.save_state(st_path, st)
        for sid in to_apply:
            sc = desired[sid]
            result = _run_and_raise(sc["block"], "apply", sc["config"], ctx,
                                    project_blocks, sid)
            mstate["scenarios"][sid] = {
                "block": sc["block"], "config": sc["config"],
                "configHash": config_hash(sc["config"]),
                "scenarioHash": scenario_hash(sc),
                "result": result, "appliedAt": _now()}
            mstate["applyOrder"].append(sid)
            st["manifests"][key] = mstate
            state.save_state(st_path, st)
        if not mstate["scenarios"]:
            st["manifests"].pop(key, None)
            state.save_state(st_path, st)
        return {"ok": True, "command": "apply", "key": key, "dryRun": False,
                "applied": to_apply, "cleaned": to_clean, "skipped": skipped,
                "allowProtectedUsed": bool(all_hits and allow_protected)}
    finally:
        lock.release(lp)


def _run_and_raise(block, op, config, ctx, project_blocks, sid, result=None):
    try:
        return blocks.run_block(block, op, config, ctx, project_blocks,
                                result=result)
    except blocks.BlockError as exc:
        raise EngineError(str(exc), block=exc.block or block,
                          scenarioId=sid) from exc


def clean_manifest(paths, branch, slot, profile_cfg=None, allow_protected=False):
    """Clean every seeded scenario for branch+slot FROM STATE (works for
    orphans whose manifest file is gone)."""
    key = store.artifact_key(branch, slot)
    st_path = _state_path(paths)
    project_blocks = blocks.discover_blocks(paths["blocks_dir"])
    lp = _lock_path(paths)
    try:
        lock.acquire(lp)
    except lock.LockHeld as exc:
        raise EngineError(f"engine lock is held ({exc.holder})") from exc
    cleaned = []
    try:
        # State is read INSIDE the lock so concurrent operations see fresh data.
        st = state.load_state(st_path)
        mstate = st["manifests"].get(key)
        if not mstate:
            return {"ok": True, "command": "clean", "key": key, "cleaned": []}
        # Gate: check whether any scenarios being cleaned touch protected targets.
        pseudo = [{"id": sid, "block": rec["block"], "config": rec["config"]}
                  for sid, rec in mstate["scenarios"].items()]
        hits = gate_violations(pseudo, project_blocks,
                               (profile_cfg or {}).get("protectedTargets"))
        _gate_or_raise(hits, allow_protected, clean_hits=hits)
        ctx = _ctx(paths, profile_cfg or {})
        for sid in list(reversed(mstate["applyOrder"])):
            rec = mstate["scenarios"][sid]
            _run_and_raise(rec["block"], "clean", rec["config"], ctx,
                           project_blocks, sid, result=rec.get("result"))
            del mstate["scenarios"][sid]
            mstate["applyOrder"].remove(sid)
            state.save_state(st_path, st)
            cleaned.append(sid)
        st["manifests"].pop(key, None)
        state.save_state(st_path, st)
        return {"ok": True, "command": "clean", "key": key, "cleaned": cleaned}
    finally:
        lock.release(lp)


def status(paths):
    st = state.load_state(_state_path(paths))
    entries = []
    for key, mstate in sorted(st["manifests"].items()):
        mp = _manifest_path(paths, key)
        drift = []
        manifest_error = None
        if os.path.exists(mp):
            try:
                manifest = load_manifest(mp)
                desired = {sc["id"]: sc for sc in manifest["scenarios"]}
                for sid, rec in mstate["scenarios"].items():
                    sc = desired.get(sid)
                    if sc is None or _scenario_dirty(rec, sc):
                        drift.append(sid)
            except EngineError as exc:
                manifest_error = str(exc)
        entries.append({"key": key, "branch": mstate["branch"],
                        "slot": mstate.get("slot"),
                        "applied": len(mstate["scenarios"]),
                        "drift": sorted(drift),
                        "manifestError": manifest_error,
                        "orphan": not os.path.exists(mp)})
    lp = _lock_path(paths)
    holder = lock.read_holder(lp) if os.path.exists(lp) else None
    return {"ok": True, "command": "status", "entries": entries,
            "lock": holder, "lockStale": lock.is_stale(lp) if holder else False}


def unlock(paths):
    lp = _lock_path(paths)
    holder = lock.read_holder(lp) if os.path.exists(lp) else None
    released = holder is not None
    lock.release(lp)
    return {"ok": True, "command": "unlock", "released": released,
            "holder": holder}


def _arg(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return default


def _resolve_paths():
    cwd = os.getcwd()
    res = store.resolve(cwd, store.store_root())
    if res["location"] == "none":
        raise EngineError("no test-pilot profile resolves here; run "
                          "test-pilot-init first")
    return {"state_dir": res["state_dir"],
            "manifests_dir": res["manifests_dir"],
            "blocks_dir": res["blocks_dir"],
            "repo_root": store.get_repo_root(cwd)}, res


def main(argv):
    args = argv[1:]
    cmd = args[0] if args else None
    as_json = "--json" in args
    if cmd not in ("apply", "clean", "status", "unlock", "validate-plan"):
        sys.stderr.write("Usage: engine.py apply|clean|status|unlock|validate-plan "
                         "[--branch B] [--slot S] [--dry-run] "
                         "[--allow-protected] [--json]\n")
        return 2
    try:
        paths, res = _resolve_paths()
        if cmd in ("apply", "clean"):
            branch = _arg(args, "--branch")
            if not branch:
                raise EngineError(f"{cmd} requires --branch")
            slot = _arg(args, "--slot")
            if cmd == "apply":
                profile_cfg = load_profile_config(res["profile"])
                out = apply_manifest(paths, branch, slot, profile_cfg,
                                     allow_protected="--allow-protected" in args,
                                     dry_run="--dry-run" in args)
            else:
                profile_cfg = load_profile_config(res["profile"])
                out = clean_manifest(paths, branch, slot, profile_cfg,
                                     allow_protected="--allow-protected" in args)
        elif cmd == "validate-plan":
            branch = _arg(args, "--branch")
            if not branch:
                raise EngineError("validate-plan requires --branch")
            slot = _arg(args, "--slot")
            key = store.artifact_key(branch, slot)
            mp = _manifest_path(paths, key)
            manifest = load_manifest(mp)
            _check_manifest_identity(manifest, mp, branch, slot)
            plan_path = os.path.join(paths["manifests_dir"], f"{key}.plan.json")
            rec = load_plan_record(plan_path, manifest, branch=branch, slot=slot)
            out = {"ok": True, "command": "validate-plan", "key": key,
                   "steps": len(rec["steps"])}
        elif cmd == "status":
            out = status(paths)
        else:
            out = unlock(paths)
        sys.stdout.write(json.dumps(out) + "\n" if as_json
                         else json.dumps(out, indent=2) + "\n")
        return 0
    except (EngineError, state.StateError, blocks.BlockError) as exc:
        payload = getattr(exc, "payload", {"error": str(exc)})
        err = {"ok": False, "command": cmd,
               "error": payload.get("error", str(exc)),
               "block": payload.get("block"),
               "scenarioId": payload.get("scenarioId")}
        sys.stdout.write(json.dumps(err) + "\n")
        return 1
    except ValueError as exc:
        err = {"ok": False, "command": cmd, "error": str(exc),
               "block": None, "scenarioId": None}
        sys.stdout.write(json.dumps(err) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
