import json
import os
import shutil
import subprocess
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

# PEP 723 block with NO third-party deps (just stdlib) so it runs without network.
ZERO_DEP_PEP723_BLOCK = """\
    # /// script
    # dependencies = []
    # ///
    BLOCK_META = {"description": "zero-dep pep723", "config": {}, "targets": ["test-db"]}

    def apply(config, ctx):
        return {"ok": True, "msg": "pep723-ran"}

    def clean(result, ctx):
        return {}

    if __name__ == "__main__":
        import json, sys
        req = json.load(sys.stdin)
        if req["op"] == "apply":
            out = apply(req["config"], req["ctx"])
        else:
            out = clean(req.get("result"), req["ctx"])
        print(json.dumps(out if out is not None else {}))
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


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_pep723_zero_dep_runs_through_real_uv(tmp_path):
    """architecture-005: a PEP-723 block with zero third-party deps round-trips
    through real `uv run` end-to-end."""
    d = _blocks_dir(tmp_path, zerodep=ZERO_DEP_PEP723_BLOCK)
    found = blocks.discover_blocks(d)
    result = blocks.run_block("zerodep", "apply", {}, {"repoRoot": str(tmp_path)},
                              found)
    assert result.get("ok") is True
    assert result.get("msg") == "pep723-ran"


def test_run_command_failure_names_block(tmp_path):
    cfg = {"command": ["python3", "-c", "import sys; sys.exit(3)"],
           "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "apply", cfg, {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command" and "exit 3" in str(e.value)


# test-005: module block nonzero exit
def test_module_block_nonzero_exit_names_block_and_stderr(tmp_path):
    d = _blocks_dir(tmp_path, echo=ECHO_BLOCK)
    found = blocks.discover_blocks(d)

    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "boom"

    def fake_runner(argv, **kw):
        return FakeProc()

    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("echo", "apply", {"msg": "hi"},
                         {"repoRoot": str(tmp_path)}, found, runner=fake_runner)
    assert e.value.block == "echo"
    assert "boom" in str(e.value)
    assert "exit 2" in str(e.value)


# test-005: module block timeout
def test_module_block_timeout_raises_block_error(tmp_path):
    d = _blocks_dir(tmp_path, echo=ECHO_BLOCK)
    found = blocks.discover_blocks(d)

    def fake_runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 600)

    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("echo", "apply", {"msg": "hi"},
                         {"repoRoot": str(tmp_path)}, found, runner=fake_runner)
    assert e.value.block == "echo"
    assert "timed out" in str(e.value).lower()


# test-005: run-command timeout
def test_run_command_timeout_raises_block_error(tmp_path):
    cfg = {"command": ["true"], "targets": ["t"]}

    def fake_runner(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 600)

    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "apply", cfg,
                         {"repoRoot": str(tmp_path)}, {}, runner=fake_runner)
    assert e.value.block == "run-command"
    assert "timed out" in str(e.value).lower()


# r3-3-code-002: non-string / empty-string argv elements raise BlockError.
def test_run_command_non_string_element_in_command(tmp_path):
    cfg = {"command": ["python3", 3], "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "apply", cfg,
                         {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command"
    assert "non-empty" in str(e.value)


# fl-code-code-001: JSON null (None) element must also be caught.
def test_run_command_null_element_in_command_raises_block_error(tmp_path):
    cfg = {"command": ["echo", None], "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "apply", cfg,
                         {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command"
    assert "non-empty" in str(e.value)
    assert "command" in str(e.value)


def test_run_command_null_element_in_clean_command_raises_block_error(tmp_path):
    cfg = {"command": ["echo", "hi"],
           "cleanCommand": ["echo", None],
           "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "clean", cfg,
                         {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command"
    assert "non-empty" in str(e.value)
    assert "cleanCommand" in str(e.value)


def test_run_command_empty_string_element_in_clean_command(tmp_path):
    cfg = {"command": ["python3", "-c", "pass"],
           "cleanCommand": ["python3", ""],
           "targets": ["t"]}
    with pytest.raises(blocks.BlockError) as e:
        blocks.run_block("run-command", "clean", cfg,
                         {"repoRoot": str(tmp_path)}, {})
    assert e.value.block == "run-command"
    assert "cleanCommand" in str(e.value)
