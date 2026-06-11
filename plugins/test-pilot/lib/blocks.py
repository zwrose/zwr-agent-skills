#!/usr/bin/env python3
"""Block discovery, static metadata, and the one subprocess contract.

A block is a Python module in the project's blocks/ dir exposing
apply(config, ctx) -> result and clean(result, ctx), plus a module-level
BLOCK_META literal dict (description, config, targets). Metadata is read by
static parse — the engine NEVER imports a block. All execution happens as a
subprocess: request JSON on stdin, result JSON on stdout (see run_block).

Built-in blocks (reserved names, lookup precedes the project dir):
- run-command: wraps a project command; declares `targets` per-scenario in
  its manifest config because each use touches different surfaces.
"""
import ast
import json
import os
import shutil
import subprocess
import sys

BUILTIN_BLOCKS = {
    "run-command": {
        "description": ("Wrap a project command (e.g. `npm run seed:x`) for "
                        "app-code seeding. `targets` is declared per-scenario "
                        "in the manifest config."),
        "config": {"command": "argv array (required)",
                   "cleanCommand": "argv array (optional; clean is a no-op without it)",
                   "targets": "non-empty list of touched surfaces (required)"},
    },
}


class BlockError(Exception):
    def __init__(self, message, block=None):
        self.block = block
        super().__init__(message)


def read_block_meta(path):
    """Statically parse the module-level BLOCK_META literal dict."""
    try:
        tree = ast.parse(open(path).read())
    except (OSError, SyntaxError) as exc:
        raise BlockError(f"cannot parse block {path}: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "BLOCK_META":
                    try:
                        meta = ast.literal_eval(node.value)
                    except ValueError as exc:
                        raise BlockError(
                            f"BLOCK_META in {path} must be a literal dict"
                        ) from exc
                    if not isinstance(meta, dict):
                        raise BlockError(f"BLOCK_META in {path} must be a dict")
                    return meta
    raise BlockError(f"block {path} has no module-level BLOCK_META")


def discover_blocks(blocks_dir):
    """{name: {"path", "meta"}}. Shadowing a built-in is a validation error;
    lookup order is built-ins first, then the project blocks/ dir."""
    found = {}
    if not blocks_dir or not os.path.isdir(blocks_dir):
        return found
    for fn in sorted(os.listdir(blocks_dir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        name = fn[:-3]
        if name in BUILTIN_BLOCKS:
            raise BlockError(
                f"project block {fn} shadows built-in block {name!r}; "
                f"built-in names are reserved", block=name)
        path = os.path.join(blocks_dir, fn)
        found[name] = {"path": path, "meta": read_block_meta(path)}
    return found


def block_targets(name, config, project_blocks):
    """Declared targets for one scenario. Missing/empty is a validation
    error — the gate never treats an undeclared block as unprotected."""
    if name == "run-command":
        targets = config.get("targets")
    elif name in project_blocks:
        targets = project_blocks[name]["meta"].get("targets")
    else:
        raise BlockError(f"unknown block {name!r}", block=name)
    if (not isinstance(targets, list) or not targets
            or not all(isinstance(t, str) and t for t in targets)):
        raise BlockError(
            f"block {name!r} declares no targets; every block must declare "
            f"the surfaces it touches (non-empty list of strings)", block=name)
    return targets


def has_pep723(path):
    try:
        with open(path) as fh:
            return any(line.strip() == "# /// script" for line in fh)
    except OSError:
        return False
