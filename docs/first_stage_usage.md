# First-stage Usage Guide

This document records how to reproduce the first-stage vLLM external model switching controller experiment.

## 1. Install dependencies

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv sync --dev
```

## 2. Configure models

Copy the example config and edit model names, ports, and optional launch commands:

```bash
cp configs/models.example.yaml configs/models.yaml
```

Minimal config assumes vLLM backends are already running:

```yaml
models:
  qwen-0.5b:
    backend_url: http://127.0.0.1:8101
    served_model_name: qwen-0.5b
    sleep_level: 1
  qwen-1.5b:
    backend_url: http://127.0.0.1:8102
    served_model_name: qwen-1.5b
    sleep_level: 1
controller:
  port: 9000
  policy: always_sleep_previous
  startup_awake_model: qwen-0.5b
  metrics_path: results/controller_events.jsonl
```

To let `scripts/launch_vllm_pool.py` start processes, add `launch_command` to each model, for example:

```yaml
launch_command:
  - vllm
  - serve
  - Qwen/Qwen2.5-0.5B-Instruct
  - --host
  - 127.0.0.1
  - --port
  - "8101"
  - --served-model-name
  - qwen-0.5b
  - --enable-sleep-mode
```

The launcher sets `VLLM_SERVER_DEV_MODE=1` by default because vLLM sleep/wake HTTP endpoints are dev management endpoints.

## 3. Launch vLLM pool

```bash
uv run python scripts/launch_vllm_pool.py --config configs/models.yaml --pid-file pids.json
```

The launcher starts or probes each backend sequentially, sleeps non-startup models, then wakes `startup_awake_model`.

To stop launched processes:

```bash
uv run python scripts/stop_vllm_pool.py --pid-file pids.json
```

## 4. Start controller

```bash
uv run python -m controller.main --config configs/models.yaml
```

Check state:

```bash
curl http://127.0.0.1:9000/admin/state
curl http://127.0.0.1:9000/v1/models
```

Manual switch:

```bash
curl -X POST http://127.0.0.1:9000/admin/switch/qwen-1.5b
```

## 5. Run A/B alternating workload

```bash
uv run python benchmarks/run_workload.py \
  --config configs/workloads/ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/ab_alternating_client.jsonl
```

The controller also records detailed switch metrics to `results/controller_events.jsonl`.

Analyze either file:

```bash
uv run python benchmarks/analyze_results.py \
  --input results/controller_events.jsonl \
  --output results/controller_summary.json
```

## 6. Collect GPU metrics during a run

In another terminal:

```bash
uv run python scripts/collect_gpu_metrics.py \
  --output results/gpu_metrics.csv \
  --interval-s 1 \
  --duration-s 300
```

## 7. Verification commands

```bash
uv run python -m pytest -q
uv run ruff check .
```

## 8. First-stage expected outputs

- `results/controller_events.jsonl`: per controller request, including sleep/wake/switch/backend TTFT/E2E latency.
- `results/ab_alternating_client.jsonl`: client-observed request metrics.
- `results/controller_summary.json`: summary stats: mean, p50, p95, p99, min, max.
- `results/gpu_metrics.csv`: sampled GPU memory/utilization timeline.
