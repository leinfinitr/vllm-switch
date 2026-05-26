import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from controller.config import ControllerConfig
from controller.main import create_app
from controller.vllm_client import VLLMClient


def make_backend(label: str, events: list[str]) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/sleep")
    async def sleep():
        events.append(f"sleep:{label}")
        return {"ok": True}

    @app.post("/wake_up")
    async def wake_up():
        events.append(f"wake:{label}")
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat(body: dict):
        events.append(f"chat:{label}")
        return {"model": body["model"], "choices": [{"message": {"content": label}}]}

    return app


@pytest.mark.asyncio
async def test_controller_switches_by_model_and_proxies_json(tmp_path):
    events: list[str] = []
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
                "b": {"backend_url": "http://b", "served_model_name": "b"},
            },
            "controller": {"startup_awake_model": "a", "metrics_path": str(tmp_path / "events.jsonl")},
        }
    )
    controller_app = create_app(config)

    def handler(request):
        if request.url.host == "a":
            return ASGITransport(make_backend("a", events)).handle_async_request(request)
        return ASGITransport(make_backend("b", events)).handle_async_request(request)

    # monkeypatch by replacing the httpx client transport with a simple router below
    class RouterTransport(ASGITransport):
        async def handle_async_request(self, request):
            if request.url.host == "a":
                return await ASGITransport(make_backend("a", events)).handle_async_request(request)
            return await ASGITransport(make_backend("b", events)).handle_async_request(request)

    controller_app.state.vllm_client._client._transport = RouterTransport(make_backend("unused", events))

    async with AsyncClient(transport=ASGITransport(controller_app), base_url="http://controller") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "b"
    assert events == ["sleep:a", "wake:b", "chat:b"]
