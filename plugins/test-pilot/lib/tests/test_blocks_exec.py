import json
import os
import textwrap

import pytest

import blocks


ECHO_BLOCK = """\
    BLOCK_META = {"description": "echo", "config": {}, "targets": ["test-db"]}

    def apply(config, ctx):
        return {"echo": config["msg"], "root": ctx["repoRoot"]}

    def clean(result, ctx):
        return {"cleaned": result["echo"]}

    if __name__ == "__main__":
        import json, sys
        req = json.load(sys.stdin)
        if req["op"] == "apply":
            out = apply(req["config"], req["ctx"])
        else:
            out = clean(req["result"], req["ctx"])
        print(json.dumps(out if out is not None else {}))
"""

DEP_BLOCK = """\
    # /// script
    # dependencies = ["pymongo==4.7.2"]
    # ///
    BLOCK_META = {"description": "x", "config": {}, "targets": ["t"]}
"""

BAD_OUTPUT_BLOCK = """\
    BLOCK_META = {"description": "x", "config": {}, "targets": ["t"]}
    if __name__ == "__main__":
        print("this is not json")
"""


def _blocks_dir(tmp_path, **named):
    d = str(tmp_path / "blocks")
    os.makedirs(d, exist_ok=True)
    for name, body in named.items():
        open(os.path.join(d, f"{name}.py"), "w").write(textwrap.dedent(body))
    return d


def test_apply_and_clean_round_trip(tmp_path):
    d = _blocks_dir(tmp_path, echo=ECHO_BLOCK)
    found = blocks.discover_blocks(d)
    ctx = {"repoRoot": str(tmp_path)}
    result = blocks.run_block("echo", "apply", {"msg": "hi"}, ctx, found)
    assert result == {"echo": "hi", "root": str(tmp_path)}
    cleaned = blocks.run_block("echo", "clean", {}, ctx, found, result=result)
    assert cleaned == {"cleaned": "hi"}


def test_non_json_output_is_structured_error(tmp_path):
    d = _blocks_dir(tmp_path, bad=BAD_OUTPUT_BLOCK)
    found = blocks.discover_blocks(d)
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("bad", "apply", {}, {"repoRoot": str(tmp_path)}, found)
    assert e.value.block == "bad"
    assert "non-JSON" in str(e.value)


def test_pep723_routes_through_uv(tmp_path, monkeypatch):
    d = _blocks_dir(tmp_path, dep=DEP_BLOCK)
    found = blocks.discover_blocks(d)
    seen = {}

    def fake_runner(argv, **kw):
        seen["argv"] = argv

        class R:
            returncode = 0
            stdout = "{}"
            stderr = ""
        return R()

    monkeypatch.setattr(blocks.shutil, "which", lambda _: "/usr/bin/uv")
    blocks.run_block("dep", "apply", {}, {"repoRoot": str(tmp_path)}, found,
                     runner=fake_runner)
    assert seen["argv"][:2] == ["uv", "run"]


def test_uv_absent_fails_with_install_hint(tmp_path, monkeypatch):
    d = _blocks_dir(tmp_path, dep=DEP_BLOCK)
    found = blocks.discover_blocks(d)
    monkeypatch.setattr(blocks.shutil, "which", lambda _: None)
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("dep", "apply", {}, {"repoRoot": str(tmp_path)}, found)
    assert "uv" in str(e.value) and "install" in str(e.value).lower()


def test_run_command_apply_and_clean(tmp_path):
    ctx = {"repoRoot": str(tmp_path)}
    cfg = {"command": ["python3", "-c", "print('seeded')"],
           "targets": ["test-db"]}
    result = blocks.run_block("run-command", "apply", cfg, ctx, {})
    assert result["exitCode"] == 0 and "seeded" in result["stdout"]
    # no cleanCommand -> clean is a recorded no-op
    assert blocks.run_block("run-command", "clean", cfg, ctx, {},
                            result=result) == {"skipped": "no cleanCommand"}


def test_run_command_failure_names_block(tmp_path):
    cfg = {"command": ["python3", "-c", "import sys; sys.exit(3)"],
           "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "apply", cfg, {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command" and "exit 3" in str(e.value)
