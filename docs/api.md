# API Reference

The controller exposes a small OpenAI-compatible data plane and an unauthenticated
administrative API. The latter is intended only for a trusted local network.

## Data Plane

### `GET /v1/models`

Returns the configured logical aliases in an OpenAI-compatible model list. Availability
is not probed by this endpoint.

### `POST /v1/chat/completions`

### `POST /v1/completions`

Both endpoints require a JSON string field named `model`. The controller:

1. validates the alias;
2. performs any required drain and lifecycle transition;
3. reserves the target model before releasing the switch lock;
4. rewrites `model` to the backend's `served_model_name`;
5. proxies the request and response.

JSON and server-sent event responses are supported. Backend status codes and end-to-end
headers are preserved; hop-by-hop and invalid representation headers are removed. An
incoming `x-request-id` is forwarded, or the controller generates one when absent.

Unknown aliases return `404`. A missing or non-string `model` returns `400`. Backend
transport failures, lifecycle failures, and lifecycle timeouts return `502`.

## Health and State

### `GET /health`

Returns controller process health plus the current active alias, model lifecycle states,
and active request counts. It does not probe backend health.

### `GET /admin/state`

Returns the same lifecycle and request-reservation snapshot without the top-level
`ok` field.

### `POST /admin/switch/{model}`

Ensures the requested alias is ready using the configured switching policy. This is an
operator endpoint; normal inference clients should select models through the request
body instead.

## CPU Backup Coordinator

### `POST /admin/cpu-backup/register`

Registers or refreshes metadata for one worker process incarnation.

```json
{
  "client_id": "run:model-a:12345:incarnation",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "model-a",
  "gpu_uuid": null,
  "metadata": {}
}
```

### `POST /admin/cpu-backup/usage`

Reports aggregate process-local backup usage. `total_bytes` must equal the sum of the
four accounting categories.

```json
{
  "client_id": "run:model-a:12345:incarnation",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "model-a",
  "total_bytes": 3250585600,
  "released_bytes_total": 0,
  "required_for_restore_bytes": 0,
  "cache_only_bytes": 3250585600,
  "invalid_bytes": 0,
  "free_local_bytes": 0,
  "metadata": {}
}
```

Invalid accounting returns `422` during request validation. Conflicting monotonic or
process-incarnation state returns `409`.

### `GET /admin/cpu-backup/release-requests/{client_id}`

Returns the controller epoch, cumulative requested release bytes, current pending bytes,
and any release requests enqueued during the poll. Workers execute only the unseen delta
within an epoch, making repeated GET responses idempotent.

### `POST /admin/cpu-backup/release`

Queues an operator-directed release target for one client:

```json
{
  "client_id": "run:model-a:12345:incarnation",
  "target_free_bytes": 1073741824
}
```

The response reports the bytes that could be queued under current evictable accounting.

### `GET /admin/cpu-backup/stats`

Returns global and per-client accounting, pending commands, the controller epoch, and a
memory-pressure monitor snapshot when that monitor is enabled.

The complete protocol, including acknowledgement and physical-reclaim semantics, is in
[CPU Backup Coordinator](cpu_backup_coordinator.md).
