# CPU Backup Metadata Coordinator

This document describes the controller-side daemon support added for vLLM pinned
CPU backup pool coordination.

Relevant commits on `research/pinned-backup-pool`:

- `17eb745 feat: add CPU backup metadata coordinator`
- `47a1824 feat: add daemon-driven CPU backup eviction`
- `462f05d feat: add CPU backup model priority policy`

## Architecture

The controller is a metadata-only coordinator for process-local vLLM CPU backup
pools.

```text
vLLM process
  allocates pinned CPU backup tensors locally
  performs D2H/H2D locally
  reports metadata to controller
  polls controller for eviction requests

controller daemon
  tracks clients and backup records
  accounts bytes globally
  enqueues eviction requests under memory pressure
  applies model-priority policy when choosing victims
```

The controller never owns vLLM's pinned memory and never performs CUDA copies.
This avoids cross-process pinned-memory sharing, CUDA context ownership, and
shared-memory registration problems.

## Backup states

The controller tracks one `BackupRecord` per vLLM backup id.

Important states:

- `allocated`: vLLM attached local backup memory to an allocation.
- `required_for_restore`: GPU weights are unmapped; the CPU backup is required to
  wake the model. The controller must not automatically evict this state.
- `cache_only`: GPU weights are present; the CPU backup only improves the next
  sleep and may be evicted.
- `invalid`: backup content is stale. The current automatic cap policy does not
  select this state because it may have come from conservative invalidation while
  a model is sleeping.
- `free_local`: local free-list memory that may be released under future policies.
- `released`: vLLM reported that the backup is no longer held; the controller
  removes it from active metadata/accounting.

The safety invariant is:

```text
required_for_restore backups are never automatically evicted.
```

## HTTP API

All endpoints are under `/admin/cpu-backup`.

### `POST /register`

Register or refresh a vLLM client.

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "qwen2p5_0p5b",
  "gpu_uuid": null,
  "metadata": {"hostname": "node-a"}
}
```

### `POST /allocated`

Record a local vLLM backup allocation.

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "backup_id": "123:0:...:weights",
  "size_bytes": 1048576000,
  "tag": "weights",
  "model_id": "qwen2p5_0p5b",
  "engine": "vllm",
  "pinned": true,
  "generation": 0,
  "metadata": {"pool_hit": false}
}
```

### `POST /state`

Update backup state.

```json
{
  "backup_id": "123:0:...:weights",
  "state": "cache_only",
  "valid": true,
  "generation": 0
}
```

### `POST /invalidate`

Mark matching backups invalid.

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "model_id": "qwen2p5_0p5b",
  "tag": "weights",
  "generation": 1,
  "reason": "allocator_invalidation"
}
```

### `POST /released`

Remove one backup from active metadata/accounting after vLLM releases it.

```json
{"backup_id": "123:0:...:weights"}
```

### `POST /evict`

Manually enqueue eviction requests for a client.

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "backup_ids": ["123:0:...:weights"],
  "reason": "manual"
}
```

### `GET /evictions/{client_id}`

Poll and clear pending eviction requests for one client. vLLM calls this at safe
points and releases only local cache-only backups it still owns.

Response:

```json
{
  "ok": true,
  "backup_ids": ["123:0:...:weights"]
}
```

### `POST /events`

Batch endpoint used by vLLM's HTTP coordinator. It accepts a list of events with
`type` equal to `register`, `allocated`, `state`, `invalidate`, or `released`.
After processing a batch, the controller evaluates cap pressure and may enqueue
new evictions.

### `GET /stats`

Return controller accounting and active metadata.

Important fields:

```json
{
  "stats": {
    "client_count": 2,
    "backup_count": 117,
    "total_bytes": 4299161600,
    "global_cap_bytes": 1,
    "over_cap_bytes": 4299161599,
    "required_for_restore_bytes": 4299161600,
    "evictable_bytes": 0,
    "default_model_priority": 0,
    "model_priorities": {
      "qwen2p5_1p5b": 10
    },
    "pending_eviction_count": 0
  }
}
```

## Configuration

Add CPU backup coordinator settings under `controller` in the YAML config.

```yaml
controller:
  host: 127.0.0.1
  port: 19090
  metrics_path: results/controller_events.jsonl

  # Optional. If unset, the coordinator records metadata but does not
  # automatically enqueue cap-driven evictions.
  cpu_backup_global_cap_bytes: 4294967296

  # Optional. Higher means "retain longer". Missing models use the default.
  cpu_backup_default_model_priority: 0
  cpu_backup_model_priorities:
    qwen2p5_0p5b: 0
    qwen2p5_1p5b: 10
```

## Eviction policy

When `cpu_backup_global_cap_bytes` is set and active metadata bytes exceed the
cap, the controller chooses eviction victims from safe states only:

```text
cache_only
free_local
```

It does not automatically choose:

```text
required_for_restore
invalid
```

Victim ordering for Phase 3 model-priority policy is:

```text
lower model priority first
then older updated_at first (LRU within the same priority)
then larger backup first
```

Priority semantics:

- Higher integer priority means the model's backup should be retained longer.
- Lower integer priority means it is cheaper to evict.
- Unconfigured models use `cpu_backup_default_model_priority`.

Example:

```yaml
cpu_backup_default_model_priority: 0
cpu_backup_model_priorities:
  hot-model: 10
  cold-model: 0
```

If both `hot-model` and `cold-model` have cache-only backups, cap pressure evicts
`cold-model` first even if `hot-model` is older or larger.

## vLLM environment

A vLLM process enables the HTTP coordinator with:

```bash
export VLLM_CPU_BACKUP_COORDINATOR=daemon
export VLLM_CPU_BACKUP_COORDINATOR_URL=http://127.0.0.1:19090
export VLLM_CPU_BACKUP_COORDINATOR_TIMEOUT_S=1.0
export VLLM_CPU_BACKUP_COORDINATOR_CLIENT_ID=<unique-client-id>
export VLLM_CPU_BACKUP_COORDINATOR_MODEL_ID=<model-id>
```

If these variables are absent, vLLM uses a no-op coordinator and the controller
receives no CPU backup metadata.

## Verification

Controller focused verification:

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
.venv/bin/python -m pytest tests/test_backup_pool.py -q
.venv/bin/python -m ruff check \
  controller/backup_pool.py \
  controller/config.py \
  controller/main.py \
  controller/router.py \
  controller/schemas.py \
  tests/test_backup_pool.py
```

Covered behavior:

- Metadata registration/allocation/state APIs.
- Batch event endpoint.
- Cap-driven eviction queue.
- `released` events removing bytes from active accounting.
- Model priority policy retaining higher-priority backups over lower-priority
  backups even when the higher-priority backup is older or larger.

Real low-cap validation was run through `llm-switch-bench` with
`cpu_backup_global_cap_bytes: 1`. The expected result is that vLLM releases
cache-only backups after wake, so the following sleep performs D2H again instead
of clean-backup reuse.
