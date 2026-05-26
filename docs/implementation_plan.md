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
