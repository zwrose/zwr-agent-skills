from resolve_diff_lines import parse_diff_lines, resolve_comment_lines


def test_crlf_diff_does_not_corrupt_filename():
    # CRLF line endings must not leave a trailing \r in the parsed file path.
    diff = ("diff --git a/foo.ts b/foo.ts\r\n--- a/foo.ts\r\n+++ b/foo.ts\r\n"
            "@@ -1,3 +1,4 @@\r\n line1\r\n+line2\r\n line3\r\n line4\r\n")
    valid = parse_diff_lines(diff)
    assert "foo.ts" in valid          # NOT "foo.ts\r"
    assert "foo.ts\r" not in valid
    r = resolve_comment_lines(diff, [{"path": "foo.ts", "line": 2, "body": "c"}])
    assert len(r["resolved"]) == 1
    assert r["dropped"] == []


def test_no_prefix_diff_is_unsupported_and_dropped():
    # Documented limitation: '+++ foo.ts' (no b/ prefix) is not recognized.
    diff = ("diff --git foo.ts foo.ts\n--- foo.ts\n+++ foo.ts\n"
            "@@ -1,1 +1,2 @@\n line1\n+line2")
    r = resolve_comment_lines(diff, [{"path": "foo.ts", "line": 2, "body": "c"}])
    assert r["resolved"] == []
    assert len(r["dropped"]) == 1
    assert r["dropped"][0]["reason"] == "file not in diff"


def test_tie_break_true_equidistant_prefers_lower_line():
    # Two hunks leave a gap: valid right-side lines are {2, 4}. A comment at
    # line 3 is equidistant (distance 1) from both -> the tie must resolve to
    # the LOWER line, 2.
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -1,0 +2,1 @@\n+b\n"
            "@@ -5,0 +4,1 @@\n+d")
    valid = parse_diff_lines(diff)
    assert valid["foo.ts"] == {2, 4}
    r = resolve_comment_lines(diff, [{"path": "foo.ts", "line": 3, "body": "tie"}])
    assert r["resolved"][0]["line"] == 2
    assert "(Re: line 3)" in r["resolved"][0]["body"]
