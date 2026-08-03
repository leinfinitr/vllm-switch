# Experiment 001: Alternating vLLM Sleep Controller

> Historical report for the first-stage baseline recorded on 2026-05-26. Its environment,
> paths, and commands are not current recommendations. See the current
> [operations guide](../operations.md).

**Date:** 2026-05-26

## Objective

Validate the first-stage external vLLM model-switch controller on a real GPU with the
`always_sleep_previous` policy and collect the initial end-to-end measurements.

## Environment

- GPU: NVIDIA GeForce RTX 3080, 10 GiB
- vLLM: 0.21.0, installed in the project `.venv`
- PyTorch: 2.11.0+cu130
- CUDA runtime reported by PyTorch: 13.0
- `CUDA_HOME`: `/home/ljl/cuda-13.0`
- Model A: `/home/ljl/models/hf/Qwen2.5-0.5B-Instruct`, served as `qwen-a`
- Model B: hard-linked copy at
  `/home/ljl/models/hf/Qwen2.5-0.5B-Instruct-B`, served as `qwen-b`

Downloading a second Hugging Face model was not possible during the run. The smoke test
therefore used two vLLM processes backed by the same Qwen2.5-0.5B-Instruct weights but
different local paths and served names. This still exercised process-level sleep/wake
and controller overhead. Later comparisons were expected to use different model families
or sizes.

## Configuration

- Controller config: `configs/archive/exp_001/models.yaml`
- Workload config: `configs/archive/exp_001/ab_alternating.yaml`
- Pattern: alternating `qwen-a, qwen-b, ...`
- Requests: 10
- Request rate: 0.2 requests/second
- Maximum output tokens: 32
- Streaming: enabled
- vLLM `--gpu-memory-utilization`: 0.35 per backend
- vLLM `--max-model-len`: 1024
- Sleep level: 1

## Commands Used

```bash
PYTHONPATH=. uv run python scripts/launch_vllm_pool.py \
  --config configs/archive/exp_001/models.yaml \
  --pid-file pids.exp001.json \
  > results/exp_001/launch.log 2>&1

PYTHONPATH=. uv run python -m controller.main \
  --config configs/archive/exp_001/models.yaml \
  > results/exp_001/controller.log 2>&1

uv run python scripts/collect_gpu_metrics.py \
  --output results/exp_001/gpu.csv \
  --interval-s 1 \
  --duration-s 120 \
  > results/exp_001/gpu_metrics.log 2>&1

PYTHONPATH=. uv run python benchmarks/run_workload.py \
  --config configs/archive/exp_001/ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/exp_001/client.jsonl

PYTHONPATH=. uv run python benchmarks/analyze_results.py \
  --input results/exp_001/controller_events.jsonl \
  --output results/exp_001/controller_summary.json
```

## Results

### Controller Metrics

Source: `results/exp_001/controller_summary.json`.

| Metric | Count | Mean ms | P50 ms | P95 ms | Min ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| End-to-end TTFT | 10 | 303.97 | 218.04 | 677.86 | 214.01 | 908.85 |
| End-to-end latency | 10 | 378.12 | 294.97 | 748.65 | 286.31 | 977.58 |
| Switch latency | 9 | 243.54 | 207.60 | 407.83 | 203.90 | 540.88 |
| Sleep latency | 9 | 123.82 | 87.74 | 282.62 | 87.42 | 412.18 |
| Wake latency | 9 | 119.71 | 120.11 | 125.45 | 116.02 | 128.68 |
| Backend TTFT after wake | 10 | 84.78 | 10.71 | 383.12 | 10.01 | 395.52 |

All 10 requests succeeded with no recorded errors.

### Warm Switching Steady State

The first switched request was slower because it moved from the startup-awake `qwen-a`
to `qwen-b`, which had been put to sleep immediately after startup. Later switch latency
stabilized around 204-208 ms:

- steady-state sleep: about 87-88 ms
- steady-state wake: about 116-121 ms
- steady-state switch: about 204-208 ms
- steady-state end-to-end TTFT: about 214-219 ms
- steady-state end-to-end latency: about 286-296 ms

### Startup Observations

From `results/exp_001/launch.log`:

- vLLM A engine initialization took 54.79 seconds, including 6.81 seconds of compilation.
- vLLM B engine initialization took 13.30 seconds, including 6.89 seconds of compilation.
- B's initial sleep released 2.96 GiB of GPU memory; 0.98 GiB was backed up to CPU and
  the remainder was discarded.
- B's initial sleep took 0.417 seconds.

The first server initialized much more slowly than the second, plausibly because of
first-use CUDA graph, compilation, or cache-warmup effects.

### GPU Metrics

Summary of `results/exp_001/gpu.csv`:

- Samples: 80
- GPU memory: 4104 MB minimum, 4493 MB mean, 4854 MB maximum
- GPU utilization: 0% minimum, 1.2% mean, 73% maximum

The final controller state was:

```json
{"active_model":"qwen-b","states":{"qwen-a":"sleeping","qwen-b":"awake"}}
```

`nvidia-smi` showed:

```text
qwen-a EngineCore: ~898 MB
qwen-b EngineCore: ~3484 MB
```

This matched the expected level-1 behavior: a sleeping process retained some GPU/runtime
memory while releasing most model and KV memory.

## Interpretation

For this pair of 0.5B model processes on an RTX 3080, steady-state level-1 sleep/wake
switching cost about 205 ms. Backend TTFT after wake was only about 10 ms in steady state,
so most user-visible TTFT in the alternating workload came from controller-triggered
sleep/wake rather than generation.

The run validated the first-stage controller path and provided an initial baseline:

- Dedicated serving could avoid the roughly 205 ms switch penalty but would require both
  models to remain resident.
- Cold reload was expected to be much slower, from seconds to tens of seconds depending
  on compilation and cache state.
- More advanced policies would need to reduce switch frequency or overlap/hide lifecycle
  cost.

These conclusions apply only to this small smoke configuration and are not a cross-system
performance claim.

## Artifacts

- `results/exp_001/launch.log`
- `results/exp_001/controller.log`
- `results/exp_001/controller_events.jsonl`
- `results/exp_001/controller_summary.json`
- `results/exp_001/client.jsonl`
- `results/exp_001/client_summary.json`
- `results/exp_001/gpu.csv`
