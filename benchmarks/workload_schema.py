from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class PatternConfig(BaseModel):
    type: str = "alternating"
    models: list[str]
    burst_size: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_models(self) -> "PatternConfig":
        if not self.models:
            raise ValueError("pattern.models must not be empty")
        if self.type not in {"alternating", "burst"}:
            raise ValueError("pattern.type must be one of: alternating, burst")
        return self


class PromptConfig(BaseModel):
    type: str = "fixed"
    text: str = "Hello"


class OutputConfig(BaseModel):
    max_tokens: int = 64


class WorkloadConfig(BaseModel):
    name: str
    base_url: str = "http://127.0.0.1:9000"
    request_rate: float = Field(default=1.0, gt=0)
    max_requests: int = Field(default=1, ge=1)
    stream: bool = True
    pattern: PatternConfig
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_workload(path: str | Path) -> WorkloadConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return WorkloadConfig.model_validate(raw)


def generate_model_sequence(config: WorkloadConfig) -> list[str]:
    models = config.pattern.models
    sequence: list[str] = []
    if config.pattern.type == "alternating":
        for i in range(config.max_requests):
            sequence.append(models[i % len(models)])
    elif config.pattern.type == "burst":
        idx = 0
        while len(sequence) < config.max_requests:
            model = models[idx % len(models)]
            sequence.extend([model] * config.pattern.burst_size)
            idx += 1
        sequence = sequence[: config.max_requests]
    else:  # pragma: no cover - pydantic validation prevents this
        raise ValueError(config.pattern.type)
    return sequence
