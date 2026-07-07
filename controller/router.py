import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from controller.backup_pool import BackupPoolState, BackupState
from controller.config import ControllerConfig
from controller.metrics import RequestMetrics
from controller.policies import SwitchingPolicy
from controller.schemas import (
    BackupAllocatedRequest,
    BackupEvictRequest,
    BackupInvalidateRequest,
    BackupRegisterRequest,
    BackupReleasedRequest,
    BackupStateUpdateRequest,
    OpenAIModel,
    OpenAIModelsResponse,
)
from controller.state import ControllerState, UnknownModelError
from controller.vllm_client import VLLMClient, VLLMClientError

HOP_BY_HOP_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
}


def make_router(
    config: ControllerConfig,
    state: ControllerState,
    policy: SwitchingPolicy,
    vllm_client: VLLMClient,
    metrics_recorder,
    backup_pool: BackupPoolState | None = None,
) -> APIRouter:
    router = APIRouter()
    backup_pool = backup_pool or BackupPoolState()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "active_model": state.active_model, "states": state.model_states}

    @router.get("/admin/state")
    async def admin_state() -> dict[str, Any]:
        return {"active_model": state.active_model, "states": state.model_states}

    @router.post("/admin/cpu-backup/register")
    async def cpu_backup_register(body: BackupRegisterRequest) -> dict[str, Any]:
        record = backup_pool.register_client(
            body.client_id,
            pid=body.pid,
            engine=body.engine,
            model_id=body.model_id,
            gpu_uuid=body.gpu_uuid,
            metadata=body.metadata,
        )
        return {"ok": True, "client": record.__dict__}

    @router.post("/admin/cpu-backup/allocated")
    async def cpu_backup_allocated(body: BackupAllocatedRequest) -> dict[str, Any]:
        record = backup_pool.record_allocated(
            client_id=body.client_id,
            backup_id=body.backup_id,
            size_bytes=body.size_bytes,
            tag=body.tag,
            model_id=body.model_id,
            engine=body.engine,
            pinned=body.pinned,
            generation=body.generation,
            metadata=body.metadata,
        )
        backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "backup": {**record.__dict__, "state": record.state.value}}

    @router.post("/admin/cpu-backup/state")
    async def cpu_backup_state(body: BackupStateUpdateRequest) -> dict[str, Any]:
        try:
            record = backup_pool.update_state(
                body.backup_id,
                state=body.state,
                valid=body.valid,
                generation=body.generation,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"unknown backup: {body.backup_id}",
            ) from exc
        backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "backup": {**record.__dict__, "state": record.state.value}}

    @router.post("/admin/cpu-backup/invalidate")
    async def cpu_backup_invalidate(body: BackupInvalidateRequest) -> dict[str, Any]:
        changed = backup_pool.invalidate(
            client_id=body.client_id,
            model_id=body.model_id,
            tag=body.tag,
            generation=body.generation,
        )
        backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "invalidated_count": len(changed), "reason": body.reason}

    @router.post("/admin/cpu-backup/released")
    async def cpu_backup_released(body: BackupReleasedRequest) -> dict[str, Any]:
        record = backup_pool.mark_released(body.backup_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown backup: {body.backup_id}",
            )
        backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "backup": {**record.__dict__, "state": record.state.value}}

    @router.post("/admin/cpu-backup/evict")
    async def cpu_backup_evict(body: BackupEvictRequest) -> dict[str, Any]:
        backup_pool.request_eviction(body.client_id, body.backup_ids)
        return {"ok": True, "queued": len(body.backup_ids)}

    @router.get("/admin/cpu-backup/evictions/{client_id}")
    async def cpu_backup_evictions(client_id: str) -> dict[str, Any]:
        backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "backup_ids": backup_pool.poll_evictions(client_id)}

    @router.get("/admin/cpu-backup/stats")
    async def cpu_backup_stats() -> dict[str, Any]:
        return {"ok": True, **backup_pool.snapshot()}

    @router.post("/admin/cpu-backup/events")
    async def cpu_backup_events(events: list[dict[str, Any]]) -> dict[str, Any]:
        processed = 0
        for event in events:
            event_type = event.get("type")
            if event_type == "register":
                backup_pool.register_client(
                    event["client_id"],
                    pid=event.get("pid"),
                    engine=event.get("engine", "unknown"),
                    model_id=event.get("model_id"),
                    gpu_uuid=event.get("gpu_uuid"),
                    metadata=event.get("metadata") or {},
                )
            elif event_type == "allocated":
                backup_pool.record_allocated(
                    client_id=event["client_id"],
                    backup_id=event["backup_id"],
                    size_bytes=event["size_bytes"],
                    tag=event.get("tag", "weights"),
                    model_id=event.get("model_id"),
                    engine=event.get("engine"),
                    pinned=event.get("pinned", True),
                    generation=event.get("generation", 0),
                    metadata=event.get("metadata") or {},
                )
            elif event_type == "state":
                backup_pool.update_state(
                    event["backup_id"],
                    state=BackupState(event["state"]),
                    valid=event.get("valid"),
                    generation=event.get("generation"),
                )
            elif event_type == "invalidate":
                backup_pool.invalidate(
                    client_id=event.get("client_id"),
                    model_id=event.get("model_id"),
                    tag=event.get("tag"),
                    generation=event.get("generation"),
                )
            elif event_type == "released":
                backup_pool.mark_released(event["backup_id"])
            else:
                raise HTTPException(status_code=400, detail=f"unknown event type: {event_type}")
            processed += 1
        queued = backup_pool.maybe_enqueue_evictions()
        return {"ok": True, "processed": processed, "queued_evictions": len(queued)}

    @router.post("/admin/switch/{model}")
    async def admin_switch(model: str) -> dict[str, Any]:
        metrics = RequestMetrics.new(model=model, path=f"/admin/switch/{model}")
        await ensure_model_ready(model, metrics)
        metrics_recorder.record(metrics)
        return {"active_model": state.active_model, "states": state.model_states}

    @router.get("/v1/models")
    async def list_models() -> OpenAIModelsResponse:
        return OpenAIModelsResponse(data=[OpenAIModel(id=name) for name in config.models])

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        return await handle_openai_proxy(request, "/v1/chat/completions")

    @router.post("/v1/completions")
    async def completions(request: Request) -> Response:
        return await handle_openai_proxy(request, "/v1/completions")

    async def handle_openai_proxy(request: Request, path: str) -> Response:
        body = await request.json()
        target_model = body.get("model")
        if not isinstance(target_model, str):
            raise HTTPException(
                status_code=400,
                detail="request JSON must contain string field 'model'",
            )

        metrics = RequestMetrics.new(model=target_model, path=path)
        request_start = time.perf_counter()
        try:
            await ensure_model_ready(target_model, metrics)
            backend_start = time.perf_counter()
            if body.get("stream") is True:
                return await stream_response(
                    target_model,
                    path,
                    body,
                    request,
                    metrics,
                    request_start,
                    backend_start,
                )

            async with state.track_request(target_model):
                status, headers, content = await vllm_client.proxy_json(
                    target_model, path, body, headers=request.headers
                )
            metrics.status_code = status
            metrics.e2e_latency_ms = (time.perf_counter() - request_start) * 1000
            metrics_recorder.record(metrics)
            return Response(
                content=content,
                status_code=status,
                headers=filtered_headers(headers),
                media_type=headers.get("content-type", "application/json"),
            )
        except (UnknownModelError, KeyError) as exc:
            metrics.error = str(exc)
            metrics.status_code = 404
            metrics_recorder.record(metrics)
            raise HTTPException(status_code=404, detail=f"unknown model: {target_model}") from exc
        except VLLMClientError as exc:
            metrics.error = str(exc)
            metrics.status_code = 502
            metrics_recorder.record(metrics)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def ensure_model_ready(target_model: str, metrics: RequestMetrics) -> None:
        state.require_model(target_model)
        async with state.switch_lock:
            decision = policy.decide(state.active_model, target_model, state.model_states)
            metrics.previous_model = state.active_model
            metrics.switch_needed = bool(decision.sleep_models or decision.wake_model)
            if not metrics.switch_needed:
                return

            if decision.wait_for_active_requests:
                await state.wait_for_other_model_requests_to_finish(target_model)
            switch_start = time.perf_counter()
            sleep_total = 0.0
            wake_total = 0.0
            for model in decision.sleep_models:
                state.mark_sleeping_in_progress(model)
                latency = await vllm_client.sleep(model, config.models[model].sleep_level)
                sleep_total += latency
                state.mark_sleeping(model)
            if decision.wake_model is not None:
                state.mark_waking(decision.wake_model)
                spec = config.models[decision.wake_model]
                wake_total = await vllm_client.wake_up(decision.wake_model, spec.wake_tags)
                state.mark_awake(decision.wake_model)
            elif decision.mark_active:
                state.mark_awake(decision.route_model)
            metrics.sleep_latency_ms = sleep_total * 1000 if sleep_total else None
            metrics.wake_latency_ms = wake_total * 1000 if wake_total else None
            metrics.switch_latency_ms = (time.perf_counter() - switch_start) * 1000

    async def stream_response(
        target_model: str,
        path: str,
        body: dict[str, Any],
        request: Request,
        metrics: RequestMetrics,
        request_start: float,
        backend_start: float,
    ) -> StreamingResponse:
        first_chunk_seen = False
        first_chunk_ts: float | None = None
        status_code = 200
        response_headers: dict[str, str] = {}

        async def iterator():
            nonlocal first_chunk_seen, first_chunk_ts, status_code, response_headers
            try:
                async with state.track_request(target_model):
                    async with vllm_client.proxy_stream(
                        target_model, path, body, headers=request.headers
                    ) as upstream:
                        status_code = upstream.status_code
                        response_headers = filtered_headers(dict(upstream.headers))
                        async for chunk in upstream.aiter_bytes():
                            if chunk and not first_chunk_seen:
                                first_chunk_seen = True
                                first_chunk_ts = time.perf_counter()
                                metrics.backend_ttft_ms = (first_chunk_ts - backend_start) * 1000
                                metrics.e2e_ttft_ms = (first_chunk_ts - request_start) * 1000
                            yield chunk
            finally:
                metrics.status_code = status_code
                metrics.e2e_latency_ms = (time.perf_counter() - request_start) * 1000
                metrics_recorder.record(metrics)

        return StreamingResponse(
            iterator(),
            status_code=status_code,
            headers=response_headers,
            media_type="text/event-stream",
        )

    return router


def filtered_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


def json_response(data: dict[str, Any]) -> Response:
    return Response(content=json.dumps(data), media_type="application/json")
