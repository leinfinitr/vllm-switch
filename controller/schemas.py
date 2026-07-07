from typing import Any

from pydantic import BaseModel, Field

from controller.backup_pool import BackupState


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "vllm-model-switch-controller"


class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]


class ErrorResponse(BaseModel):
    error: dict[str, Any] = Field(default_factory=dict)


class BackupRegisterRequest(BaseModel):
    client_id: str
    pid: int | None = None
    engine: str = "unknown"
    model_id: str | None = None
    gpu_uuid: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackupAllocatedRequest(BaseModel):
    client_id: str
    backup_id: str
    size_bytes: int = Field(ge=0)
    tag: str = "weights"
    model_id: str | None = None
    engine: str | None = None
    pinned: bool = True
    generation: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackupStateUpdateRequest(BaseModel):
    backup_id: str
    state: BackupState
    valid: bool | None = None
    generation: int | None = None


class BackupInvalidateRequest(BaseModel):
    client_id: str | None = None
    model_id: str | None = None
    tag: str | None = None
    generation: int | None = None
    reason: str | None = None


class BackupReleasedRequest(BaseModel):
    backup_id: str


class BackupEvictRequest(BaseModel):
    client_id: str
    backup_ids: list[str]
