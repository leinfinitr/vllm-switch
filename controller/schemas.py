from typing import Any

from pydantic import BaseModel, Field


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
