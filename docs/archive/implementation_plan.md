# External vLLM Model Switch Controller: Initial Implementation Plan

> Archived record. This plan used per-backup records, a fixed cap, and eviction
> terminology that were later replaced by aggregate usage, dynamic pressure, and the
> byte-release protocol. See the root [README](../../README.md), current
> [operations guide](../operations.md), and
> [CPU backup protocol](../cpu_backup_coordinator.md).

**Goal:** Build the first-stage external controller for multi-model vLLM Sleep Mode
experiments.

**Architecture:** One FastAPI controller manages multiple single-model vLLM server
processes. It exposes OpenAI-compatible endpoints, serializes model switches with a lock,
calls vLLM `/sleep` and `/wake_up`, proxies requests, and records per-request metrics.

**Stack:** Python, FastAPI, httpx, Pydantic, pytest, uv, and the vLLM HTTP API.

## Initial Scope

- Configuration-driven model pool
- Always-sleep-previous switching policy
- Controller lifecycle state machine
- vLLM management client
- OpenAI-compatible `/v1/models`, `/v1/chat/completions`, and `/v1/completions`
- Streaming proxy and TTFT metrics
- vLLM pool start/stop helpers
- A/B workload runner and result analyzer
- Unit and mocked routing-path tests

## Later CPU Backup Extension

After the first switching controller was built, an experimental metadata-only path was
added for coordinating vLLM pinned CPU backup pools. It supported local backup retention
without moving pinned-memory ownership out of vLLM.

The scope at that time was:

1. Metadata coordinator APIs under `/admin/cpu-backup/*`.
2. Client and backup records with `required_for_restore`, `cache_only`, `invalid`, and
   `released` states.
3. A global backup byte cap and eviction requests queued by a daemon.
4. Automatic eviction limited to safe `cache_only` and `free_local` records.
5. Model-priority victim selection, with LRU age and backup size as tie-breakers.

The design deliberately kept the data plane inside vLLM. vLLM owned pinned backup
tensors and every D2H/H2D copy; the controller owned accounting and policy only.

## Explicit Non-Goals

- Single-process multi-model mechanisms inside vLLM
- Partial eviction or lazy loading
- Production-grade authentication
- Multi-GPU scheduling

## Verification Planned at the Time

- Run unit tests with `uv run pytest`.
- Run lint with `uv run ruff check .`.
- Document the manual mock-server path.
- Preserve Git commits by implementation stage.
- Keep focused coordinator tests in `tests/test_backup_pool.py`.
- Document coordinator API, cap-based eviction, release accounting, and priority victim
  selection in `docs/cpu_backup_coordinator.md`.
