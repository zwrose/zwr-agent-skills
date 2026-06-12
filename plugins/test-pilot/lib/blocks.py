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
        with open(path) as _fh:
            tree = ast.parse(_fh.read())
    except (OSError, SyntaxError) as exc:
        raise BlockError(f"cannot parse block {path}: {exc}") from exc
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "BLOCK_META":
                    value_node = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "BLOCK_META":
                value_node = node.value
        if value_node is not None:
            try:
                meta = ast.literal_eval(value_node)
            except (ValueError, TypeError, SyntaxError, RecursionError) as exc:
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


def valid_targets(value):
    """Return True iff value is a non-empty list of non-empty strings.

    Single source of truth for the targets validity rule used by both
    block_targets() (gate enforcement) and catalog.generate() (INVALID marker).
    """
    return (isinstance(value, list) and bool(value)
            and all(isinstance(t, str) and t for t in value))


def block_targets(name, config, project_blocks):
    """Declared targets for one scenario. Missing/empty is a validation
    error — the gate never treats an undeclared block as unprotected."""
    if name == "run-command":
        targets = config.get("targets")
    elif name in project_blocks:
        targets = project_blocks[name]["meta"].get("targets")
    else:
        raise BlockError(f"unknown block {name!r}", block=name)
    if not valid_targets(targets):
        raise BlockError(
            f"block {name!r} declares no targets; every block must declare "
            f"the surfaces it touches (non-empty list of strings)", block=name)
    return targets


def has_pep723(path):
    try:
        with open(path) as fh:
            return any(line.rstrip("\r\n") == "# /// script" for line in fh)
    except OSError:
        return False


def run_block(name, op, config, ctx, project_blocks, result=None,
              runner=subprocess.run):
    """Execute a block under the ONE subprocess contract."""
    if name == "run-command":
        return _run_command_block(op, config, ctx, runner)
    info = project_blocks.get(name)
    if info is None:
        raise BlockError(f"unknown block {name!r}", block=name)
    if has_pep723(info["path"]):
        if shutil.which("uv") is None:
            raise BlockError(
                f"block {name!r} declares PEP 723 dependencies but `uv` is "
                f"not installed; install it first "
                f"(https://docs.astral.sh/uv/ — e.g. `brew install uv`)",
                block=name)
        argv = ["uv", "run", info["path"]]
    else:
        argv = [sys.executable, info["path"]]
    request = {"op": op, "config": config, "ctx": ctx}
    if result is not None:
        request["result"] = result
    try:
        proc = runner(argv, input=json.dumps(request), text=True,
                      capture_output=True, cwd=ctx.get("repoRoot"), timeout=600)
    except subprocess.TimeoutExpired:
        raise BlockError(f"block {name!r} {op} timed out after 600s", block=name)
    if proc.returncode != 0:
        raise BlockError(
            f"block {name!r} {op} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:500]}", block=name)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BlockError(
            f"block {name!r} printed non-JSON output: "
            f"{proc.stdout.strip()[:200]!r}", block=name) from exc


def _run_command_block(op, config, ctx, runner):
    argv = config.get("command") if op == "apply" else config.get("cleanCommand")
    if op == "clean" and not argv:
        return {"skipped": "no cleanCommand"}
    field = "command" if op == "apply" else "cleanCommand"
    if not isinstance(argv, list) or not argv:
        raise BlockError(f"run-command requires config.{field} (argv array)",
                         block="run-command")
    _MISSING = object()
    bad = next((a for a in argv if not isinstance(a, str) or not a), _MISSING)
    if bad is not _MISSING:
        raise BlockError(
            f"run-command requires config.{field} to be an array of non-empty "
            f"strings; got {bad!r}", block="run-command")
    try:
        proc = runner(argv, text=True, capture_output=True,
                      cwd=ctx.get("repoRoot"), timeout=600)
    except subprocess.TimeoutExpired:
        raise BlockError(f"block 'run-command' {op} timed out after 600s",
                         block="run-command")
    if proc.returncode != 0:
        raise BlockError(
            f"run-command {op} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:500]}", block="run-command")
    return {"exitCode": 0, "stdout": proc.stdout[-2000:]}
