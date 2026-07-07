from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BackupState(StrEnum):
    ALLOCATED = "allocated"
    REQUIRED_FOR_RESTORE = "required_for_restore"
    CACHE_ONLY = "cache_only"
    INVALID = "invalid"
    FREE_LOCAL = "free_local"
    RELEASED = "released"


@dataclass
class BackupRecord:
    client_id: str
    backup_id: str
    size_bytes: int
    tag: str = "weights"
    model_id: str | None = None
    engine: str | None = None
    pinned: bool = True
    generation: int = 0
    valid: bool = False
    state: BackupState = BackupState.ALLOCATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientRecord:
    client_id: str
    pid: int | None = None
    engine: str = "unknown"
    model_id: str | None = None
    gpu_uuid: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class BackupPoolState:
    """Metadata-only coordinator for process-local CPU backup pools.

    The controller never owns or touches the pinned memory. It records which
    client process owns which local backup buffers, whether they are required
    for restore, and which bytes are safe to evict in later phases.
    """

    def __init__(self, global_cap_bytes: int | None = None) -> None:
        self.clients: dict[str, ClientRecord] = {}
        self.backups: dict[str, BackupRecord] = {}
        self.eviction_requests: dict[str, list[str]] = {}
        self.queued_evictions: set[str] = set()
        self.global_cap_bytes = global_cap_bytes

    def register_client(
        self,
        client_id: str,
        *,
        pid: int | None = None,
        engine: str = "unknown",
        model_id: str | None = None,
        gpu_uuid: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ClientRecord:
        now = time.time()
        existing = self.clients.get(client_id)
        if existing is None:
            record = ClientRecord(
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
        existing.pid = pid
        existing.engine = engine
        existing.model_id = model_id
        existing.gpu_uuid = gpu_uuid
        existing.metadata = metadata or {}
        existing.updated_at = now
        return existing

    def record_allocated(
        self,
        *,
        client_id: str,
        backup_id: str,
        size_bytes: int,
        tag: str = "weights",
        model_id: str | None = None,
        engine: str | None = None,
        pinned: bool = True,
        generation: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> BackupRecord:
        if client_id not in self.clients:
            self.register_client(client_id, engine=engine or "unknown", model_id=model_id)
        now = time.time()
        existing = self.backups.get(backup_id)
        if existing is None:
            record = BackupRecord(
                client_id=client_id,
                backup_id=backup_id,
                size_bytes=size_bytes,
                tag=tag,
                model_id=model_id,
                engine=engine,
                pinned=pinned,
                generation=generation,
                state=BackupState.ALLOCATED,
                metadata=metadata or {},
                created_at=now,
                updated_at=now,
            )
            self.backups[backup_id] = record
            return record
        existing.client_id = client_id
        existing.size_bytes = size_bytes
        existing.tag = tag
        existing.model_id = model_id
        existing.engine = engine
        existing.pinned = pinned
        existing.generation = generation
        existing.state = BackupState.ALLOCATED
        existing.metadata = metadata or {}
        existing.updated_at = now
        return existing

    def update_state(
        self,
        backup_id: str,
        *,
        state: BackupState,
        valid: bool | None = None,
        generation: int | None = None,
    ) -> BackupRecord:
        record = self.backups[backup_id]
        record.state = state
        if valid is not None:
            record.valid = valid
        if generation is not None:
            record.generation = generation
        record.updated_at = time.time()
        return record

    def invalidate(
        self,
        *,
        client_id: str | None = None,
        model_id: str | None = None,
        tag: str | None = None,
        generation: int | None = None,
    ) -> list[BackupRecord]:
        changed = []
        for record in self.backups.values():
            if client_id is not None and record.client_id != client_id:
                continue
            if model_id is not None and record.model_id != model_id:
                continue
            if tag is not None and record.tag != tag:
                continue
            record.valid = False
            record.state = BackupState.INVALID
            if generation is not None:
                record.generation = generation
            record.updated_at = time.time()
            changed.append(record)
        return changed

    def mark_released(self, backup_id: str) -> BackupRecord | None:
        record = self.backups.pop(backup_id, None)
        if record is None:
            self.queued_evictions.discard(backup_id)
            return None
        record.state = BackupState.RELEASED
        record.valid = False
        record.updated_at = time.time()
        self.queued_evictions.discard(backup_id)
        return record

    def poll_evictions(self, client_id: str) -> list[str]:
        backup_ids = self.eviction_requests.pop(client_id, [])
        self.queued_evictions.difference_update(backup_ids)
        return backup_ids

    def request_eviction(self, client_id: str, backup_ids: list[str]) -> None:
        new_ids = [backup_id for backup_id in backup_ids if backup_id not in self.queued_evictions]
        if not new_ids:
            return
        self.eviction_requests.setdefault(client_id, []).extend(new_ids)
        self.queued_evictions.update(new_ids)

    def maybe_enqueue_evictions(self) -> list[str]:
        if self.global_cap_bytes is None:
            return []
        total_bytes = sum(record.size_bytes for record in self.backups.values())
        bytes_to_free = total_bytes - self.global_cap_bytes
        if bytes_to_free <= 0:
            return []

        candidates = sorted(
            (
                record
                for record in self.backups.values()
                if record.state in {BackupState.CACHE_ONLY, BackupState.FREE_LOCAL}
                and record.backup_id not in self.queued_evictions
            ),
            key=lambda record: (record.updated_at, -record.size_bytes),
        )
        queued = []
        freed = 0
        for record in candidates:
            self.request_eviction(record.client_id, [record.backup_id])
            queued.append(record.backup_id)
            freed += record.size_bytes
            if freed >= bytes_to_free:
                break
        return queued

    def stats(self) -> dict[str, Any]:
        by_state: dict[str, dict[str, int]] = {}
        by_client: dict[str, dict[str, int]] = {}
        for record in self.backups.values():
            state_bucket = by_state.setdefault(record.state.value, {"count": 0, "bytes": 0})
            state_bucket["count"] += 1
            state_bucket["bytes"] += record.size_bytes

            client_bucket = by_client.setdefault(record.client_id, {"count": 0, "bytes": 0})
            client_bucket["count"] += 1
            client_bucket["bytes"] += record.size_bytes

        evictable_bytes = sum(
            record.size_bytes
            for record in self.backups.values()
            if record.state in {BackupState.CACHE_ONLY, BackupState.INVALID, BackupState.FREE_LOCAL}
        )
        required_bytes = sum(
            record.size_bytes
            for record in self.backups.values()
            if record.state == BackupState.REQUIRED_FOR_RESTORE
        )
        total_bytes = sum(record.size_bytes for record in self.backups.values())
        return {
            "client_count": len(self.clients),
            "backup_count": len(self.backups),
            "total_bytes": total_bytes,
            "global_cap_bytes": self.global_cap_bytes,
            "over_cap_bytes": (
                max(total_bytes - self.global_cap_bytes, 0)
                if self.global_cap_bytes is not None
                else 0
            ),
            "required_for_restore_bytes": required_bytes,
            "evictable_bytes": evictable_bytes,
            "by_state": by_state,
            "by_client": by_client,
            "pending_eviction_count": sum(len(v) for v in self.eviction_requests.values()),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "stats": self.stats(),
            "clients": {client_id: record.__dict__ for client_id, record in self.clients.items()},
            "backups": {
                backup_id: {
                    **record.__dict__,
                    "state": record.state.value,
                }
                for backup_id, record in self.backups.items()
            },
        }
