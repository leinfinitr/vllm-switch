import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from controller.config import ControllerConfig
from controller.main import create_app


@pytest.mark.asyncio
async def test_switch_timeout_is_one_deadline_across_sleep_and_wake(tmp_path):
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
                "b": {"backend_url": "http://b", "served_model_name": "b"},
            },
            "controller": {
                "startup_awake_model": "a",
                "switch_timeout_s": 0.05,
                "metrics_path": str(tmp_path / "events.jsonl"),
            },
        }
    )
    app = create_app(config)
    app.state.controller_state.startup_reconciled = True

    async def slow_sleep(_model, _level, _timeout_s):
        await asyncio.sleep(0.04)
        return 0.04, 0.0

    async def slow_wake(_model, _tags, _timeout_s):
        await asyncio.sleep(0.04)
        return 0.04, 0.0

    async def proxy(*_args, **_kwargs):
        return 200, {"content-type": "application/json"}, b"{}"

    app.state.vllm_client.sleep_and_wait_with_timeout = slow_sleep
    app.state.vllm_client.wake_up_and_wait_with_timeout = slow_wake
    app.state.vllm_client.proxy_json = proxy

    started = time.perf_counter()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://controller") as client:
        response = await client.post("/v1/chat/completions", json={"model": "b", "messages": []})
    elapsed = time.perf_counter() - started

    assert response.status_code == 502
    assert elapsed < 0.075


@pytest.mark.asyncio
async def test_startup_reconciliation_checks_full_pool_and_rejects_multiple_awake(tmp_path):
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
                "b": {"backend_url": "http://b", "served_model_name": "b"},
            },
            "controller": {
                "startup_awake_model": "a",
                "switch_timeout_s": 0.1,
                "metrics_path": str(tmp_path / "events.jsonl"),
            },
        }
    )
    app = create_app(config)
    observed = []

    async def all_awake(model, *, timeout_s=None):
        observed.append((model, timeout_s))
        return False

    app.state.vllm_client.is_sleeping = all_awake
    async with AsyncClient(transport=ASGITransport(app), base_url="http://controller") as client:
        response = await client.post("/v1/chat/completions", json={"model": "a", "messages": []})

    assert response.status_code == 502
    assert [model for model, _ in observed] == ["a", "b"]
    assert all(timeout is not None and 0 < timeout <= 0.1 for _, timeout in observed)


@pytest.mark.asyncio
async def test_startup_probe_is_inside_end_to_end_switch_deadline(tmp_path):
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
                "b": {"backend_url": "http://b", "served_model_name": "b"},
            },
            "controller": {
                "startup_awake_model": "a",
                "switch_timeout_s": 0.05,
                "metrics_path": str(tmp_path / "events.jsonl"),
            },
        }
    )
    app = create_app(config)

    async def slow_probe(model, *, timeout_s=None):
        await asyncio.sleep(0.02)
        return model == "b"

    async def slow_sleep(_model, _level, _timeout_s):
        await asyncio.sleep(0.02)
        return 0.02, 0.0

    async def slow_wake(_model, _tags, _timeout_s):
        await asyncio.sleep(0.02)
        return 0.02, 0.0

    app.state.vllm_client.is_sleeping = slow_probe
    app.state.vllm_client.sleep_and_wait_with_timeout = slow_sleep
    app.state.vllm_client.wake_up_and_wait_with_timeout = slow_wake

    started = time.perf_counter()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://controller") as client:
        response = await client.post("/v1/chat/completions", json={"model": "b", "messages": []})
    elapsed = time.perf_counter() - started

    assert response.status_code == 502
    assert elapsed < 0.075
