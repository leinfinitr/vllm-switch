from controller.policies import (
    AlwaysAwakePreviousPolicy,
    AlwaysSleepPreviousPolicy,
    make_policy,
)
from controller.state import ModelState


def test_same_target_model_does_not_switch():
    policy = AlwaysSleepPreviousPolicy()

    decision = policy.decide(
        current_active="a",
        target_model="a",
        states={"a": ModelState.AWAKE},
    )

    assert decision.sleep_models == []
    assert decision.wake_model is None
    assert decision.route_model == "a"


def test_different_target_sleeps_previous_and_wakes_target():
    policy = AlwaysSleepPreviousPolicy()

    decision = policy.decide(
        current_active="a",
        target_model="b",
        states={"a": ModelState.AWAKE, "b": ModelState.SLEEPING},
    )

    assert decision.sleep_models == ["a"]
    assert decision.wake_model == "b"
    assert decision.route_model == "b"


def test_no_active_model_wakes_target():
    policy = AlwaysSleepPreviousPolicy()

    decision = policy.decide(
        current_active=None,
        target_model="a",
        states={"a": ModelState.SLEEPING},
    )

    assert decision.sleep_models == []
    assert decision.wake_model == "a"
    assert decision.route_model == "a"


def test_awake_previous_policy_switch_keeps_previous_awake_and_wakes_target():
    policy = AlwaysAwakePreviousPolicy()

    decision = policy.decide(
        current_active="a",
        target_model="b",
        states={"a": ModelState.AWAKE, "b": ModelState.SLEEPING},
    )

    assert decision.sleep_models == []
    assert decision.wake_model == "b"
    assert decision.route_model == "b"
    assert decision.wait_for_active_requests is True
    assert decision.mark_active is False


def test_awake_previous_policy_waits_and_marks_already_awake_target_active():
    policy = AlwaysAwakePreviousPolicy()

    decision = policy.decide(
        current_active="a",
        target_model="b",
        states={"a": ModelState.AWAKE, "b": ModelState.AWAKE},
    )

    assert decision.sleep_models == []
    assert decision.wake_model is None
    assert decision.route_model == "b"
    assert decision.wait_for_active_requests is True
    assert decision.mark_active is True


def test_make_policy_supports_always_awake_previous():
    assert isinstance(make_policy("always_awake_previous"), AlwaysAwakePreviousPolicy)
