#!/usr/bin/env python3
"""Validate the marketplace catalog and every plugin manifest it points at.

Run from the repo root (CI does this). Exits non-zero with a list of problems.

Checks, in order:
  1. marketplace.json parses and has `name` + a `plugins` array.
  2. Each plugin entry has `name` + `source`.
  3. For local (string) sources: the plugin dir exists, its
     `.claude-plugin/plugin.json` exists and parses, its `name` matches the
     catalog entry, and its `version` is valid SemVer.
  4. The "silent-mask" trap: a plugin must NOT declare `version` in BOTH its
     plugin.json and its marketplace entry — plugin.json wins silently, so a
     stale catalog version would be masked. (See Claude Code plugin docs,
     "Version resolution and release channels".)
  5. metadata.version, if present, is valid SemVer.

Object sources (github/url/git-subdir/npm) are reported but not fetched.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# Official SemVer 2.0.0 regex (https://semver.org).
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

errors: list[str] = []
notes: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        err(f"missing file: {path.relative_to(REPO_ROOT)}")
    except json.JSONDecodeError as e:
        err(f"invalid JSON in {path.relative_to(REPO_ROOT)}: {e}")
    return None


def resolve_source(source: str, plugin_root: str | None) -> Path:
    """Resolve a string source to a dir, honoring metadata.pluginRoot."""
    if plugin_root and not source.startswith(("./", "/")):
        rel = f"{plugin_root.rstrip('/')}/{source}"
    else:
        rel = source
    return (REPO_ROOT / rel).resolve()


def main() -> int:
    catalog = load_json(MARKETPLACE)
    if catalog is None:
        return 1

    if not catalog.get("name"):
        err("marketplace.json: missing top-level `name`")

    metadata = catalog.get("metadata") or {}
    mkt_version = catalog.get("version") or metadata.get("version")
    if mkt_version is not None and not SEMVER.match(str(mkt_version)):
        err(f"marketplace.json: metadata.version '{mkt_version}' is not valid SemVer")

    plugin_root = metadata.get("pluginRoot")

    plugins = catalog.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        err("marketplace.json: `plugins` must be a non-empty array")
        return _finish()

    for i, entry in enumerate(plugins):
        label = entry.get("name") or f"plugins[{i}]"
        if not entry.get("name"):
            err(f"{label}: missing `name`")
        source = entry.get("source")
        if source is None:
            err(f"{label}: missing `source`")
            continue

        if not isinstance(source, str):
            notes.append(f"{label}: remote source ({source.get('source')}) — not fetched/validated")
            continue

        plugin_dir = resolve_source(source, plugin_root)
        if not plugin_dir.is_dir():
            err(f"{label}: source path does not exist: {source}")
            continue

        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        manifest = load_json(manifest_path)
        if manifest is None:
            continue

        if manifest.get("name") != entry.get("name"):
            err(
                f"{label}: name mismatch — catalog '{entry.get('name')}' "
                f"vs plugin.json '{manifest.get('name')}'"
            )

        version = manifest.get("version")
        if not version:
            err(f"{label}: plugin.json has no `version` (required for update detection)")
        elif not SEMVER.match(str(version)):
            err(f"{label}: plugin.json version '{version}' is not valid SemVer")

        # The silent-mask trap: version in both places.
        if "version" in entry:
            err(
                f"{label}: `version` is set in BOTH plugin.json and the marketplace "
                f"entry. plugin.json wins silently — remove it from marketplace.json."
            )

    return _finish()


def _finish() -> int:
    for n in notes:
        print(f"note: {n}")
    if errors:
        print(f"\n✗ {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("✓ marketplace + plugin manifests valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
