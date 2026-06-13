#!/usr/bin/env python3
"""File lock guarding concurrent engine applies (parallel worktree agents)."""
import json
import os
import socket
import time


class LockHeld(Exception):
    def __init__(self, holder):
        self.holder = holder or {}
        super().__init__(f"engine lock held by {self.holder}")


def _holder_info():
    return {"pid": os.getpid(), "host": socket.gethostname(),
            "acquiredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def acquire(lock_path):
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise LockHeld(read_holder(lock_path)) from None
    with os.fdopen(fd, "w") as fh:
        json.dump(_holder_info(), fh)


def read_holder(lock_path):
    try:
        with open(lock_path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def is_stale(lock_path):
    """True only when the recorded pid is provably dead on THIS host."""
    h = read_holder(lock_path)
    if h.get("host") != socket.gethostname() or not h.get("pid"):
        return False
    try:
        os.kill(int(h["pid"]), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, ValueError, OverflowError):
        return False
    return False


def release(lock_path):
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass
