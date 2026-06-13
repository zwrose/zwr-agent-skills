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
# Legitimate keys are %-encoded so '/' never appears literally; '..' and '-->'
# are also forbidden. fullmatch closes the trailing-newline gap ($ matches
# before \n in Python; fullmatch does not).
_KEY_RE = re.compile(r"[^\s/\\]+")


def _valid_marker_key(key):
    """Return True iff key is an acceptable sanitized artifact key.

    Combines: no whitespace/slash/backslash (via _KEY_RE fullmatch, which
    unlike match/search also rejects trailing newlines), no '-->' sequence
    (HTML comment close), and no '..' sequence (path-traversal defense).
    """
    return bool(_KEY_RE.fullmatch(key)) and "-->" not in key and ".." not in key


def render_marker(family, key):
    if family not in MARKER_FAMILIES:
        raise ValueError(f"unknown marker family {family!r}")
    if not _valid_marker_key(key):
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


# Shared sensitive key-name alternation used by patterns 3 and 4.
# x-api-key is intentionally absent here: it is handled by patterns 1 and 1b
# (the colon-separator forms are disambiguated there).
_SECRET_KEY_NAMES = (
    r"session[_-]?id|session|sid|token|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|password|passwd|pwd|"
    r"client[_-]?secret"
)

_SCRUB_PATTERNS = [
    # Pattern 1: line-anchored HTTP headers (e.g. "Authorization: Bearer ...")
    (re.compile(r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|"
                r"set-cookie|x-api-key|x-api[_-]?key)\s*:\s*).+$"), r"\1[REDACTED]"),
    # Pattern 1b: mid-line x-api-key (dict-dump and request-log forms)
    # e.g. {'x-api-key': 'abc123'} or {'x-api-key': 'abc def'} (space in value)
    # or "request headers: x-api-key: abc123".
    # or stringified-JSON form: {\"x-api-key\": \"sk-live-abc123\"}.
    # Authorization is handled by pattern 1 (line-anchored) and pattern 2 (bearer).
    # Value alternation: quoted (full string, no newline, backslash-escaped quote
    # tolerance mirrors pattern 4) first, then unquoted fallback (excludes
    # structural chars }/'", so re-scrubbing [REDACTED] is idempotent).
    (re.compile(r"""(?i)(?<!\w)(x[_-]?api[_-]?key)(?:\\?[\"'])?\s*:\s*"""
                r"""(?:\\?\"[^\"\n]*\\?\"|\\?'[^'\n]*\\?'|[^\s}'",]+)"""),
     r"\1: [REDACTED]"),
    # Pattern 2: Bearer tokens
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    # Pattern 3: key=value query/form params
    (re.compile(r"(?i)\b(" + _SECRET_KEY_NAMES + r"|x[_-]?api[_-]?key)"
                r"=([^&\s;\"']+)"),
     r"\1=[REDACTED]"),
    # Pattern 4: Colon-separator (JSON/object/dict) forms: "key": "value" or
    # 'key': 'value'. Tolerates optional backslash before each quote (so
    # stringified-JSON forms like \"access_token\":\"x\" are caught too).
    # Value class excludes newlines to prevent cross-line over-redaction.
    (re.compile(r"(?i)(\\?[\"'](?:" + _SECRET_KEY_NAMES + r")\\?[\"']\s*:\s*)"
                r"(?:\\?\"[^\"\n]*\\?\"|\\?'[^'\n]*\\?')"),
     r"\1[REDACTED]"),
    # URI userinfo credentials: scheme://user:pass@host -> scheme://[REDACTED]@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^@\s/]+)@"),
     r"\1:[REDACTED]@"),
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


def _parse_paginated_arrays(text):
    """gh api --paginate concatenates page outputs; parse them all."""
    dec, idx, items = json.JSONDecoder(), 0, []
    while idx < len(text):
        while idx < len(text) and text[idx] in " \r\n\t":
            idx += 1
        if idx >= len(text):
            break
        obj, idx = dec.raw_decode(text, idx)
        items.extend(obj if isinstance(obj, list) else [obj])
    return items


def list_comments(pr):
    raw = _parse_paginated_arrays(_gh(
        "api", f"repos/{{owner}}/{{repo}}/issues/{pr}/comments", "--paginate"))
    return [{"id": c["id"], "author": c["user"]["login"], "body": c["body"]}
            for c in raw]


def upsert(pr, family, key, body, plans_dir=None):
    """Edit-else-create the marker-managed comment; preserve plan checkboxes;
    delete the local fallback file once posted. Returns {"action", "id"}."""
    marker = render_marker(family, key)
    body = scrub(body)
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


def _arg(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return default


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
            if not pr.isdigit():
                sys.stderr.write(f"error: --pr must be a numeric PR number, "
                                 f"got {pr!r}\n")
                return 2
            with open(body_file) as _fh:
                body = _fh.read()
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
