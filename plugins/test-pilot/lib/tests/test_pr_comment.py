import os

import pytest

import pr_comment as pc


def test_marker_render_uses_key_verbatim():
    assert pc.render_marker("plan", "feat%2Fx~admin") == \
        "<!-- test-pilot-plan: feat%2Fx~admin -->"
    assert pc.render_marker("results", "main") == \
        "<!-- test-pilot-results: main -->"
    with pytest.raises(ValueError):
        pc.render_marker("bogus", "k")
    with pytest.raises(ValueError):
        pc.render_marker("plan", "evil -->key")  # raw, unsanitized input
    # test-003: --> branch in isolation (no whitespace, only --> violation)
    with pytest.raises(ValueError):
        pc.render_marker("plan", "evil-->key")
    # r3v-3-test-001: trailing newline is rejected (fullmatch, not $-anchored match)
    with pytest.raises(ValueError):
        pc.render_marker("plan", "feat%2Fx\n")


def _c(cid, author, body):
    return {"id": cid, "author": author, "body": body}


def test_find_comment_author_filtered_and_discriminating():
    me, evil = "zwrose", "mallory"
    comments = [
        _c(1, evil, pc.render_marker("plan", "feat%2Fx") + "\nplanted"),
        _c(2, me, pc.render_marker("results", "feat%2Fx") + "\nresults"),
        _c(3, me, pc.render_marker("plan", "feat%2Fx~admin") + "\nslotted"),
        _c(4, me, pc.render_marker("plan", "feat%2Fx") + "\nreal plan"),
    ]
    # planted marker from another author ignored
    found = pc.find_comment(comments, "plan", "feat%2Fx", me)
    assert found["id"] == 4
    # family discrimination
    assert pc.find_comment(comments, "results", "feat%2Fx", me)["id"] == 2
    # slot discrimination
    assert pc.find_comment(comments, "plan", "feat%2Fx~admin", me)["id"] == 3
    # no match -> None
    assert pc.find_comment(comments, "results", "other", me) is None


def test_checkbox_state_preserved_for_unchanged_steps():
    old = ("- [x] Open /todos and add an item\n"
           "- [ ] Delete an item\n"
           "- [x] Step that got reworded\n")
    new = ("- [ ] Open /todos and add an item\n"   # unchanged -> stays checked
           "- [ ] Delete an item\n"                 # unchecked -> stays
           "- [ ] Step with brand new wording\n")   # changed -> resets
    merged = pc.merge_checkboxes(old, new)
    assert "- [x] Open /todos and add an item" in merged
    assert "- [ ] Delete an item" in merged
    assert "- [ ] Step with brand new wording" in merged


def test_scrub_strips_auth_material():
    s = pc.scrub
    assert "[REDACTED]" in s("Authorization: Bearer abc123def456ghi789")
    assert "abc123def456ghi789" not in s("Authorization: Bearer abc123def456ghi789")
    assert "[REDACTED]" in s("Set-Cookie: session=deadbeef; HttpOnly")
    assert "deadbeef" not in s("Set-Cookie: session=deadbeef; HttpOnly")
    assert "[REDACTED]" in s("cookie: sid=s3cr3tt0ken99")
    assert "[REDACTED]" in s("GET /api?access_token=xyzzy12345")
    assert "xyzzy12345" not in s("GET /api?access_token=xyzzy12345")
    assert "[REDACTED]" in s("bearer 0123456789abcdef")
    # security-001: password/passwd/pwd/client_secret key=value forms
    assert "[REDACTED]" in s("password=hunter2")
    assert "hunter2" not in s("password=hunter2")
    assert "[REDACTED]" in s("passwd=s3cr3t")
    assert "[REDACTED]" in s("pwd=mypass123")
    assert "[REDACTED]" in s("client_secret=abc123xyz")
    assert "[REDACTED]" in s("client-secret=abc123xyz")
    # security-001: URI userinfo credentials
    assert "[REDACTED]" in s("mongodb://app:s3cret@host/db")
    assert "s3cret" not in s("mongodb://app:s3cret@host/db")
    assert "[REDACTED]" in s("postgres://user:pass@localhost/mydb")
    assert "pass" not in s("postgres://user:pass@localhost/mydb")


