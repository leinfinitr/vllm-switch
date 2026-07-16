from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClientBackupUsage:
    """Aggregated pinned CPU backup accounting for one vLLM process.

    The controller intentionally does not track per-tensor backup ids. vLLM owns
    the local tensors and decides which cache-only/invalid/free-local buffers to
    release when the controller asks for a target byte count.
    """

    client_id: str
    pid: int | None = None
    engine: str = "unknown"
    model_id: str | None = None
    gpu_uuid: str | None = None
    total_bytes: int = 0
    required_for_restore_bytes: int = 0
    cache_only_bytes: int = 0
    invalid_bytes: int = 0
    free_local_bytes: int = 0
    pending_release_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def evictable_bytes(self) -> int:
        return self.cache_only_bytes + self.invalid_bytes + self.free_local_bytes

    def snapshot(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "pid": self.pid,
            "engine": self.engine,
            "model_id": self.model_id,
            "gpu_uuid": self.gpu_uuid,
            "total_bytes": self.total_bytes,
            "required_for_restore_bytes": self.required_for_restore_bytes,
            "cache_only_bytes": self.cache_only_bytes,
            "invalid_bytes": self.invalid_bytes,
            "free_local_bytes": self.free_local_bytes,
            "evictable_bytes": self.evictable_bytes,
            "pending_release_bytes": self.pending_release_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class BackupPoolState:
    """Metadata-only coordinator for process-local CPU backup pools.

    The controller tracks only per-client aggregate bytes. It never decides which
    local tensor to release; it only asks a vLLM process to free a target number
    of evictable bytes according to global cap and model-priority policy.
    """

    def __init__(
        self,
        global_cap_bytes: int | None = None,
        *,
        model_priorities: dict[str, int] | None = None,
        default_model_priority: int = 0,
    ) -> None:
        self.clients: dict[str, ClientBackupUsage] = {}
        self.release_requests: dict[str, int] = {}
        self.global_cap_bytes = global_cap_bytes
        self.model_priorities = model_priorities or {}
        self.default_model_priority = default_model_priority

    def model_priority(self, model_id: str | None) -> int:
        if model_id is None:
            return self.default_model_priority
        return self.model_priorities.get(model_id, self.default_model_priority)

    def register_client(
        self,
        client_id: str,
        *,
        pid: int | None = None,
        engine: str = "unknown",
        model_id: str | None = None,
        gpu_uuid: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClientBackupUsage:
        now = time.time()
        record = self.clients.get(client_id)
        if record is None:
            record = ClientBackupUsage(
                client_id=client_id,
                pid=pid,
                engine=engine,
                model_id=model_id,
                gpu_uuid=gpu_uuid,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
            self.clients[client_id] = record
            return record
        record.pid = pid
        record.engine = engine
        record.model_id = model_id
        record.gpu_uuid = gpu_uuid
        record.metadata = metadata or {}
        record.updated_at = now
        return record

    def report_usage(
        self,
        *,
        client_id: str,
        total_bytes: int,
        required_for_restore_bytes: int,
        cache_only_bytes: int,
        invalid_bytes: int,
        free_local_bytes: int,
        pid: int | None = None,
        engine: str = "unknown",
        model_id: str | None = None,
        gpu_uuid: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClientBackupUsage:
        record = self.register_client(
            client_id,
            pid=pid,
            engine=engine,
            model_id=model_id,
            gpu_uuid=gpu_uuid,
            metadata=metadata,
        )
        # A decrease in total reserved bytes is the only unambiguous aggregate
        # acknowledgement that vLLM actually released pinned memory. State-only
        # transitions (for example cache_only -> required_for_restore) must not
        # be treated as release progress.
        released_bytes = max(record.total_bytes - total_bytes, 0)
        record.pending_release_bytes = max(
            record.pending_release_bytes - released_bytes, 0
        )
        record.total_bytes = total_bytes
        record.required_for_restore_bytes = required_for_restore_bytes
        record.cache_only_bytes = cache_only_bytes
        record.invalid_bytes = invalid_bytes
        record.free_local_bytes = free_local_bytes
        # Requests that exceed the currently evictable footprint are cancelled;
        # the policy will reissue bytes when those buffers become evictable again.
        record.pending_release_bytes = min(
            record.pending_release_bytes, record.evictable_bytes
        )
        record.updated_at = time.time()
        return record

    def request_release(self, client_id: str, target_free_bytes: int) -> int:
        if target_free_bytes <= 0:
            return 0
        record = self.clients.get(client_id)
        if record is None:
            return 0
        available = max(record.evictable_bytes - record.pending_release_bytes, 0)
        requested = min(target_free_bytes, available)
        if requested <= 0:
            return 0
        self.release_requests[client_id] = (
            self.release_requests.get(client_id, 0) + requested
        )
        record.pending_release_bytes += requested
        return requested

    def poll_release_request(self, client_id: str) -> int:
        return self.release_requests.pop(client_id, 0)

    def maybe_enqueue_release_requests(self) -> dict[str, int]:
        """Queue bytes-based release requests when evictable bytes exceed cap."""
        if self.global_cap_bytes is None:
            return {}
        bytes_to_free = self.stats()["over_cap_bytes"]
        if bytes_to_free <= 0:
            return {}

        queued: dict[str, int] = {}
        candidates = sorted(
            (
                record
                for record in self.clients.values()
                if record.evictable_bytes > record.pending_release_bytes
            ),
            key=lambda record: (
                self.model_priority(record.model_id),
                record.updated_at,
                -(record.evictable_bytes - record.pending_release_bytes),
            ),
        )
        for record in candidates:
            if bytes_to_free <= 0:
                break
            available = record.evictable_bytes - record.pending_release_bytes
            requested = self.request_release(record.client_id, min(bytes_to_free, available))
            if requested > 0:
                queued[record.client_id] = requested
                bytes_to_free -= requested
        return queued

    def stats(self) -> dict[str, Any]:
        total_bytes = sum(record.total_bytes for record in self.clients.values())
        required_bytes = sum(
            record.required_for_restore_bytes for record in self.clients.values()
        )
        cache_only_bytes = sum(record.cache_only_bytes for record in self.clients.values())
        invalid_bytes = sum(record.invalid_bytes for record in self.clients.values())
        free_local_bytes = sum(record.free_local_bytes for record in self.clients.values())
        evictable_bytes = cache_only_bytes + invalid_bytes + free_local_bytes
        pending_release_bytes = sum(
            record.pending_release_bytes for record in self.clients.values()
        )
        effective_evictable = max(evictable_bytes - pending_release_bytes, 0)
        return {
            "client_count": len(self.clients),
            "total_bytes": total_bytes,
            "global_cap_bytes": self.global_cap_bytes,
            "default_model_priority": self.default_model_priority,
            "model_priorities": self.model_priorities,
            "over_cap_bytes": (
                max(effective_evictable - self.global_cap_bytes, 0)
                if self.global_cap_bytes is not None
                else 0
            ),
            "required_for_restore_bytes": required_bytes,
            "cache_only_bytes": cache_only_bytes,
            "invalid_bytes": invalid_bytes,
            "free_local_bytes": free_local_bytes,
            "evictable_bytes": evictable_bytes,
            "pending_release_bytes": pending_release_bytes,
            "pending_release_request_count": len(self.release_requests),
            "by_client": {
                client_id: record.snapshot()
                for client_id, record in self.clients.items()
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "stats": self.stats(),
            "clients": {
                client_id: record.snapshot()
                for client_id, record in self.clients.items()
            },
            "release_requests": dict(self.release_requests),
        }
