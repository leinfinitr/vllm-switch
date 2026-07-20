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


@pytest.mark.asyncio
async def test_prepare_pool_cleans_up_started_processes_on_failure(tmp_path, monkeypatch):
    events = []

    class FakeProcess:
        next_pid = 100

        def __init__(self, *_args, **_kwargs):
            self.pid = FakeProcess.next_pid
            FakeProcess.next_pid += 1

        def wait(self, timeout=None):
            events.append(f"wait:{self.pid}")

    config = SimpleNamespace(
        models={
            "a": SimpleNamespace(
                backend_url="http://a",
                sleep_level=1,
                launch_command=["server-a"],
                env={},
                cwd=None,
            ),
            "b": SimpleNamespace(
                backend_url="http://b",
                sleep_level=1,
                launch_command=["server-b"],
                env={},
                cwd=None,
            ),
        },
        controller=SimpleNamespace(startup_awake_model="a", switch_timeout_s=5),
    )

    async def fake_health(url, timeout_s):
        if url == "http://b":
            raise TimeoutError("boom")

    async def fake_transition(*_args, **_kwargs):
        return None

    monkeypatch.setattr(launch_vllm_pool.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(
        launch_vllm_pool.os,
        "killpg",
        lambda pid, sig: events.append(f"killpg:{pid}:{sig.name}"),
    )
    monkeypatch.setattr(launch_vllm_pool, "wait_health", fake_health)
    monkeypatch.setattr(launch_vllm_pool, "post_and_wait", fake_transition)
    pid_file = tmp_path / "pids.json"
    with pytest.raises(TimeoutError, match="boom"):
        await launch_vllm_pool.prepare_pool(
            config, pid_file=pid_file, skip_launch=False
        )

    assert events == [
        "killpg:101:SIGTERM",
        "killpg:100:SIGTERM",
        "wait:101",
        "wait:100",
    ]
    assert not pid_file.exists()