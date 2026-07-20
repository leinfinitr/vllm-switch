import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from httpx import ASGITransport

from scripts.smoke_openai_switch import run_smoke, write_jsonl


def test_write_jsonl_preserves_smoke_order(tmp_path):
    output = tmp_path / "smoke.jsonl"
    write_jsonl(output, [{"model": "a"}, {"model": "b"}, {"model": "b"}, {"model": "a"}])

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["model"] for record in records] == ["a", "b", "b", "a"]


@pytest.mark.asyncio
async def test_smoke_uses_only_openai_requests_and_state_read(monkeypatch):
    app = FastAPI()
    paths: list[tuple[str, str]] = []

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        paths.append((request.method, request.url.path))
        return StreamingResponse(
            iter(
                [
                    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                    b"data: [DONE]\n\n",
                ]
            ),
            media_type="text/event-stream",
        )

    @app.get("/admin/state")
    async def state(request: Request):
        paths.append((request.method, request.url.path))
        return {"active_requests": {}}

    original_client = httpx.AsyncClient

    def in_process_client(*args, **kwargs):
        kwargs["transport"] = ASGITransport(app)
        kwargs["base_url"] = "http://controller"
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", in_process_client)
    records = await run_smoke("http://controller", ["a", "b"])

    assert len(records) == 6
    assert paths.count(("POST", "/v1/chat/completions")) == 6
    assert paths[-1] == ("GET", "/admin/state")
    assert all(path != "/admin/switch" for _, path in paths)