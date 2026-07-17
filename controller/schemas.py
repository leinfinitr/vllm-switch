from typing import Any

from pydantic import BaseModel, Field, model_validator


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "vllm-model-switch-controller"


class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]


class BackupRegisterRequest(BaseModel):
    client_id: str
    pid: int | None = None
    engine: str = "unknown"
    model_id: str | None = None
    gpu_uuid: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackupUsageRequest(BaseModel):
    client_id: str
    pid: int | None = None
    engine: str = "unknown"
    model_id: str | None = None
    gpu_uuid: str | None = None
    total_bytes: int = Field(ge=0)
    released_bytes_total: int | None = Field(default=None, ge=0)
    required_for_restore_bytes: int = Field(ge=0)
    cache_only_bytes: int = Field(default=0, ge=0)
    invalid_bytes: int = Field(default=0, ge=0)
    free_local_bytes: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_accounting(self) -> "BackupUsageRequest":
        accounted = (
            self.required_for_restore_bytes
            + self.cache_only_bytes
            + self.invalid_bytes
            + self.free_local_bytes
        )
        if accounted != self.total_bytes:
            raise ValueError("accounted backup bytes must equal total_bytes")
        return self


class BackupReleaseRequest(BaseModel):
    client_id: str
    target_free_bytes: int = Field(ge=0)
