import json
from types import SimpleNamespace

import pytest

from scripts import launch_vllm_pool


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

    async def fake_post(url, path, params=None, timeout_s=600):
        events.append(f"post:{url[-1]}:{path}")

    async def fake_wait(url, expected, timeout_s, poll_interval_s=0.1):
        events.append(f"probe:{url[-1]}:{expected}")

    monkeypatch.setattr(launch_vllm_pool, "wait_health", fake_health)
    monkeypatch.setattr(launch_vllm_pool, "post", fake_post)
    monkeypatch.setattr(launch_vllm_pool, "wait_sleep_state", fake_wait)

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