import pytest

import store


@pytest.mark.parametrize("branch,slot,expected", [
    ("main", None, "main"),
    ("feat/x", None, "feat%2Fx"),
    ("feat/x", "admin", "feat%2Fx~admin"),
    ("release-1.2", None, "release-1.2"),
    ("release-1", "2", "release-1~2"),
    ("50%off", None, "50%25off"),
    ("a%2Fb", None, "a%252Fb"),           # pre-encoded text stays distinct from real /
])
def test_artifact_key(branch, slot, expected):
    assert store.artifact_key(branch, slot) == expected


def test_collision_classes_are_distinct():
    # The spec's named collision: release-1 + slot 2 vs release-1.2 + no slot.
    assert store.artifact_key("release-1", "2") != store.artifact_key("release-1.2")
    # %-encoding collision: feat%2Fx (literal) vs feat/x.
    assert store.artifact_key("a%2Fb") != store.artifact_key("a/b")


def test_invalid_slots_rejected():
    for bad in ("has~tilde", "has space", "dot.slot", "", "-leading"):
        with pytest.raises(ValueError):
            store.artifact_key("main", bad)


def test_empty_branch_rejected():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            store.artifact_key(bad)
