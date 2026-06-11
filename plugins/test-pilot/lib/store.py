#!/usr/bin/env python3
"""test-pilot storage resolver + artifact key derivation.

artifact_key() is THE one key-derivation function for every artifact name
that embeds branch+slot identity (manifests, plan records, fallback files,
comment markers). Injective: % is encoded before /, and the slot delimiter ~
is illegal in git refnames, so distinct (branch, slot) pairs never collide.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def sanitize_branch(branch):
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError("empty branch name")
    return branch.replace("%", "%25").replace("/", "%2F")


def artifact_key(branch, slot=None):
    if slot is not None and not SLOT_RE.match(slot):
        raise ValueError(
            f"invalid slot {slot!r}: must match {SLOT_RE.pattern}")
    key = sanitize_branch(branch)
    return f"{key}~{slot}" if slot is not None else key
