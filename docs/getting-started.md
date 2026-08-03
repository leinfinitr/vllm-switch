# Getting Started

This guide runs the controller against a small pool of single-model vLLM servers. See
the [configuration reference](configuration.md) for every field and
[operations](operations.md) for repeatable validation workflows.

## 1. Install the Controller

Prerequisites:

- Python 3.11 or newer
- `uv`
- A CUDA environment supported by the vLLM checkout used for the backends
- Enough GPU memory to initialize each configured model individually

Install the development environment from the repository root:

```bash
uv sync --dev
```

Run the fast verification commands before connecting real backends:

```bash
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```

## 2. Create a Local Configuration

For already-running backends:

```bash
cp configs/models.example.yaml configs/models.local.yaml
```

For backends that the repository launcher should start:

```bash
cp configs/models.request_switch.example.yaml \
  configs/models.request_switch.local.yaml
```

Files matching `configs/*.local.yaml` are ignored. Keep model paths, virtual environment
paths, access tokens, and host-specific ports there.

At minimum, each alias needs a backend URL and the name exposed by that vLLM server:

```yaml
models:
  model-a:
    backend_url: http://127.0.0.1:8101
    served_model_name: model-a
    sleep_level: 1
  model-b:
    backend_url: http://127.0.0.1:8102
    served_model_name: model-b
    sleep_level: 1

controller:
  host: 127.0.0.1
  port: 9000
  policy: always_sleep_previous
  startup_awake_model: model-a
  metrics_path: results/controller_events.jsonl
```

The alias under `models` is the value clients send. `served_model_name` is written into
the request forwarded to that backend.

## 3. Enable vLLM Lifecycle Endpoints

Every backend must expose `/health`, `/sleep`, `/wake_up`, and `/is_sleeping`. A typical
launch command includes:

```bash
VLLM_SERVER_DEV_MODE=1 vllm serve /path/to/model-a \
  --host 127.0.0.1 \
  --port 8101 \
  --served-model-name model-a \
  --enable-sleep-mode
```

Use separate ports and processes for each model. The launcher sets
`VLLM_SERVER_DEV_MODE=1` by default for processes defined by `launch_command`.

CPU backup coordination additionally requires the companion research vLLM checkout.
Configure its coordinator environment as shown in
`configs/models.request_switch.example.yaml`; an installed upstream wheel does not
contain the research backup pool.

## 4. Start the Controller and Prepare the Pool

Start the controller first. This is required when vLLM workers register with the CPU
backup coordinator during startup and is also valid for the basic switching setup.

```bash
uv run python -m controller.main --config configs/models.local.yaml
```

In another shell, start or prepare the backends:

```bash
uv run python -m scripts.launch_vllm_pool \
  --config configs/models.local.yaml \
  --pid-file pids.json
```

Use `--skip-launch` to prepare backends that are already running even when the config
contains `launch_command` entries.

The launcher waits for health, sleeps and verifies each backend in configuration order,
then wakes and verifies `startup_awake_model`. This sequential initialization supports
pools whose models cannot be awake on the GPU at the same time. Do not send inference
requests until the launcher reports completion.

## 5. Verify the Pool

```bash
curl -fsS http://127.0.0.1:9000/health
curl -fsS http://127.0.0.1:9000/admin/state
curl -fsS http://127.0.0.1:9000/v1/models
```

Send a non-streaming chat request:

```bash
curl -fsS http://127.0.0.1:9000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "model-b",
    "messages": [{"role": "user", "content": "Reply with one word."}],
    "max_tokens": 8
  }'
```

If `model-a` was active, this request drains it, sleeps it, wakes `model-b`, and forwards
the request. The same workflow applies to streaming requests.

## 6. Stop Launcher-Managed Backends

```bash
uv run python -m scripts.stop_vllm_pool --pid-file pids.json
```

Stop the controller separately with `Ctrl-C`. The stop script only manages process
groups recorded by the launcher; it does not stop externally managed backends.
