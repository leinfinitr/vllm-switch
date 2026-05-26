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
- JSONL metrics recorder: `controller/metrics.py`
- Sequential vLLM pool launcher/stopper: `scripts/launch_vllm_pool.py`, `scripts/stop_vllm_pool.py`
- GPU metrics helper: `scripts/collect_gpu_metrics.py`
- Workload runner/analyzer: `benchmarks/run_workload.py`, `benchmarks/analyze_results.py`

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
