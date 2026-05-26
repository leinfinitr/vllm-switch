import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

from controller.config import ModelSpec
from controller.vllm_client import VLLMClient


@pytest.mark.asyncio
async def test_sleep_calls_vllm_sleep_endpoint_with_level():
    seen = {}
    app = FastAPI()

    @app.post("/sleep")
    async def sleep(request: Request):
        seen["level"] = request.query_params["level"]
        return {"ok": True}

    transport = ASGITransport(app=app)
    client = VLLMClient(
        {"a": ModelSpec(backend_url="http://testserver", served_model_name="a")},
        timeout_s=5,
    )
    client._client._transport = transport  # test-only in-process transport

    latency = await client.sleep("a", level=1)
    await client.aclose()

    assert seen == {"level": "1"}
    assert latency >= 0


@pytest.mark.asyncio
async def test_wake_up_sends_repeated_tags_query_params():
    seen = {}
    app = FastAPI()

    @app.post("/wake_up")
    async def wake_up(request: Request):
        seen["tags"] = request.query_params.getlist("tags")
        return {"ok": True}

    transport = ASGITransport(app=app)
    client = VLLMClient(
        {"a": ModelSpec(backend_url="http://testserver", served_model_name="a")},
        timeout_s=5,
    )
    client._client._transport = transport

    await client.wake_up("a", tags=["weights", "kv_cache"])
    await client.aclose()

    assert seen == {"tags": ["weights", "kv_cache"]}
