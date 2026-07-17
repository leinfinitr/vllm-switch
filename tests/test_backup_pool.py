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
    assert stats["over_cap_bytes"] == 2048

    queued = state.maybe_enqueue_release_requests()
    assert queued == {"client-a": 2048}
    assert state.release_request_snapshot("client-a") == (2048, 2048)

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
    assert state.stats()["pending_release_bytes"] == 1024
    assert state.maybe_enqueue_release_requests() == {}


def test_hard_cap_reclaims_all_evictable_bytes_when_required_exceeds_cap():
    state = BackupPoolState(global_cap_bytes=4096)
    state.report_usage(
        client_id="client-a",
        pid=1,
        engine="vllm",
        model_id="a",
        total_bytes=10_240,
        required_for_restore_bytes=5120,
        cache_only_bytes=5120,
        invalid_bytes=0,
        free_local_bytes=0,
    )

    # The cap applies to total backup usage. Required storage is protected, so
    # the best feasible action is to release every evictable byte.
    assert state.maybe_enqueue_release_requests() == {"client-a": 5120}
    assert state.stats()["over_cap_bytes"] == 1024


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
    assert state.release_request_snapshot("client-a") == (1024, 1024)
    assert state.release_request_snapshot("client-b") == (0, 0)


def test_pending_release_survives_non_evictable_state_transition():
    state = BackupPoolState()
    usage = {
        "client_id": "client-a",
        "total_bytes": 1024,
        "invalid_bytes": 0,
        "free_local_bytes": 0,
    }
    state.report_usage(
        **usage,
        required_for_restore_bytes=0,
        cache_only_bytes=1024,
    )
    assert state.request_release("client-a", 1024) == 1024
    assert state.release_request_snapshot("client-a") == (1024, 1024)
    # GET is idempotent; losing a response does not consume the command.
    assert state.release_request_snapshot("client-a") == (1024, 1024)

    # Wake makes the same storage temporarily non-evictable. This is not a
    # physical release acknowledgement and must not cancel/reissue the request.
    state.report_usage(
        **usage,
        required_for_restore_bytes=1024,
        cache_only_bytes=0,
    )
    assert state.stats()["pending_release_bytes"] == 1024
    state.report_usage(
        **usage,
        required_for_restore_bytes=0,
        cache_only_bytes=1024,
    )
    assert state.request_release_bytes(1024) == {}

    # This legacy-client path has no cumulative counter, so an observed
    # total_bytes drop is its compatibility acknowledgement.
    state.report_usage(
        client_id="client-a",
        total_bytes=0,
        required_for_restore_bytes=0,
        cache_only_bytes=0,
        invalid_bytes=0,
        free_local_bytes=0,
    )
    assert state.stats()["pending_release_bytes"] == 0


def test_release_counter_ack_survives_latest_wins_reallocation():
    state = BackupPoolState()
    usage = {
        "client_id": "client-a",
        "total_bytes": 1024,
        "required_for_restore_bytes": 0,
        "cache_only_bytes": 1024,
        "invalid_bytes": 0,
        "free_local_bytes": 0,
    }
    state.report_usage(**usage, released_bytes_total=0)
    assert state.request_release("client-a", 1024) == 1024

    # The worker released 1 KiB and immediately reused newly allocated 1 KiB.
    # A latest-wins footprint is unchanged, but the monotonic counter preserves
    # the real allocator release acknowledgement.
    state.report_usage(**usage, released_bytes_total=1024)
    assert state.stats()["pending_release_bytes"] == 0

    with pytest.raises(ValueError, match="must be monotonic"):
        state.report_usage(**usage, released_bytes_total=512)

    with pytest.raises(ValueError, match="presence must remain stable"):
        state.report_usage(**usage)


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
                "released_bytes_total": 0,
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

        release_request = (await client.get("/admin/cpu-backup/release-requests/client-a")).json()
        assert release_request["ok"] is True
        assert release_request["requested_release_bytes_total"] == 4096
        assert release_request["pending_release_bytes"] == 4096

        response = await client.post(
            "/admin/cpu-backup/usage",
            json={
                "client_id": "client-a",
                "pid": 123,
                "engine": "vllm",
                "model_id": "model-a",
                "total_bytes": 1024,
                "released_bytes_total": 4096,
                "required_for_restore_bytes": 1024,
                "cache_only_bytes": 0,
                "invalid_bytes": 0,
                "free_local_bytes": 0,
            },
        )
        assert response.status_code == 200
        stats = (await client.get("/admin/cpu-backup/stats")).json()
        assert stats["stats"]["evictable_bytes"] == 0

        stale = await client.post(
            "/admin/cpu-backup/usage",
            json={
                "client_id": "client-a",
                "total_bytes": 1024,
                "released_bytes_total": 2048,
                "required_for_restore_bytes": 1024,
                "cache_only_bytes": 0,
                "invalid_bytes": 0,
                "free_local_bytes": 0,
            },
        )
        assert stale.status_code == 409
