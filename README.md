# vLLM Model Switch Controller

An external control plane for routing OpenAI-compatible requests across a pool of
single-model vLLM backends. The controller serializes model lifecycle transitions,
drains in-flight requests before sleeping a backend, and can coordinate reclaimable
pinned CPU backups under host-memory pressure.

> [!IMPORTANT]
> This is an experimental research system, not a production gateway. The management
> API has no authentication or transport security. Bind it to a trusted interface and
> keep it behind an authenticated proxy if it is used outside an isolated host.

## What It Does

- Exposes `/v1/models`, `/v1/chat/completions`, and `/v1/completions` from one base URL.
- Maps a logical model alias to a backend `served_model_name`.
- Serializes sleep/wake transitions and verifies their post-conditions.
- Drains active requests before sleeping their model.
- Holds exactly one reservation for the lifetime of each JSON or streaming request.
- Preserves backend status codes, end-to-end headers, and response streams.
- Records switch, queue, time-to-first-token (TTFT), and end-to-end latency as JSONL.
- Aggregates process-local CPU backup usage and issues byte-based release targets.

The controller never owns backup tensors and never performs D2H or H2D copies. Tensor
validity, copy synchronization, restore requirements, and concrete reclamation remain
inside vLLM.

## How It Fits Together

```text
OpenAI client
    |
    v
model-switch controller :9000
    |-- alias model-a --> vLLM backend :8101
    `-- alias model-b --> vLLM backend :8102

vLLM workers -- aggregate CPU backup usage --> controller
vLLM workers <-- cumulative release targets -- controller
```

The default `always_sleep_previous` policy keeps at most one configured model awake.
A request for another alias waits for the current model to drain, sleeps it, wakes the
target, reserves the target, and only then forwards the request.

## Requirements

- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/) for the documented environment workflow.
- One or more vLLM servers with Sleep Mode and development management endpoints enabled.
- Linux when host-memory pressure coordination is enabled; the monitor reads
  `/proc/meminfo`.
- The companion research vLLM implementation for CPU backup reuse and reclamation.
  Standard vLLM can still be used for the basic external switching path when it exposes
  the required lifecycle endpoints.

## Quick Start

Install the project and run its checks:

```bash
uv sync --dev
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```

Create a machine-local configuration:

```bash
cp configs/models.example.yaml configs/models.local.yaml
$EDITOR configs/models.local.yaml
```

The minimal example expects its vLLM backends to be running already. For a launcher
template with explicit commands, copy `configs/models.request_switch.example.yaml`
instead and replace every `/path/to/...` value.

Start the controller, then prepare the backend pool in another shell:

```bash
# Shell 1
uv run python -m controller.main --config configs/models.local.yaml

# Shell 2
uv run python -m scripts.launch_vllm_pool \
  --config configs/models.local.yaml \
  --pid-file pids.json
```

Do not send inference traffic until the launcher finishes. It initializes or probes
each backend sequentially, sleeps each one, and finally wakes `startup_awake_model`.

List aliases and send a request:

```bash
curl -fsS http://127.0.0.1:9000/v1/models

curl -fsS http://127.0.0.1:9000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen-0.5b",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "max_tokens": 32
  }'
```

Changing only the request's `model` field drives routing and any required lifecycle
transition. Clients do not need to call `/admin/switch`.

## Documentation

- [Getting started](docs/getting-started.md)
- [Configuration reference](docs/configuration.md)
- [Operations and validation](docs/operations.md)
- [API reference](docs/api.md)
- [Architecture](docs/architecture.md)
- [CPU backup coordinator protocol](docs/cpu_backup_coordinator.md)
- [Documentation index and archive](docs/README.md)

## Repository Layout

```text
controller/      Runtime control plane and proxy
scripts/         Backend lifecycle, smoke-test, and telemetry utilities
benchmarks/      Lightweight workload runner and result analyzer
configs/         Reusable examples and current workload definitions
tests/           Unit, routing, lifecycle, and pressure-policy tests
docs/            Current architecture and operating documentation
docs/archive/    Completed plans and historical experiment reports
results/         Curated controller evidence; transient runs stay ignored
```

Machine paths, credentials, PID files, logs, and live-run output must stay in ignored
`configs/*.local.yaml`, `results/tmp/`, or `tmp/` paths. Cross-system benchmark evidence
belongs in the sibling `llm-switch-bench` repository.

## Project Scope

This repository owns the external multi-backend control plane: alias routing, request
reservations and drain, sleep/wake serialization, OpenAI proxying, aggregate CPU backup
accounting, and host-memory pressure policy. It does not provide a general cluster
scheduler, backend authentication, tensor-level backup management, or production
reconciliation after process failure.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes and [SECURITY.md](SECURITY.md)
before deploying or reporting a security issue.
