#!/usr/bin/env python3
"""PR comment management for test-pilot: marker render/find (author-
filtered), edit-else-create upsert via gh, checkbox-state preservation,
diagnostic scrubbing (the ENFORCED redaction rule), and the no-PR local-
fallback file lifecycle. Both skills call this; neither re-implements it.

CLI:
  pr_comment.py upsert --pr N --family plan|results --key K \
      --body-file F [--plans-dir D]
  pr_comment.py scrub            # stdin -> scrubbed stdout
"""
import json
import os
import re
import subprocess
import sys

MARKER_FAMILIES = ("plan", "results")
# Keys come from store.artifact_key(): %-encoded branch + optional ~slot.
_KEY_RE = re.compile(r"^[^\s]+$")


def render_marker(family, key):
    if family not in MARKER_FAMILIES:
        raise ValueError(f"unknown marker family {family!r}")
    if not _KEY_RE.match(key) or "-->" in key:
        raise ValueError(
            f"marker key {key!r} is not a sanitized artifact key; derive it "
            f"with store.artifact_key()")
    return f"<!-- test-pilot-{family}: {key} -->"


def find_comment(comments, family, key, author):
    """First comment by `author` containing the exact marker. Planted markers
    from other authors are ignored."""
    marker = render_marker(family, key)
    for c in comments:
        if c.get("author") == author and marker in c.get("body", ""):
            return c
    return None


_CHECKBOX_RE = re.compile(r"^(\s*- )\[([ x])\](\s+.*)$")


def merge_checkboxes(old_body, new_body):
    """Keep [x] for steps whose text is unchanged; new/reworded steps reset."""
    checked = set()
    for line in old_body.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m and m.group(2) == "x":
            checked.add(m.group(3).strip())
    out = []
    for line in new_body.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m and m.group(3).strip() in checked:
            line = f"{m.group(1)}[x]{m.group(3)}"
        out.append(line)
    return "\n".join(out)


_SCRUB_PATTERNS = [
    (re.compile(r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|"
                r"set-cookie|x-api-key)\s*:\s*).+$"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(session[_-]?id|session|sid|token|api[_-]?key|"
                r"access[_-]?token|refresh[_-]?token)=([^&\s;\"']+)"),
     r"\1=[REDACTED]"),
]


def scrub(text):
    """Strip auth material from a diagnostic before it can reach a comment."""
    for pat, repl in _SCRUB_PATTERNS:
        text = pat.sub(repl, text)
    return text


def fallback_path(plans_dir, key, family):
    suffix = ".results.md" if family == "results" else ".md"
    return os.path.join(plans_dir, f"{key}{suffix}")


def write_fallback(plans_dir, key, family, body):
    os.makedirs(plans_dir, exist_ok=True)
    with open(fallback_path(plans_dir, key, family), "w") as fh:
        fh.write(body)


def delete_fallback(plans_dir, key, family):
    try:
        os.unlink(fallback_path(plans_dir, key, family))
    except FileNotFoundError:
        pass


# ---- gh-backed upsert (acceptance-tested, not unit-tested) ----

def _gh(*args, inp=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True,
                       timeout=60, input=inp)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}... failed: "
                           f"{r.stderr.strip()[:300]}")
    return r.stdout


def gh_user():
    return _gh("api", "user", "-q", ".login").strip()


def list_comments(pr):
    raw = json.loads(_gh("api", f"repos/{{owner}}/{{repo}}/issues/{pr}/comments",
                         "--paginate"))
    return [{"id": c["id"], "author": c["user"]["login"], "body": c["body"]}
            for c in raw]


def upsert(pr, family, key, body, plans_dir=None):
    """Edit-else-create the marker-managed comment; preserve plan checkboxes;
    delete the local fallback file once posted. Returns {"action", "id"}."""
    marker = render_marker(family, key)
    if marker not in body:
        body = f"{marker}\n{body}"
    author = gh_user()
    existing = find_comment(list_comments(pr), family, key, author)
    if existing:
        if family == "plan":
            body = merge_checkboxes(existing["body"], body)
        _gh("api", "-X", "PATCH",
            f"repos/{{owner}}/{{repo}}/issues/comments/{existing['id']}",
            "-f", f"body={body}")
        action, cid = "edited", existing["id"]
    else:
        out = _gh("api", "-X", "POST",
                  f"repos/{{owner}}/{{repo}}/issues/{pr}/comments",
                  "-f", f"body={body}")
        action, cid = "created", json.loads(out)["id"]
    if plans_dir:
        delete_fallback(plans_dir, key, family)
    return {"action": action, "id": cid}


def _arg(args, flag):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main(argv):
    args = argv[1:]
    cmd = args[0] if args else None
    try:
        if cmd == "scrub":
            sys.stdout.write(scrub(sys.stdin.read()))
            return 0
        if cmd == "upsert":
            pr = _arg(args, "--pr")
            family = _arg(args, "--family")
            key = _arg(args, "--key")
            body_file = _arg(args, "--body-file")
            if not all((pr, family, key, body_file)):
                sys.stderr.write("usage: upsert --pr N --family plan|results "
                                 "--key K --body-file F [--plans-dir D]\n")
                return 2
            body = open(body_file).read()
            out = upsert(pr, family, key, body, _arg(args, "--plans-dir"))
            sys.stdout.write(json.dumps({"ok": True, **out}) + "\n")
            return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        return 1
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