def test_scrub_strips_json_colon_form_secrets():
    s = pc.scrub
    # Double-quoted key and value (standard JSON)
    assert "[REDACTED]" in s('{"access_token": "xyzzy12345"}')
    assert "xyzzy12345" not in s('{"access_token": "xyzzy12345"}')
    assert "[REDACTED]" in s("{'client_secret': 'abc123'}")
    assert "abc123" not in s("{'client_secret': 'abc123'}")
    assert "[REDACTED]" in s('"password": "hunter2"')
    assert "hunter2" not in s('"password": "hunter2"')
    # session_id colon form
    assert "[REDACTED]" in s('"session_id": "sess-abc"')
    assert "sess-abc" not in s('"session_id": "sess-abc"')


def test_scrub_strips_quoted_values_with_spaces_commas_braces():
    """r3-4: quoted secret values containing spaces, commas, or braces are redacted."""
    s = pc.scrub
    # Value contains a space
    assert "[REDACTED]" in s('"password": "hunter two"')
    assert "hunter two" not in s('"password": "hunter two"')
    # Value contains a comma
    assert "[REDACTED]" in s('"client_secret": "a,b"')
    assert "a,b" not in s('"client_secret": "a,b"')
    # Value contains braces (JSON-style object snippet)
    assert "[REDACTED]" in s('"access_token": "{tok: xyz}"')
    assert "{tok: xyz}" not in s('"access_token": "{tok: xyz}"')
    # Single-quoted variant with spaces
    assert "[REDACTED]" in s("'password': 'my secret pass'")
    assert "my secret pass" not in s("'password': 'my secret pass'")


def test_scrub_negative_benign_text_unchanged():
    benign = ("Console error: TypeError: cannot read 'title' of undefined\n"
              "POST /api/todos returned 500\n"
              "The token bucket algorithm limits requests")
    assert pc.scrub(benign) == benign


# Fix 3: paginated gh output parser.
def test_parse_paginated_arrays_handles_concatenated_pages():
    text = '[{"id": 1}, {"id": 2}]\n[{"id": 3}]'
    assert pc._parse_paginated_arrays(text) == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert pc._parse_paginated_arrays("[]") == []
    assert pc._parse_paginated_arrays("") == []


# Fix 6: scrub is idempotent.
def test_scrub_is_idempotent():
    s = "Authorization: Bearer abc123def456\nplain text"
    assert pc.scrub(pc.scrub(s)) == pc.scrub(s)


# r3v-2-security-001: pattern 1b must redact escaped-quote (stringified-JSON) x-api-key.
def test_scrub_catches_escaped_json_x_api_key():
    s = pc.scrub
    raw = r'{"x-api-key": \"sk-live-abc123\"}'
    out = s(raw)
    assert "sk-live-abc123" not in out
    assert "[REDACTED]" in out


# r3v-1-code-002: scrub is idempotent for dict-dump x-api-key forms (brace survives re-scrub).
def test_scrub_is_idempotent_dict_dump_x_api_key():
    raw = "{'x-api-key': 'abc123xyz'}"
    once = pc.scrub(raw)
    twice = pc.scrub(once)
    assert once == twice


# test-001: upsert unit tests via monkeypatched seams
def _extract_body_from_gh_args(args):
    """Extract the body value from -f body=<value> in gh call args."""
    for i, a in enumerate(args):
        if a == "-f" and i + 1 < len(args) and args[i + 1].startswith("body="):
            return args[i + 1][len("body="):]
    return None


