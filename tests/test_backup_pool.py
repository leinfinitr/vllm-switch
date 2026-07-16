import pytest
from httpx import ASGITransport, AsyncClient

from controller.backup_pool import BackupPoolState
from controller.config import ControllerConfig
from controller.main import create_app


def test_backup_pool_state_tracks_aggregate_usage_and_release_bytes():
    state = BackupPoolState(global_cap_bytes=2048)
    state.report_usage(
        client_id="client-a",
        pid=123,
        engine="vllm",
        model_id="model-a",
        total_bytes=4096,
        required_for_restore_bytes=1024,
        cache_only_bytes=2048,
        invalid_bytes=512,
        free_local_bytes=512,
    )

    stats = state.stats()
    assert stats["client_count"] == 1
    assert stats["total_bytes"] == 4096
    assert stats["required_for_restore_bytes"] == 1024
    assert stats["evictable_bytes"] == 3072
    assert stats["over_cap_bytes"] == 1024

    queued = state.maybe_enqueue_release_requests()
    assert queued == {"client-a": 1024}
    assert state.poll_release_request("client-a") == 1024

    state.report_usage(
        client_id="client-a",
        pid=123,
        engine="vllm",
        model_id="model-a",
        total_bytes=3072,
        required_for_restore_bytes=512,
        cache_only_bytes=1536,
        invalid_bytes=512,
        free_local_bytes=512,
    )
    assert state.stats()["pending_release_bytes"] == 0
    assert state.maybe_enqueue_release_requests() == {"client-a": 512}


def test_backup_pool_release_prefers_lower_model_priority():
    state = BackupPoolState(
        global_cap_bytes=4096,
        model_priorities={"cold-model": 0, "hot-model": 10},
    )
    state.report_usage(
        client_id="client-a",
        pid=123,
        engine="vllm",
        model_id="cold-model",
        total_bytes=1024,
        required_for_restore_bytes=0,
        cache_only_bytes=1024,
        invalid_bytes=0,
        free_local_bytes=0,
    )
    state.report_usage(
        client_id="client-b",
        pid=124,
        engine="vllm",
        model_id="hot-model",
        total_bytes=4096,
        required_for_restore_bytes=0,
        cache_only_bytes=4096,
        invalid_bytes=0,
        free_local_bytes=0,
    )
    state.clients["client-b"].updated_at = 1.0
    state.clients["client-a"].updated_at = 2.0

    queued = state.maybe_enqueue_release_requests()

    assert queued == {"client-a": 1024}
    assert state.poll_release_request("client-a") == 1024
    assert state.poll_release_request("client-b") == 0


@pytest.mark.asyncio
async def test_cpu_backup_admin_api_records_aggregate_usage(tmp_path):
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
            },
            "controller": {
                "startup_awake_model": "a",
                "metrics_path": str(tmp_path / "events.jsonl"),
                "cpu_backup_global_cap_bytes": 0,
                "cpu_backup_default_model_priority": 1,
                "cpu_backup_model_priorities": {"model-a": 5},
            },
        }
    )
    app = create_app(config)

    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://controller",
    ) as client:
        response = await client.post(
            "/admin/cpu-backup/register",
            json={
                "client_id": "client-a",
                "pid": 123,
                "engine": "vllm",
                "model_id": "model-a",
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        response = await client.post(
            "/admin/cpu-backup/usage",
            json={
                "client_id": "client-a",
                "pid": 123,
                "engine": "vllm",
                "model_id": "model-a",
                "total_bytes": 5120,
                "required_for_restore_bytes": 1024,
                "cache_only_bytes": 4096,
                "invalid_bytes": 0,
                "free_local_bytes": 0,
            },
        )
        assert response.status_code == 200
        assert response.json()["queued_release_requests"] == {"client-a": 4096}

        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["ok"] is True
        assert stats["stats"]["model_priorities"] == {"model-a": 5}
        assert stats["stats"]["default_model_priority"] == 1
        assert stats["stats"]["required_for_restore_bytes"] == 1024
        assert stats["stats"]["evictable_bytes"] == 4096
        assert "backups" not in stats

        release_request = (
            await client.get("/admin/cpu-backup/release-requests/client-a")
        ).json()
        assert release_request["ok"] is True
        assert release_request["target_free_bytes"] == 4096

        response = await client.post(
            "/admin/cpu-backup/events",
            json=[
                {
                    "type": "usage",
                    "client_id": "client-a",
                    "pid": 123,
                    "engine": "vllm",
                    "model_id": "model-a",
                    "total_bytes": 1024,
                    "required_for_restore_bytes": 1024,
                    "cache_only_bytes": 0,
                    "invalid_bytes": 0,
                    "free_local_bytes": 0,
                }
            ],
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 1
        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["stats"]["evictable_bytes"] == 0
