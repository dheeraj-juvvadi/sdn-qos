"""Unit tests for the policy model (validation, runtime update, reset)."""
import os

import pytest

from controller.policy import Policy, PolicyError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "policies", "default_policy.yaml")


def test_load_default():
    p = Policy.load(PATH)
    c = p.classes()
    assert c["voip"]["priority"] > c["video"]["priority"] > c["bulk"]["priority"] \
        > c["best_effort"]["priority"]
    assert c["voip"]["meter_id"] == 0          # VoIP is never metered
    assert c["bulk"]["meter_id"] > 0
    assert p.uses_meters() and p.uses_queues()


def test_update_and_reset():
    p = Policy.load(PATH)
    p.update({"classes": {"bulk": {"cap_kbps": 2000}}})
    assert p.cls("bulk")["cap_kbps"] == 2000
    p.reset()
    assert p.cls("bulk")["cap_kbps"] == 5000


@pytest.mark.parametrize("patch", [
    {"classes": {"nosuch": {"priority": 1}}},
    {"classes": {"bulk": {"dscp": 99}}},
    {"classes": {"video": {"min_kbps": 20000}}},
    {"enforcement": "magic"},
])
def test_invalid_updates_rejected(patch):
    p = Policy.load(PATH)
    with pytest.raises(PolicyError):
        p.update(patch)
    assert p.cls("bulk")["cap_kbps"] == 5000   # unchanged after rejection
