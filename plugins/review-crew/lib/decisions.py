#!/usr/bin/env python3
"""Versioned learning-loop store + proposal analyzer for review decisions.

The store records the user's per-finding decisions so that a clear repeated
signal can PROPOSE (never auto-apply) a calibration edit. It lives at a project
path the skill passes (e.g. `.claude/review-decisions.json`), OUTSIDE any temp
session dir, and is written atomically (write temp + os.replace).

Store shape:  {"schema": 1, "records": [{"id", "dimension", "category", "action", "ts?"}]}
  action ∈ {skip, guidance, fix};  category is a short tag the skill supplies.

Analyze is read-only: it never mutates any profile/CLAUDE.md, it only proposes.
Nothing here raises on bad input — corrupt/missing input soft-fails.
"""
import hashlib
import json
import os
import sys
import tempfile

SCHEMA = 1

# A clear repeated signal:
SKIP_THRESHOLD = 3      # >=3 skips sharing (dimension, category) -> calibration
GUIDANCE_THRESHOLD = 2  # >=2 guidance citing a convention      -> convention


def _empty_store():
    return {"schema": SCHEMA, "records": []}


def load_store(path):
    """Read the store; soft-fail to an empty store on missing/corrupt/invalid."""
    if not path or not os.path.exists(path):
        return _empty_store()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return _empty_store()
    data.setdefault("schema", SCHEMA)
    return data


def atomic_write(path, store):
    """Write the store atomically: temp file in the same dir + os.replace."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".review-decisions.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(store, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append(path, record):
    """Append one record atomically; create/repair the store as needed."""
    store = load_store(path)
    rid = record.get("id") or hashlib.sha1(
        json.dumps(record, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    entry = {
        "id": rid,
        "dimension": record.get("dimension"),
        "category": record.get("category"),
        "action": record.get("action"),
    }
    if record.get("ts") is not None:
        entry["ts"] = record["ts"]
    store["records"].append(entry)
    atomic_write(path, store)
    return entry


def signal_hash(target, dimension, category):
    """Stable sha1 hex of (target, dimension, category)."""
    key = "\x00".join([target or "", dimension or "", category or ""])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def analyze(path, nudge_ack=None):
    """Return {"proposal": {...} | None}. Read-only; never raises on bad input.

    Returns at most ONE proposal (the strongest/first-qualifying signal):
      - >=3 skips sharing (dimension, category) -> target "profile" (calibration)
      - >=2 guidance sharing category (cited convention) -> target "CLAUDE.md"
    Skip patterns are checked first. A signal whose hash is in nudge_ack is
    declined (returns null) so it does not re-fire until the signal changes.
    """
    acked = set(nudge_ack or [])
    store = load_store(path)
    records = store.get("records", [])

    skip_counts = {}       # (dimension, category) -> count
    guidance_counts = {}   # (dimension, category) -> count
    skip_order = []        # first-seen order of qualifying-candidate keys
    guidance_order = []
    for r in records:
        action = r.get("action")
        key = (r.get("dimension"), r.get("category"))
        if action == "skip":
            if key not in skip_counts:
                skip_order.append(key)
            skip_counts[key] = skip_counts.get(key, 0) + 1
        elif action == "guidance":
            if key not in guidance_counts:
                guidance_order.append(key)
            guidance_counts[key] = guidance_counts.get(key, 0) + 1

    # Skip pattern -> calibration -> profile (checked first).
    for key in skip_order:
        if skip_counts[key] >= SKIP_THRESHOLD:
            dimension, category = key
            h = signal_hash("profile", dimension, category)
            if h in acked:
                continue
            return {"proposal": {
                "target": "profile",
                "dimension": dimension,
                "category": category,
                "text": (f"Consider calibrating the profile: findings in "
                         f"{dimension}/{category} are repeatedly skipped — "
                         f"adjust the threat model / scope / focus so they "
                         f"aren't surfaced."),
                "signal_hash": h,
            }}

    # Guidance citing a convention -> CLAUDE.md.
    for key in guidance_order:
        if guidance_counts[key] >= GUIDANCE_THRESHOLD:
            dimension, category = key
            h = signal_hash("CLAUDE.md", dimension, category)
            if h in acked:
                continue
            return {"proposal": {
                "target": "CLAUDE.md",
                "dimension": dimension,
                "category": category,
                "text": (f"Consider documenting the convention '{category}' in "
                         f"CLAUDE.md — reviewers keep citing it as guidance."),
                "signal_hash": h,
            }}

    return {"proposal": None}


def main(argv):
    args = argv[1:]
    if not args:
        sys.stderr.write(
            "Usage: decisions.py append <file> <record-json>\n"
            "       decisions.py analyze <file> [--nudge-ack <comma-separated-hashes>]\n"
        )
        return 2

    cmd = args[0]

    if cmd == "append":
        if len(args) < 3:
            sys.stderr.write("Usage: decisions.py append <file> <record-json>\n")
            return 2
        path = args[1]
        try:
            record = json.loads(args[2])
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")
        except ValueError as exc:
            sys.stderr.write(f"invalid record json: {exc}\n")
            return 2
        append(path, record)
        sys.stdout.write("ok\n")
        return 0

    if cmd == "analyze":
        if len(args) < 2:
            sys.stderr.write(
                "Usage: decisions.py analyze <file> [--nudge-ack <hashes>]\n")
            return 2
        path = args[1]
        nudge_ack = []
        rest = args[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--nudge-ack" and i + 1 < len(rest):
                nudge_ack = [h for h in rest[i + 1].split(",") if h]
                i += 2
            else:
                i += 1
        result = analyze(path, nudge_ack)
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0

    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