def test_upsert_creates_when_no_existing_comment(monkeypatch, tmp_path):
    """No existing comment -> POST path, action 'created', marker injected."""
    monkeypatch.setattr(pc, "gh_user", lambda: "zwrose")
    monkeypatch.setattr(pc, "list_comments", lambda pr: [])
    posted = {}

    def fake_gh(*args, inp=None):
        if args[0] == "api" and args[1] == "-X" and args[2] == "POST":
            posted["body"] = _extract_body_from_gh_args(args)
            return '{"id": 42}'
        return ""

    monkeypatch.setattr(pc, "_gh", fake_gh)
    result = pc.upsert(123, "plan", "feat%2Fx", "body text")
    assert result["action"] == "created"
    assert result["id"] == 42
    marker = pc.render_marker("plan", "feat%2Fx")
    assert posted["body"] is not None
    assert marker in posted["body"]


def test_upsert_edits_existing_comment(monkeypatch):
    """Existing comment -> PATCH path, action 'edited' (idempotent)."""
    key = "feat%2Fx"
    marker = pc.render_marker("plan", key)
    existing_body = f"{marker}\n- [ ] step one"
    monkeypatch.setattr(pc, "gh_user", lambda: "zwrose")
    monkeypatch.setattr(pc, "list_comments",
                        lambda pr: [{"id": 7, "author": "zwrose",
                                     "body": existing_body}])
    patched = {}

    def fake_gh(*args, inp=None):
        if args[0] == "api" and args[1] == "-X" and args[2] == "PATCH":
            patched["called"] = True
            return ""
        return ""

    monkeypatch.setattr(pc, "_gh", fake_gh)
    result = pc.upsert(123, "plan", key, f"{marker}\n- [ ] step one")
    assert result["action"] == "edited"
    assert result["id"] == 7
    assert patched.get("called") is True


def test_upsert_plan_family_merges_checkboxes(monkeypatch):
    """family='plan' -> checked steps from existing body preserved in new body."""
    key = "feat%2Fx"
    marker = pc.render_marker("plan", key)
    existing_body = f"{marker}\n- [x] Open the page\n- [ ] Delete an item"
    monkeypatch.setattr(pc, "gh_user", lambda: "zwrose")
    monkeypatch.setattr(pc, "list_comments",
                        lambda pr: [{"id": 5, "author": "zwrose",
                                     "body": existing_body}])
    patched_body = {}

    def fake_gh(*args, inp=None):
        if args[0] == "api" and args[1] == "-X" and args[2] == "PATCH":
            patched_body["v"] = _extract_body_from_gh_args(args)
        return ""

    monkeypatch.setattr(pc, "_gh", fake_gh)
    pc.upsert(123, "plan", key, f"{marker}\n- [ ] Open the page\n- [ ] Delete an item")
    assert "- [x] Open the page" in patched_body.get("v", "")


def test_upsert_results_family_does_not_merge_checkboxes(monkeypatch):
    """family='results' -> checkboxes NOT merged from existing body."""
    key = "feat%2Fx"
    marker = pc.render_marker("results", key)
    existing_body = f"{marker}\n- [x] Some step"
    monkeypatch.setattr(pc, "gh_user", lambda: "zwrose")
    monkeypatch.setattr(pc, "list_comments",
                        lambda pr: [{"id": 9, "author": "zwrose",
                                     "body": existing_body}])
    patched_body = {}

    def fake_gh(*args, inp=None):
        if args[0] == "api" and args[1] == "-X" and args[2] == "PATCH":
            patched_body["v"] = _extract_body_from_gh_args(args)
        return ""

    monkeypatch.setattr(pc, "_gh", fake_gh)
    pc.upsert(123, "results", key, f"{marker}\n- [ ] Some step")
    assert "- [ ] Some step" in patched_body.get("v", "")
    assert "- [x] Some step" not in patched_body.get("v", "")


