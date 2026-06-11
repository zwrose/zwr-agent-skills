import os
import textwrap

import pytest

import blocks


def _write_block(d, name, body):
    p = os.path.join(str(d), f"{name}.py")
    open(p, "w").write(textwrap.dedent(body))
    return p


GOOD = """\
    BLOCK_META = {
        "description": "Seed todos via the HTTP API.",
        "config": {"count": "number of todos"},
        "targets": ["http://localhost:3000/api"],
    }

    def apply(config, ctx):
        return {"ids": []}

    def clean(result, ctx):
        pass
"""

DEP_BEARING = """\
    # /// script
    # dependencies = ["pymongo==4.7.2"]
    # ///
    import pymongo  # never imported by the engine — static parse only
    BLOCK_META = {
        "description": "x",
        "config": {},
        "targets": ["mongodb://localhost:27017/app-test"],
    }
"""


def test_read_meta_static_parse_never_executes(tmp_path):
    p = _write_block(tmp_path, "mongo-seeder", DEP_BEARING)
    meta = blocks.read_block_meta(p)  # would ImportError if it executed
    assert meta["targets"] == ["mongodb://localhost:27017/app-test"]


def test_missing_meta_is_error(tmp_path):
    p = _write_block(tmp_path, "bare", "def apply(c, x):\n    pass\n")
    with pytest.raises(blocks.BlockError):
        blocks.read_block_meta(p)


def test_discover_blocks(tmp_path):
    _write_block(tmp_path, "http-seeder", GOOD)
    found = blocks.discover_blocks(str(tmp_path))
    assert set(found) == {"http-seeder"}
    assert found["http-seeder"]["meta"]["targets"]


def test_builtin_shadowing_is_validation_error(tmp_path):
    _write_block(tmp_path, "run-command", GOOD)
    with pytest.raises(blocks.BlockError) as e:
        blocks.discover_blocks(str(tmp_path))
    assert "reserved" in str(e.value)


def test_targets_module_block(tmp_path):
    _write_block(tmp_path, "http-seeder", GOOD)
    found = blocks.discover_blocks(str(tmp_path))
    assert blocks.block_targets("http-seeder", {}, found) == \
        ["http://localhost:3000/api"]


def test_targets_run_command_from_config():
    assert blocks.block_targets(
        "run-command", {"command": ["npm", "run", "seed"],
                        "targets": ["app-test-db"]}, {}) == ["app-test-db"]


def test_missing_or_empty_targets_is_error(tmp_path):
    # run-command without config.targets
    with pytest.raises(blocks.BlockError):
        blocks.block_targets("run-command", {"command": ["x"]}, {})
    # module block with empty targets list
    p = _write_block(tmp_path, "no-targets", """\
        BLOCK_META = {"description": "x", "config": {}, "targets": []}
    """)
    found = blocks.discover_blocks(str(tmp_path))
    with pytest.raises(blocks.BlockError):
        blocks.block_targets("no-targets", {}, found)


def test_unknown_block_is_error():
    with pytest.raises(blocks.BlockError):
        blocks.block_targets("nope", {}, {})


def test_pep723_detection(tmp_path):
    dep = _write_block(tmp_path, "dep", DEP_BEARING)
    plain = _write_block(tmp_path, "plain", GOOD)
    assert blocks.has_pep723(dep) is True
    assert blocks.has_pep723(plain) is False


# r2-code-code-002: indented '# /// script' inside a docstring must NOT match.
DOCSTRING_EXAMPLE = """\
    \"\"\"Block with PEP 723 example in docstring (indented, NOT a real header).

    If this block needs third-party packages, add a PEP 723 header:

        # /// script
        # dependencies = ["requests==2.32.3"]
        # ///
    \"\"\"

    BLOCK_META = {
        "description": "Plain block.",
        "config": {},
        "targets": ["http://localhost:3000"],
    }

    def apply(config, ctx):
        return {}

    def clean(result, ctx):
        pass
"""


def test_pep723_indented_docstring_example_not_matched(tmp_path):
    """A block whose docstring contains the indented example must NOT be detected
    as PEP-723-bearing (the header must be at column 0)."""
    p = _write_block(tmp_path, "with-docstring-example", DOCSTRING_EXAMPLE)
    assert blocks.has_pep723(p) is False


def test_pep723_real_header_at_column_0_matched(tmp_path):
    """A real PEP 723 header starting at column 0 must be detected."""
    p = _write_block(tmp_path, "real-pep723", DEP_BEARING)
    assert blocks.has_pep723(p) is True
