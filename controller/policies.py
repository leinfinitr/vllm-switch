from typing import NamedTuple, Protocol

from controller.state import ModelState


class SwitchDecision(NamedTuple):
    sleep_models: list[str]
    wake_model: str | None
    route_model: str


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


def make_policy(name: str) -> SwitchingPolicy:
    if name == "always_sleep_previous":
        return AlwaysSleepPreviousPolicy()
    raise ValueError(f"unknown switching policy: {name}")
