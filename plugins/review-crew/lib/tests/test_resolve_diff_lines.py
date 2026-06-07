from resolve_diff_lines import resolve_comment_lines


def test_passes_through_in_hunk_line():
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -1,3 +1,4 @@\n line1\n+line2\n line3\n line4")
    comments = [{"path": "foo.ts", "line": 2, "body": "test comment"}]
    r = resolve_comment_lines(diff, comments)
    assert len(r["resolved"]) == 1
    assert r["resolved"][0] == comments[0]
    assert r["dropped"] == []
    assert r["moved"] == []


def test_moves_out_of_hunk_to_nearest_with_prefix():
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -10,3 +10,4 @@\n line10\n+line11\n line12\n line13")
    comments = [{"path": "foo.ts", "line": 99, "body": "out-of-hunk"}]
    r = resolve_comment_lines(diff, comments)
    assert len(r["resolved"]) == 1
    assert "(Re: line 99)" in r["resolved"][0]["body"]
    assert "out-of-hunk" in r["resolved"][0]["body"]
    assert len(r["moved"]) == 1
    assert r["moved"][0]["originalLine"] == 99


def test_drops_file_not_in_diff():
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -1,1 +1,2 @@\n line1\n+line2")
    comments = [{"path": "missing.ts", "line": 1, "body": "x"}]
    r = resolve_comment_lines(diff, comments)
    assert r["resolved"] == []
    assert len(r["dropped"]) == 1
    assert r["dropped"][0]["reason"] == "file not in diff"


def test_handles_new_file_all_plus_lines():
    diff = ("diff --git a/new.ts b/new.ts\nnew file mode 100644\n--- /dev/null\n+++ b/new.ts\n"
            "@@ -0,0 +1,3 @@\n+line1\n+line2\n+line3")
    comments = [{"path": "new.ts", "line": 2, "body": "on new file"}]
    r = resolve_comment_lines(diff, comments)
    assert len(r["resolved"]) == 1
    assert r["resolved"][0] == comments[0]
    assert r["dropped"] == []


def test_handles_multiple_hunks():
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -1,2 +1,3 @@\n line1\n+line2\n line3\n"
            "@@ -10,2 +11,3 @@\n line11\n+line12\n line13")
    comments = [{"path": "foo.ts", "line": 2, "body": "first hunk"},
                {"path": "foo.ts", "line": 12, "body": "second hunk"}]
    r = resolve_comment_lines(diff, comments)
    assert len(r["resolved"]) == 2
    assert r["dropped"] == []
    assert r["moved"] == []


def test_skips_deletion_lines_when_tracking_new_line_numbers():
    diff = ("diff --git a/foo.ts b/foo.ts\n--- a/foo.ts\n+++ b/foo.ts\n"
            "@@ -1,4 +1,4 @@\n line1\n-OLD line2\n+NEW line2\n line3\n line4")
    r1 = resolve_comment_lines(diff, [{"path": "foo.ts", "line": 2, "body": "on NEW line2"}])
    assert len(r1["resolved"]) == 1
    assert r1["moved"] == []
    r2 = resolve_comment_lines(diff, [{"path": "foo.ts", "line": 99, "body": "far away"}])
    assert len(r2["resolved"]) == 1
    assert "(Re: line 99)" in r2["resolved"][0]["body"]
    assert r2["resolved"][0]["line"] in (1, 2, 3, 4)
