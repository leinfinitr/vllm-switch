# vLLM Model Switch Controller

External model switching controller for evaluating vLLM Sleep Mode as a multi-model serving baseline.

## First-stage goal

Provide one OpenAI-compatible endpoint in front of multiple single-model vLLM server processes. The first policy is **Always sleep previous**:

1. Route request by the OpenAI `model` field.
2. If target model differs from the active model, sleep the previous active vLLM server.
3. Wake the target vLLM server.
4. Forward the request and record switch/TTFT/E2E metrics.

This project is intentionally outside vLLM for the first stage, so it can compare e2e behavior with cold reload, dedicated serving, SwapServeLLM, and ServerlessLLM.

## Implemented components

- Config-driven model pool: `controller/config.py`
- Always-sleep-previous policy: `controller/policies.py`
- Runtime state machine: `controller/state.py`
- vLLM management/proxy client: `controller/vllm_client.py`
- FastAPI OpenAI-compatible controller: `controller/main.py`, `controller/router.py`
- Metadata-only CPU backup coordinator: `controller/backup_pool.py`
- JSONL metrics recorder: `controller/metrics.py`
- Sequential vLLM pool launcher/stopper: `scripts/launch_vllm_pool.py`, `scripts/stop_vllm_pool.py`
- GPU metrics helper: `scripts/collect_gpu_metrics.py`
- Workload runner/analyzer: `benchmarks/run_workload.py`, `benchmarks/analyze_results.py`

## CPU backup coordination

The controller can also act as a metadata-only daemon for vLLM pinned CPU backup
pools. vLLM still allocates pinned CPU backup tensors locally and performs D2H /
H2D copies itself; the controller only records metadata, accounts global bytes,
and enqueues cache-only eviction requests under policy pressure.

Implemented CPU backup policy features:

- client and backup metadata tracking;
- backup states including `required_for_restore` and `cache_only`;
- global cap driven daemon eviction requests;
- model-priority eviction policy where higher priority models are retained
  longer;
- stats and batch event APIs under `/admin/cpu-backup/*`.

See `docs/cpu_backup_coordinator.md` for API, config, safety invariants, and
verification details.

## Quick start

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv sync --dev
uv run python -m pytest -q
uv run ruff check .
```

See:

- `docs/implementation_plan.md`
- `docs/first_stage_usage.md`
- `docs/cpu_backup_coordinator.md`
