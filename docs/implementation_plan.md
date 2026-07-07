# vLLM External Model Switch Controller Implementation Plan

**Goal:** Build a first-stage external controller for multi-model vLLM Sleep Mode experiments.

**Architecture:** Multiple single-model vLLM server processes are managed by one FastAPI controller. The controller exposes OpenAI-compatible endpoints, serializes model switches with a lock, calls vLLM `/sleep` and `/wake_up`, proxies requests, and records per-request metrics.

**Tech Stack:** Python, FastAPI, httpx, pydantic, pytest, uv, vLLM HTTP API.

## Stage 1 Scope

- Config-driven model pool.
- Always-sleep-previous policy.
- Controller state machine.
- vLLM management client.
- OpenAI-compatible `/v1/models`, `/v1/chat/completions`, `/v1/completions`.
- Streaming proxy and TTFT metrics.
- vLLM pool launch/stop helpers.
- Workload runner and analyzer for first-stage A/B workload.
- Tests for core behavior and mock router paths.

## CPU backup coordinator extension

The controller also has an experimental metadata-only coordination path for
vLLM pinned CPU backup pools. This was added after the first-stage switching
controller to support coordinated local backup retention without moving pinned
memory ownership out of vLLM.

Scope implemented so far:

1. Metadata coordinator APIs under `/admin/cpu-backup/*`.
2. Client and backup records with states such as `required_for_restore`,
   `cache_only`, `invalid`, and `released`.
3. Global backup byte cap with daemon-enqueued eviction requests.
4. Safe automatic eviction limited to `cache_only` and `free_local` records.
5. Model-priority eviction policy: lower priority models are evicted before
   higher priority models; LRU and backup size break ties.

The design intentionally keeps the data plane in vLLM. vLLM owns pinned CPU
backup tensors and all D2H/H2D copies. The controller owns only accounting and
policy.

## Explicit Non-goals

- Single-process multi-model vLLM internals.
- Partial eviction / lazy loading.
- Production authentication.
- Multi-GPU scheduling.

## Verification

- Unit tests pass with `uv run pytest`.
- Lint pass with `uv run ruff check .`.
- Manual mock server path documented.
- Git commits preserved by implementation phase.
- CPU backup coordinator focused tests live in `tests/test_backup_pool.py`.
- CPU backup API, cap-driven eviction, released accounting, and model-priority
  victim ordering are documented in `docs/cpu_backup_coordinator.md`.
