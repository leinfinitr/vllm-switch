from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelSpec(BaseModel):
    """Configuration for one single-model vLLM backend."""

    model_config = ConfigDict(frozen=True)

    backend_url: str
    served_model_name: str
    sleep_level: int = Field(default=1, ge=1, le=2)
    wake_tags: list[str] | None = None
    launch_command: list[str] | None = None
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class ControllerSettings(BaseModel):
    """Configuration for the external controller process."""

    host: str = "0.0.0.0"
    port: int = 9000
    policy: str = "always_sleep_previous"
    startup_awake_model: str | None = None
    request_timeout_s: float = 600
    switch_timeout_s: float = 600
    metrics_path: str = "results/controller_events.jsonl"
    cpu_backup_global_cap_bytes: int | None = Field(default=None, ge=0)
    cpu_backup_default_model_priority: int = 0
    cpu_backup_model_priorities: dict[str, int] = Field(default_factory=dict)
    cpu_memory_reclaim_available_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    cpu_memory_recovery_available_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    cpu_memory_reclaim_available_bytes: int = Field(default=0, ge=0)
    cpu_memory_recovery_available_bytes: int = Field(default=0, ge=0)
    cpu_memory_poll_interval_s: float = Field(default=0.5, gt=0)
    cpu_memory_pressure_consecutive_samples: int = Field(default=3, ge=1)
    cpu_memory_reclaim_cooldown_s: float = Field(default=2.0, ge=0)

    @model_validator(mode="after")
    def validate_memory_pressure_watermarks(self) -> "ControllerSettings":
        low_ratio = self.cpu_memory_reclaim_available_ratio
        high_ratio = self.cpu_memory_recovery_available_ratio
        if low_ratio is not None and high_ratio is None:
            raise ValueError(
                "cpu_memory_recovery_available_ratio is required when "
                "cpu_memory_reclaim_available_ratio is configured"
            )
        if low_ratio is not None and high_ratio is not None and high_ratio < low_ratio:
            raise ValueError("CPU memory recovery ratio must be >= reclaim ratio")
        if self.cpu_memory_recovery_available_bytes < self.cpu_memory_reclaim_available_bytes:
            raise ValueError("CPU memory recovery bytes must be >= reclaim bytes")
        return self


class ControllerConfig(BaseModel):
    """Top-level model switch controller config."""

    models: dict[str, ModelSpec]
    controller: ControllerSettings = Field(default_factory=ControllerSettings)

    @model_validator(mode="after")
    def validate_startup_model(self) -> "ControllerConfig":
        startup = self.controller.startup_awake_model
        if startup is not None and startup not in self.models:
            raise ValueError(f"startup_awake_model {startup!r} is not in configured models")
        if not self.models:
            raise ValueError("at least one model must be configured")
        return self


def load_config(path: str | Path) -> ControllerConfig:
    """Load a controller config from YAML."""

    with Path(path).open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return ControllerConfig.model_validate(raw)
