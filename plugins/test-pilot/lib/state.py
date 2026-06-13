#!/usr/bin/env python3
"""Versioned machine-local state for seeded scenarios.

Shape:
{
  "schemaVersion": 1,
  "manifests": {
    "<key>": {
      "branch": "feat/x", "slot": null,
      "applyOrder": ["scenario-id", ...],          # apply sequence, for reverse clean
      "scenarios": {
        "<id>": {"block": "...", "config": {...}, "configHash": "...",
                 "scenarioHash": "...", "result": {...}, "appliedAt": "..."}
      }
    }
  }
}
"""
import json
import os

import store

SCHEMA_VERSION = 1


class StateError(Exception):
    """User-facing state failure. Never silently resets a bad file."""


def fresh_state():
    return {"schemaVersion": SCHEMA_VERSION, "manifests": {}}


def load_state(path):
    if not os.path.exists(path):
        return fresh_state()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(
            f"state file {path} is unreadable or corrupt ({exc}); refusing "
            f"to guess. Inspect or move the file aside, then re-run apply to "
            f"rebuild from manifests.") from exc
    v = data.get("schemaVersion")
    if v != SCHEMA_VERSION:
        raise StateError(
            f"state file {path} has schemaVersion {v!r}; this engine supports "
            f"{SCHEMA_VERSION}. Update the test-pilot plugin (or migrate the "
            f"file) — do not edit it by hand.")
    return data


def save_state(path, data):
    store.atomic_write(path, json.dumps(data, indent=2, sort_keys=True))
