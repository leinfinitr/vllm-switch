import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts import launch_vllm_pool


@pytest.mark.asyncio
async def test_post_and_probe_share_one_transition_deadline(monkeypatch):
    async def fake_post(*_args, **_kwargs):
        await asyncio.sleep(0.04)

    async def fake_wait(*_args, **_kwargs):
        await asyncio.sleep(0.04)

    monkeypatch.setattr(launch_vllm_pool, "post", fake_post)
    monkeypatch.setattr(launch_vllm_pool, "wait_sleep_state", fake_wait)
    with pytest.raises(TimeoutError, match="lifecycle transition timed out"):
        await launch_vllm_pool.post_and_wait(
            "http://a",
            "/sleep",
            expected=True,
            timeout_s=0.05,
            params={"level": 1},
        )


@pytest.mark.asyncio
async def test_prepare_pool_sleeps_every_backend_before_waking_startup(tmp_path, monkeypatch):
    events: list[str] = []
    config = SimpleNamespace(
        models={
            "a": SimpleNamespace(
                backend_url="http://a",
                sleep_level=1,
                launch_command=None,
                env={},
                cwd=None,
            ),
            "b": SimpleNamespace(
                backend_url="http://b",
                sleep_level=1,
                launch_command=None,
                env={},
                cwd=None,
            ),
        },
        controller=SimpleNamespace(startup_awake_model="a", switch_timeout_s=5),
    )

    async def fake_health(url, timeout_s):
        events.append(f"health:{url[-1]}")

    async def fake_post_and_wait(
        url, path, *, expected, timeout_s=600, params=None
    ):
        events.append(f"post:{url[-1]}:{path}")
        events.append(f"probe:{url[-1]}:{expected}")

    monkeypatch.setattr(launch_vllm_pool, "wait_health", fake_health)
    monkeypatch.setattr(launch_vllm_pool, "post_and_wait", fake_post_and_wait)

    pid_file = tmp_path / "pids.json"
    await launch_vllm_pool.prepare_pool(
        config,
        pid_file=pid_file,
        skip_launch=True,
    )

    assert events == [
        "health:a",
        "post:a:/sleep",
        "probe:a:True",
        "health:b",
        "post:b:/sleep",
        "probe:b:True",
        "post:a:/wake_up",
        "probe:a:False",
    ]
    assert json.loads(pid_file.read_text()) == {}