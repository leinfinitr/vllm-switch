# Experiment 001: vLLM Sleep Controller A/B Alternating

Date: 2026-05-26

## Goal

Validate the first-stage external vLLM model switching controller with the `Always sleep previous` policy on a real GPU and collect first end-to-end metrics.

## Environment

- Host GPU: NVIDIA GeForce RTX 3080, 10 GiB
- vLLM: 0.21.0 installed in project `.venv`
- PyTorch: 2.11.0+cu130
- CUDA runtime reported by torch: 13.0
- `CUDA_HOME`: `/home/ljl/cuda-13.0`
- Model A: `/home/ljl/models/hf/Qwen2.5-0.5B-Instruct`, served as `qwen-a`
- Model B: hardlink copy `/home/ljl/models/hf/Qwen2.5-0.5B-Instruct-B`, served as `qwen-b`

Note: network was unavailable when trying to fetch a second distinct HF model, so this smoke experiment uses two distinct vLLM processes backed by the same Qwen2.5-0.5B-Instruct weights under different local paths/model names. This still validates process-level sleep/wake switching and controller overhead; later comparison should use distinct model families/sizes.

## Config

- Controller config: `configs/models.exp001.yaml`
- Workload config: `configs/workloads/exp001_ab_alternating.yaml`
- Pattern: `qwen-a, qwen-b, ...` alternating
- Requests: 10
- Request rate: 0.2 req/s
- Max output tokens: 32
- Streaming: enabled
- vLLM `--gpu-memory-utilization`: 0.35 per backend
- vLLM `--max-model-len`: 1024
- Sleep level: 1

## Commands

```bash
PYTHONPATH=. uv run python scripts/launch_vllm_pool.py \
  --config configs/models.exp001.yaml \
  --pid-file pids.exp001.json \
  > results/exp_001/launch.log 2>&1

PYTHONPATH=. uv run python -m controller.main \
  --config configs/models.exp001.yaml \
  > results/exp_001/controller.log 2>&1

uv run python scripts/collect_gpu_metrics.py \
  --output results/exp_001/gpu.csv \
  --interval-s 1 \
  --duration-s 120 \
  > results/exp_001/gpu_metrics.log 2>&1

PYTHONPATH=. uv run python benchmarks/run_workload.py \
  --config configs/workloads/exp001_ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/exp_001/client.jsonl

PYTHONPATH=. uv run python benchmarks/analyze_results.py \
  --input results/exp_001/controller_events.jsonl \
  --output results/exp_001/controller_summary.json
```

## Results

### Controller-side metrics

From `results/exp_001/controller_summary.json`:

| Metric | Count | Mean ms | P50 ms | P95 ms | Min ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| E2E TTFT | 10 | 303.97 | 218.04 | 677.86 | 214.01 | 908.85 |
| E2E latency | 10 | 378.12 | 294.97 | 748.65 | 286.31 | 977.58 |
| Switch latency | 9 | 243.54 | 207.60 | 407.83 | 203.90 | 540.88 |
| Sleep latency | 9 | 123.82 | 87.74 | 282.62 | 87.42 | 412.18 |
| Wake latency | 9 | 119.71 | 120.11 | 125.45 | 116.02 | 128.68 |
| Backend TTFT after wake | 10 | 84.78 | 10.71 | 383.12 | 10.01 | 395.52 |

There were 10/10 successful requests and 0 errors.

### Warm-switch steady state

The first switched request was slower because it switched from the initially awake `qwen-a` into the already-sleeping `qwen-b` after startup. After that, switch latency stabilized around 204-208 ms:

- steady-state sleep: about 87-88 ms
- steady-state wake: about 116-121 ms
- steady-state switch: about 204-208 ms
- steady-state E2E TTFT: about 214-219 ms
- steady-state E2E latency: about 286-296 ms

### Startup observations

From `results/exp_001/launch.log`:

- vLLM A init engine took 54.79 s, including compilation 6.81 s.
- vLLM B init engine took 13.30 s, including compilation 6.89 s.
- Initial sleep of B freed 2.96 GiB GPU memory, with 0.98 GiB backed up in CPU and the rest discarded.
- Initial sleep of B took 0.417 s.

The first server's init was much slower than the second, likely due to first-time CUDA graph / compile / cache warmup effects.

### GPU metrics

From `results/exp_001/gpu.csv` summary:

- Samples: 80
- GPU memory used: min 4104 MB, mean 4493 MB, max 4854 MB
- GPU utilization: min 0%, mean 1.2%, max 73%

At experiment end, controller state was:

```json
{"active_model":"qwen-b","states":{"qwen-a":"sleeping","qwen-b":"awake"}}
```

`nvidia-smi` showed:

```text
qwen-a EngineCore: ~898 MB
qwen-b EngineCore: ~3484 MB
```

This matches the expected level-1 behavior: sleeping process still holds some GPU/runtime memory but releases most model/KV memory.

## Interpretation

For this 0.5B model pair on RTX 3080, vLLM level-1 sleep/wake switching has a steady-state switch cost around 205 ms. Because backend TTFT after wake is only around 10 ms in steady state, most user-visible TTFT during alternating workload comes from controller-triggered sleep/wake, not from the generation path itself.

This validates the first-stage controller path and gives an initial baseline for later comparison:

- Dedicated serving should remove the ~205 ms switching penalty but needs both models resident.
- Cold reload should be far slower, likely seconds to tens of seconds depending on compile/cache state.
- More advanced policies should aim to reduce the number of switches or overlap/hide the sleep/wake cost.

## Artifacts

- `results/exp_001/launch.log`
- `results/exp_001/controller.log`
- `results/exp_001/controller_events.jsonl`
- `results/exp_001/controller_summary.json`
- `results/exp_001/client.jsonl`
- `results/exp_001/client_summary.json`
- `results/exp_001/gpu.csv`
