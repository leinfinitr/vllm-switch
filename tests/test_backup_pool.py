import pytest
from httpx import ASGITransport, AsyncClient

from controller.backup_pool import BackupPoolState, BackupState
from controller.config import ControllerConfig
from controller.main import create_app


def test_backup_pool_state_tracks_required_and_evictable_bytes():
    state = BackupPoolState(global_cap_bytes=2048)
    state.register_client("client-a", pid=123, engine="vllm", model_id="model-a")
    state.record_allocated(
        client_id="client-a",
        backup_id="b0",
        size_bytes=1024,
        tag="weights",
        model_id="model-a",
        engine="vllm",
    )
    state.record_allocated(
        client_id="client-a",
        backup_id="b1",
        size_bytes=2048,
        tag="weights",
        model_id="model-a",
        engine="vllm",
    )

    state.update_state("b0", state=BackupState.REQUIRED_FOR_RESTORE, valid=True)
    state.update_state("b1", state=BackupState.CACHE_ONLY, valid=True)

    stats = state.stats()
    assert stats["client_count"] == 1
    assert stats["backup_count"] == 2
    assert stats["total_bytes"] == 3072
    assert stats["required_for_restore_bytes"] == 1024
    assert stats["evictable_bytes"] == 2048
    assert stats["over_cap_bytes"] == 1024

    queued = state.maybe_enqueue_evictions()
    assert queued == ["b1"]
    assert state.poll_evictions("client-a") == ["b1"]

    invalidated = state.invalidate(client_id="client-a", tag="weights", generation=1)
    assert len(invalidated) == 2
    assert all(not record.valid for record in invalidated)
    assert all(record.state == BackupState.INVALID for record in invalidated)


def test_backup_pool_eviction_prefers_lower_model_priority_before_lru():
    state = BackupPoolState(
        global_cap_bytes=4096,
        model_priorities={"cold-model": 0, "hot-model": 10},
    )
    state.register_client("client-a", pid=123, engine="vllm", model_id="cold-model")
    state.register_client("client-b", pid=124, engine="vllm", model_id="hot-model")
    cold = state.record_allocated(
        client_id="client-a",
        backup_id="cold-backup",
        size_bytes=1024,
        tag="weights",
        model_id="cold-model",
        engine="vllm",
    )
    hot = state.record_allocated(
        client_id="client-b",
        backup_id="hot-backup",
        size_bytes=4096,
        tag="weights",
        model_id="hot-model",
        engine="vllm",
    )
    state.update_state("cold-backup", state=BackupState.CACHE_ONLY, valid=True)
    state.update_state("hot-backup", state=BackupState.CACHE_ONLY, valid=True)

    # Make the high-priority model older. Priority should still win over LRU.
    hot.updated_at = 1.0
    cold.updated_at = 2.0

    queued = state.maybe_enqueue_evictions()

    assert queued == ["cold-backup"]
    assert state.poll_evictions("client-a") == ["cold-backup"]
    assert state.poll_evictions("client-b") == []


@pytest.mark.asyncio
async def test_cpu_backup_admin_api_records_metadata(tmp_path):
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {"backend_url": "http://a", "served_model_name": "a"},
            },
            "controller": {
                "startup_awake_model": "a",
                "metrics_path": str(tmp_path / "events.jsonl"),
                "cpu_backup_global_cap_bytes": 4096,
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
            "/admin/cpu-backup/allocated",
            json={
                "client_id": "client-a",
                "backup_id": "b0",
                "size_bytes": 4096,
                "tag": "weights",
                "model_id": "model-a",
                "engine": "vllm",
            },
        )
        assert response.status_code == 200

        response = await client.post(
            "/admin/cpu-backup/state",
            json={
                "backup_id": "b0",
                "state": "required_for_restore",
                "valid": True,
            },
        )
        assert response.status_code == 200

        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["ok"] is True
        assert stats["stats"]["model_priorities"] == {"model-a": 5}
        assert stats["stats"]["default_model_priority"] == 1
        assert stats["stats"]["required_for_restore_bytes"] == 4096
        assert stats["backups"]["b0"]["state"] == "required_for_restore"

        response = await client.post(
            "/admin/cpu-backup/invalidate",
            json={"client_id": "client-a", "tag": "weights", "generation": 1},
        )
        assert response.status_code == 200
        assert response.json()["invalidated_count"] == 1

        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["backups"]["b0"]["state"] == "invalid"
        assert stats["backups"]["b0"]["valid"] is False

        response = await client.post(
            "/admin/cpu-backup/events",
            json=[
                {
                    "type": "allocated",
                    "client_id": "client-a",
                    "backup_id": "b1",
                    "size_bytes": 1024,
                    "tag": "weights",
                    "model_id": "model-a",
                    "engine": "vllm",
                },
                {
                    "type": "state",
                    "backup_id": "b1",
                    "state": "cache_only",
                    "valid": True,
                },
            ],
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 2
        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["backups"]["b1"]["state"] == "cache_only"
        evictions = (await client.get("/admin/cpu-backup/evictions/client-a")).json()
        assert evictions["ok"] is True
        assert evictions["backup_ids"] == ["b1"]

        response = await client.post(
            "/admin/cpu-backup/released",
            json={"backup_id": "b1"},
        )
        assert response.status_code == 200
        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert "b1" not in stats["backups"]
        assert stats["stats"]["total_bytes"] == 4096
