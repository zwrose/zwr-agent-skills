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


def test_scrub_negative_benign_text_unchanged():
    benign = ("Console error: TypeError: cannot read 'title' of undefined\n"
              "POST /api/todos returned 500\n"
              "The token bucket algorithm limits requests")
    assert pc.scrub(benign) == benign


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
