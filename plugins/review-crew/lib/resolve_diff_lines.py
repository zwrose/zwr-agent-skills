#!/usr/bin/env python3
"""Resolve review comment lines against a unified diff so GitHub won't 422.

Faithful port of resolve-diff-lines.ts. A comment anchored to a (file, line) not
inside a diff hunk on the RIGHT side is rejected by GitHub ("Line could not be
resolved"). This normalizes comments: in-hunk -> pass through; out-of-hunk but
file in diff -> move to nearest valid line + "(Re: line N) " prefix; file not in
diff -> drop.

Deviations from the TS original (intentional): each diff line has a trailing
'\\r' stripped so CRLF diffs don't leave '\\r' in filenames. Limitation: --no-prefix
diffs ('+++ <path>' without 'b/') are NOT recognized; their comments are dropped.
"""
import json
import re
import sys

_HUNK_NEWSTART = re.compile(r"\+(\d+)")


def parse_diff_lines(diff_text):
    """Map each RIGHT-side file path to the set of line numbers valid as anchors
    (added '+' lines and context ' ' lines)."""
    valid = {}
    current_file = None
    new_line = None
    in_hunk = False

    for raw in diff_text.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw  # CRLF-safe
        if line.startswith("diff --git"):
            in_hunk = False
            current_file = None
        elif line.startswith("+++ b/"):
            current_file = line[6:]
            valid.setdefault(current_file, set())
            in_hunk = False
        elif line.startswith("+++ "):
            # +++ /dev/null or --no-prefix form — not a recognized RIGHT file.
            current_file = None
            in_hunk = False
        elif line.startswith("@@ "):
            m = _HUNK_NEWSTART.search(line)
            if m and current_file:
                new_line = int(m.group(1))
                in_hunk = True
            else:
                in_hunk = False
        elif in_hunk and current_file is not None and new_line is not None:
            if line.startswith("+"):
                valid[current_file].add(new_line)
                new_line += 1
            elif line.startswith("-"):
                pass  # deletion: does not advance the new-file counter
            elif line.startswith(" ") or line.startswith("\\"):
                if line.startswith(" "):
                    valid[current_file].add(new_line)
                    new_line += 1
            else:
                in_hunk = False
    return valid


def resolve_comment_lines(diff, comments):
    valid = parse_diff_lines(diff)
    resolved, dropped, moved = [], [], []

    for comment in comments:
        file_lines = valid.get(comment["path"])
        if not file_lines:
            dropped.append({"comment": comment, "reason": "file not in diff"})
            continue
        if comment["line"] in file_lines:
            resolved.append(comment)
            continue
        # Nearest valid line by absolute distance; ties -> lower line.
        nearest = min(file_lines, key=lambda l: (abs(l - comment["line"]), l))
        moved_comment = dict(comment)
        moved_comment["line"] = nearest
        moved_comment["body"] = f"(Re: line {comment['line']}) {comment['body']}"
        resolved.append(moved_comment)
        moved.append({"comment": moved_comment, "originalLine": comment["line"], "newLine": nearest})

    return {"resolved": resolved, "dropped": dropped, "moved": moved}


def main(argv):
    args = argv[1:]
    if len(args) < 2:
        sys.stderr.write("Usage: resolve_diff_lines.py <diff-path> <review-json-path> [--output <out-path>]\n")
        return 1
    diff_path, review_path = args[0], args[1]
    output_path = args[args.index("--output") + 1] if "--output" in args else None

    with open(diff_path, encoding="utf-8") as fh:
        diff_text = fh.read()
    try:
        with open(review_path, encoding="utf-8") as fh:
            review = json.load(fh)
    except (OSError, ValueError) as err:
        sys.stderr.write(f"Failed to parse review JSON at {review_path}: {err}\n")
        return 1
    if not isinstance(review, dict) or not isinstance(review.get("comments"), list):
        sys.stderr.write(f'Review JSON at {review_path} is missing a "comments" array.\n')
        return 1

    result = resolve_comment_lines(diff_text, review["comments"])
    for m in result["moved"]:
        sys.stderr.write(f"MOVED: {m['comment']['path']}:{m['originalLine']} -> {m['newLine']}\n")
    for d in result["dropped"]:
        sys.stderr.write(f"DROPPED: {d['comment']['path']}:{d['comment']['line']} - {d['reason']}\n")

    review["comments"] = result["resolved"]
    output = json.dumps(review, indent=2)
    if output_path:
        with open(output_path, "w") as fh:
            fh.write(output)
        sys.stderr.write(f"Wrote {len(result['resolved'])} comments to {output_path}\n")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
