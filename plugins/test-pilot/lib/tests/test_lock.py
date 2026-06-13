import json
import os

import pytest

import lock


def test_acquire_release(tmp_path):
    p = str(tmp_path / "state" / "engine.lock")
    lock.acquire(p)
    assert os.path.exists(p)
    holder = lock.read_holder(p)
    assert holder["pid"] == os.getpid()
    lock.release(p)
    assert not os.path.exists(p)


def test_contention_raises_with_holder_info(tmp_path):
    p = str(tmp_path / "engine.lock")
    lock.acquire(p)
    with pytest.raises(lock.LockHeld) as e:
        lock.acquire(p)
    assert e.value.holder["pid"] == os.getpid()
    lock.release(p)


def test_live_lock_is_not_stale(tmp_path):
    p = str(tmp_path / "engine.lock")
    lock.acquire(p)        # held by THIS live pid
    assert lock.is_stale(p) is False
    lock.release(p)


def test_dead_pid_lock_is_stale(tmp_path):
    p = str(tmp_path / "engine.lock")
    lock.acquire(p)
    h = lock.read_holder(p)
    h["pid"] = 99999999     # not a live pid
    json.dump(h, open(p, "w"))
    assert lock.is_stale(p) is True


def test_release_missing_is_noop(tmp_path):
    lock.release(str(tmp_path / "nope.lock"))  # must not raise
