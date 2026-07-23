# Project Context

## Scope

This repository owns the external multi-backend control plane: model alias routing,
request reservations and drain, sleep/wake serialization, OpenAI proxying, aggregate
CPU-backup accounting, and host-memory pressure policy. Tensor validity, D2H/H2D,
and concrete backup reclamation remain inside vLLM.

## Repository conventions

- Keep current architecture and operating instructions under `docs/`.
- Keep completed plans and historical experiment reports under `docs/archive/`.
- Keep reusable example configuration under `configs/`; historical experiment-specific
  configuration belongs under `configs/archive/`.
- Keep machine paths and live-run output in ignored `configs/*.local.yaml`, `results/tmp/`,
  or `tmp/`; curated benchmark evidence belongs in `llm-switch-bench`.
- Preserve fail-closed lifecycle behavior and exactly-once streaming reservations when
  changing the request path.

## Related repositories

- `../vllm`: allocator-local CPU backup state, eager snapshot, version invalidation,
  sleep transactions, and coordinator client.
- `../llm-switch-bench`: benchmark adapters, raw/curated evidence, plots, and reports.
