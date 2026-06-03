from typing import NamedTuple, Protocol

from controller.state import ModelState


class SwitchDecision(NamedTuple):
    sleep_models: list[str]
    wake_model: str | None
    route_model: str
    wait_for_active_requests: bool = False
    mark_active: bool = False


class SwitchingPolicy(Protocol):
    def decide(
        self,
        current_active: str | None,
        target_model: str,
        states: dict[str, ModelState],
    ) -> SwitchDecision: ...


class AlwaysSleepPreviousPolicy:
    """First-stage policy: only one model is awake; sleep previous on every switch."""

    def decide(
        self,
        current_active: str | None,
        target_model: str,
        states: dict[str, ModelState],
    ) -> SwitchDecision:
        if target_model not in states:
            raise KeyError(target_model)
        if current_active == target_model and states[target_model] == ModelState.AWAKE:
            return SwitchDecision([], None, target_model)
        if current_active is None or current_active == target_model:
            return SwitchDecision([], target_model, target_model)
        return SwitchDecision([current_active], target_model, target_model)


class AlwaysAwakePreviousPolicy:
    """FCFS-like policy: keep previous model awake and switch after its active requests finish."""

    def decide(
        self,
        current_active: str | None,
        target_model: str,
        states: dict[str, ModelState],
    ) -> SwitchDecision:
        if target_model not in states:
            raise KeyError(target_model)
        if current_active == target_model and states[target_model] == ModelState.AWAKE:
            return SwitchDecision([], None, target_model)
        if states[target_model] == ModelState.AWAKE:
            return SwitchDecision(
                [],
                None,
                target_model,
                wait_for_active_requests=True,
                mark_active=True,
            )
        return SwitchDecision(
            [],
            target_model,
            target_model,
            wait_for_active_requests=True,
        )


def make_policy(name: str) -> SwitchingPolicy:
    if name == "always_sleep_previous":
        return AlwaysSleepPreviousPolicy()
    if name == "always_awake_previous":
        return AlwaysAwakePreviousPolicy()
    raise ValueError(f"unknown switching policy: {name}")
