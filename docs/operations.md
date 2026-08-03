# Operations and Validation

This guide covers routine operation after initial setup. Follow
[Getting Started](getting-started.md) first and use the
[Configuration Reference](configuration.md) for field definitions.

## Startup Sequence

When CPU backup workers use the daemon coordinator, start the controller before the
backends so registration succeeds. The same order is safe for a basic switching pool.

```bash
# Shell 1: start the control plane
uv run python -m controller.main \
  --config configs/models.request_switch.local.yaml

# Shell 2: launch or prepare backends
mkdir -p results/tmp/request-switch
uv run python -m scripts.launch_vllm_pool \
  --config configs/models.request_switch.local.yaml \
  --pid-file results/tmp/request-switch/pids.json
```

The launcher creates one process group per configured `launch_command`, waits for
backend health, and verifies every sleep/wake post-condition. If preparation fails, it
terminates all processes it started. It does not terminate externally managed backends.

Do not send inference traffic until the launcher has written its PID file. During
initialization, the controller's configured expected state may not yet match the backend
pool.

## Health and State Checks

```bash
curl -fsS http://127.0.0.1:9000/health
curl -fsS http://127.0.0.1:9000/admin/state
curl -fsS http://127.0.0.1:9000/v1/models
curl -fsS http://127.0.0.1:9000/admin/cpu-backup/stats
```

`/health` checks the controller process and reports its in-memory state; it does not
probe all backends. Query each backend's `/health` and `/is_sleeping` endpoints when
diagnosing lifecycle state.

For an operator-directed transition:

```bash
curl -fsS -X POST \
  http://127.0.0.1:9000/admin/switch/model-b
```

Normal inference clients should not use this endpoint. Selecting `model-b` in an OpenAI
request performs the same readiness transition and reserves the request atomically.

## Request-Driven Smoke Test

The smoke client sends only standard `/v1/chat/completions` requests. It exercises basic
alias switching and an overlapping streaming request, then reads `/admin/state` to
confirm reservations return to zero.

```bash
uv run python scripts/smoke_openai_switch.py \
  --base-url http://127.0.0.1:9000 \
  --models model-a model-b \
  --output results/tmp/request-switch/smoke.jsonl
```

Use two aliases that exist in the active configuration. A successful run exits zero and
writes request-level evidence to the selected JSONL file.

## Lightweight A/B Workload

The repository includes a simple sequential workload for controller validation. It is
not the primary cross-system benchmark harness.

```bash
uv run python benchmarks/run_workload.py \
  --config configs/workloads/ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/tmp/ab-alternating/client.jsonl
```

Analyze either client or controller JSONL:

```bash
uv run python benchmarks/analyze_results.py \
  --input results/controller_events.jsonl \
  --output results/tmp/ab-alternating/controller-summary.json
```

Use the sibling `llm-switch-bench` repository for frozen traces, cross-system adapters,
repeated runs, plots, and curated benchmark evidence.

## CPU Backup Pressure Validation

Run paired retain and release checks with the same two aliases:

```bash
uv run python scripts/validate_backup_pressure.py \
  --base-url http://127.0.0.1:9000 \
  --mode retain \
  --model-a model-a \
  --model-b model-b \
  --output results/tmp/backup-pressure/retain.json

uv run python scripts/validate_backup_pressure.py \
  --base-url http://127.0.0.1:9000 \
  --mode release \
  --model-a model-a \
  --model-b model-b \
  --output results/tmp/backup-pressure/release.json
```

The release mode sends an explicit release request. To validate automatic pressure
policy, configure safe watermarks above the current `MemAvailable`; do not exhaust host
RAM on a shared machine. Logical byte counters alone are not proof that memory returned
to the operating system. See the [coordinator protocol](cpu_backup_coordinator.md).

## GPU Telemetry

Collect `nvidia-smi` metrics in another shell during a run:

```bash
uv run python scripts/collect_gpu_metrics.py \
  --output results/tmp/ab-alternating/gpu.csv \
  --interval-s 1 \
  --duration-s 300
```

The collector records a GPU memory and utilization timeline. Preserve machine metadata,
model revisions, configuration, and repository commits alongside any curated result.

## Output Files

Common outputs are:

- `results/controller_events.jsonl`: controller request records, including queue, drain,
  sleep, wake, switch, first-byte, and end-to-end timing where applicable.
- A workload `client.jsonl`: request timing and outcome observed by the client.
- Analyzer JSON: count, mean, p50, p95, p99, minimum, and maximum summaries.
- GPU CSV: sampled memory and utilization data.
- A launcher PID file: process group leaders managed by the stop script.

Keep live runs under ignored `results/tmp/`. Only intentional, reviewable evidence should
be curated elsewhere, and cross-system evidence belongs in `llm-switch-bench`.

## Shutdown

Stop launcher-managed backend process groups first:

```bash
uv run python -m scripts.stop_vllm_pool \
  --pid-file results/tmp/request-switch/pids.json
```

Then stop the controller with `Ctrl-C`. Stop externally managed vLLM servers with their
own process manager.

## Troubleshooting

### A backend never becomes healthy

Run the configured `launch_command` directly and inspect its stderr. Verify model paths,
CUDA compatibility, port availability, and the vLLM executable. The controller's own
environment does not need to contain the research vLLM package, but backend commands do.

### Sleep or wake times out

Confirm `VLLM_SERVER_DEV_MODE=1`, `--enable-sleep-mode`, and the `/is_sleeping` endpoint.
The controller marks uncertain transitions as `error` and fails closed. A later request
attempts state reconciliation, but operators should inspect the backend before retrying.

### Requests return `502`

Check controller logs and backend health. The controller uses `502` for backend transport
errors, failed lifecycle responses, invalid lifecycle probes, and transition timeouts.
Backend-generated non-2xx inference responses are proxied unchanged.

### Local requests unexpectedly use an HTTP proxy

Controller-to-backend and launcher lifecycle clients explicitly set `trust_env=False`.
If an external test client still uses a proxy, unset its proxy environment or configure
`NO_PROXY` for loopback addresses.

### CPU backup usage stays stale after a worker exits

The controller has no worker lease or heartbeat. Process-incarnation records remain in
accounting after abnormal exit, which can cause conservative over-reclamation requests.
Restart the controller after confirming all affected workers are stopped.

## Repository Checks

Documentation-only changes should still run the full fast test and lint suite:

```bash
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```