def test_upsert_deletes_fallback_after_post(monkeypatch, tmp_path):
    """Fallback file is deleted after a successful post."""
    key = "feat%2Fx"
    plans = str(tmp_path / "plans")
    pc.write_fallback(plans, key, "plan", "some body")
    fallback = pc.fallback_path(plans, key, "plan")
    assert os.path.exists(fallback)
    monkeypatch.setattr(pc, "gh_user", lambda: "zwrose")
    monkeypatch.setattr(pc, "list_comments", lambda pr: [])
    monkeypatch.setattr(pc, "_gh", lambda *a, inp=None: '{"id": 1}')
    pc.upsert(123, "plan", key, "body text", plans_dir=plans)
    assert not os.path.exists(fallback)


# fl-secu-security-001: scrub catches backslash-escaped quotes (stringified JSON).
def test_scrub_catches_escaped_json_token():
    s = pc.scrub
    # Stringified-JSON form: \"access_token\":\"eyJhbGc...\"
    raw = r'{"access_token": \"eyJhbGciOiJIUzI1NiJ9\"}'
    out = s(raw)
    assert "[REDACTED]" in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


# fl-secu-security-002: unbalanced quote must not span newlines / cascade.
def test_scrub_unbalanced_quote_does_not_span_newlines():
    # First value is truncated (unbalanced closing quote).
    # The second secret on the next line must STILL be redacted.
    text = '"password": "truncated\n"token": "SECRET123"'
    out = pc.scrub(text)
    assert "SECRET123" not in out
    assert "[REDACTED]" in out
    # The intervening newline and second-line key must survive (not be swallowed).
    assert "\n" in out


# fl-secu-security-003: dict-dump x-api-key and mid-line header forms are caught.
def test_scrub_catches_dict_dump_x_api_key():
    s = pc.scrub
    # Python dict dump form.
    assert "[REDACTED]" in s("{'x-api-key': 'abc123xyz'}")
    assert "abc123xyz" not in s("{'x-api-key': 'abc123xyz'}")
    # Mid-line x-api-key header in a log line.
    assert "[REDACTED]" in s("request headers: x-api-key: abc123xyz")
    assert "abc123xyz" not in s("request headers: x-api-key: abc123xyz")
    # Mid-line Authorization: Bearer is caught by the existing bearer pattern.
    assert "[REDACTED]" in s("see also: bearer tok1234567890abc")
    assert "tok1234567890abc" not in s("see also: bearer tok1234567890abc")


def test_scrub_negative_x_api_key_benign_unchanged():
    """A key name that only partially matches must not trigger redaction."""
    benign = "x-request-id: abc123"
    assert pc.scrub(benign) == benign


# r2v-code-code-001: pattern 1b must fully redact quoted x-api-key values with whitespace.
def test_scrub_x_api_key_quoted_value_with_space():
    """{'x-api-key': 'abc def'} — the post-whitespace portion ('def') must not leak."""
    s = pc.scrub
    result = s("{'x-api-key': 'abc def'}")
    assert "[REDACTED]" in result
    assert "def" not in result


# fl-secu-security-004: render_marker and fallback_path reject traversal keys.
def test_render_marker_rejects_path_separator_in_key():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        pc.render_marker("plan", "../../etc/passwd")


def test_render_marker_rejects_backslash_in_key():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        pc.render_marker("plan", "key\\name")


def test_render_marker_rejects_dotdot_in_key():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        pc.render_marker("plan", "feat%2Fx..evil")


def test_fallback_lifecycle(tmp_path):
    plans = str(tmp_path / "plans")
    p = pc.fallback_path(plans, "feat%2Fx~admin", "plan")
    r = pc.fallback_path(plans, "feat%2Fx~admin", "results")
    assert p.endswith("feat%2Fx~admin.md")
    assert r.endswith("feat%2Fx~admin.results.md")
    pc.write_fallback(plans, "feat%2Fx~admin", "plan", "body")
    assert open(p).read() == "body"
    pc.delete_fallback(plans, "feat%2Fx~admin", "plan")
    assert not os.path.exists(p)
    pc.delete_fallback(plans, "feat%2Fx~admin", "plan")  # idempotent
