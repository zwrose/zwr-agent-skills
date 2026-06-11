"""{{Block purpose, one line.}}

test-pilot scenario block. Executes as a subprocess: request JSON on stdin
({"op": "apply"|"clean", "config", "ctx"[, "result"]}), result JSON on
stdout. ctx carries repoRoot, baseUrl, apiBase, dbEnvVar.

If this block needs third-party packages, add a PEP 723 header WITH PINNED
VERSIONS and the engine will run it under `uv run`:

    # /// script
    # dependencies = ["requests==2.32.3"]
    # ///
"""

BLOCK_META = {
    "description": "{{What this block seeds, one sentence.}}",
    "config": {
        "{{field}}": "{{what it means}}",
    },
    # REQUIRED, non-empty: every surface this block touches. The protected-
    # target gate checks these; an empty list is a validation error.
    "targets": ["{{e.g. http://localhost:3000/api or app-test-db}}"],
}


def apply(config, ctx):
    """Seed the scenario. Return a JSON-serializable result that clean()
    can use to tear everything down (created ids, paths, names)."""
    raise NotImplementedError


def clean(result, ctx):
    """Tear down exactly what apply() created, using its result."""
    raise NotImplementedError


if __name__ == "__main__":
    import json
    import sys

    _req = json.load(sys.stdin)
    if _req["op"] == "apply":
        _out = apply(_req["config"], _req["ctx"])
    else:
        _out = clean(_req["result"], _req["ctx"])
    print(json.dumps(_out if _out is not None else {}))
