import os
import subprocess
import textwrap

import catalog


BLOCK = """\
    BLOCK_META = {
        "description": "Seed todos via the HTTP API.",
        "config": {"count": "number of todos to create"},
        "targets": ["http://localhost:3000/api"],
    }
"""


def _blocks_dir(tmp_path):
    d = str(tmp_path / "blocks")
    os.makedirs(d)
    open(os.path.join(d, "http-seeder.py"), "w").write(textwrap.dedent(BLOCK))
    return d


def test_generate_lists_builtins_first_then_project_blocks(tmp_path):
    text = catalog.generate(_blocks_dir(tmp_path))
    assert text.index("## run-command") < text.index("## http-seeder")
    assert "Seed todos via the HTTP API." in text
    # declared targets are reviewable
    assert "http://localhost:3000/api" in text
    assert "per-scenario" in text          # run-command's targets note
    assert "do not edit by hand" in text.splitlines()[0]


def test_cli_writes_catalog_file(tmp_path):
    d = _blocks_dir(tmp_path)
    lib = os.path.dirname(os.path.abspath(catalog.__file__))
    r = subprocess.run(["python3", os.path.join(lib, "catalog.py"),
                        "--blocks-dir", d], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(d, "CATALOG.md"))


def test_empty_blocks_dir_still_documents_builtins(tmp_path):
    d = str(tmp_path / "empty")
    os.makedirs(d)
    text = catalog.generate(d)
    assert "## run-command" in text


def test_cli_missing_dir_value_is_usage_error(tmp_path):
    lib = os.path.dirname(os.path.abspath(catalog.__file__))
    r = subprocess.run(["python3", os.path.join(lib, "catalog.py"),
                        "--blocks-dir"], capture_output=True, text=True)
    assert r.returncode == 2
    assert "usage" in r.stderr


# fl-code-code-004: a project block with missing/empty targets shows "(none declared — INVALID)".
def test_generate_invalid_targets_shown_as_invalid(tmp_path):
    d = str(tmp_path / "blocks")
    os.makedirs(d)
    # Block with no targets field.
    open(os.path.join(d, "notargets.py"), "w").write(textwrap.dedent("""\
        BLOCK_META = {
            "description": "missing targets",
            "config": {},
        }
    """))
    text = catalog.generate(d)
    assert "(none declared — INVALID)" in text
    # Must not produce an empty inline code span for targets (`` `` ).
    assert "**Targets:** ``" not in text


# r2v-arch-architecture-001: string-valued targets must also show INVALID (not char-split).
def test_generate_string_targets_shown_as_invalid(tmp_path):
    """A block with targets="test-db" (string, not list) must render INVALID,
    not a char-split list of individual characters."""
    d = str(tmp_path / "blocks")
    os.makedirs(d)
    open(os.path.join(d, "strblock.py"), "w").write(textwrap.dedent("""\
        BLOCK_META = {
            "description": "string targets",
            "config": {},
            "targets": "test-db",
        }
    """))
    text = catalog.generate(d)
    assert "(none declared — INVALID)" in text
    # Ensure individual characters from "test-db" are NOT rendered as targets.
    assert "`t`, `e`" not in text
