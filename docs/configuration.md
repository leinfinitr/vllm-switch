# Configuration Reference

The controller reads one YAML document with top-level `models` and `controller` maps.
Invalid values are rejected by Pydantic during startup.

## Model Entries

Each key under `models` is a public alias accepted by the OpenAI-compatible endpoints.

| Field | Required | Default | Description |
|---|---:|---:|---|
| `backend_url` | yes | - | Base URL of one single-model vLLM server. |
| `served_model_name` | yes | - | Model name written into forwarded request bodies. |
| `sleep_level` | no | `1` | vLLM sleep level; accepted values are `1` and `2`. |
| `wake_tags` | no | `null` | Optional repeated `tags` values sent to `/wake_up`. |
| `launch_command` | no | `null` | Argument vector used by `scripts.launch_vllm_pool`. |
| `env` | no | `{}` | Environment overrides for a launcher-managed process. |
| `cwd` | no | `null` | Working directory for a launcher-managed process. |

Use a YAML list for `launch_command`; it is passed directly to `subprocess.Popen` and is
not interpreted by a shell:

```yaml
models:
  model-a:
    backend_url: http://127.0.0.1:8101
    served_model_name: model-a
    sleep_level: 1
    cwd: /path/to/vllm-checkout
    env:
      VLLM_SERVER_DEV_MODE: "1"
    launch_command:
      - /path/to/venv/bin/vllm
      - serve
      - /path/to/model-a
      - --host
      - 127.0.0.1
      - --port
      - "8101"
      - --served-model-name
      - model-a
      - --enable-sleep-mode
```

## Controller Settings

| Field | Default | Description |
|---|---:|---|
| `host` | `0.0.0.0` | Controller listen address. Prefer `127.0.0.1` unless a trusted proxy is required. |
| `port` | `9000` | Controller listen port. |
| `policy` | `always_sleep_previous` | Switching policy name. |
| `startup_awake_model` | `null` | Alias expected to be awake after pool preparation. |
| `request_timeout_s` | `600` | Timeout for proxied inference requests. |
| `switch_timeout_s` | `600` | Shared deadline for each lifecycle call and state verification. |
| `metrics_path` | `results/controller_events.jsonl` | Per-request controller metrics output. |

`startup_awake_model`, when set, must name a configured alias. At least one model is
required.

### Switching Policies

- `always_sleep_previous`: keep one model awake; drain and sleep it before waking a
  different model.
- `always_awake_previous`: wait for requests on other models to finish and wake the
  target without sleeping already-awake models. Use only when GPU capacity permits it.

Unknown policy names fail controller startup.

## Host-Memory Pressure Settings

| Field | Default | Description |
|---|---:|---|
| `cpu_memory_reclaim_available_ratio` | `null` | Enter pressure handling below this `MemAvailable / MemTotal` ratio. |
| `cpu_memory_recovery_available_ratio` | `null` | Leave pressure handling at or above this ratio. |
| `cpu_memory_reclaim_available_bytes` | `0` | Absolute reclaim threshold in bytes. |
| `cpu_memory_recovery_available_bytes` | `0` | Absolute recovery threshold in bytes. |
| `cpu_memory_poll_interval_s` | `0.5` | `/proc/meminfo` polling interval. |
| `cpu_memory_pressure_consecutive_samples` | `3` | Consecutive low samples required before reclaiming. |
| `cpu_memory_reclaim_cooldown_s` | `2.0` | Minimum interval between reclaim rounds. |

Ratio thresholds must be configured as a pair, and recovery must be greater than or
equal to reclaim. The same ordering applies to byte thresholds. When ratios and bytes
are both configured, the controller uses the more conservative threshold:

```text
low  = max(MemTotal * reclaim_ratio, reclaim_bytes)
high = max(MemTotal * recovery_ratio, recovery_bytes)
```

The pressure monitor is enabled when a reclaim ratio or a positive reclaim byte value is
configured.

## CPU Backup Policy Settings

| Field | Default | Description |
|---|---:|---|
| `cpu_backup_global_cap_bytes` | `null` | Optional hard cap used as a safety/debug guard. |
| `cpu_backup_default_model_priority` | `0` | Priority for models without an explicit entry. |
| `cpu_backup_model_priorities` | `{}` | Per-model priorities; lower values are reclaimed first. |

The primary policy is live host-memory pressure. The hard cap is optional and cannot
force vLLM to release restore-required or copy-in-flight storage. See the
[CPU backup protocol](cpu_backup_coordinator.md) for invariants.

## Local and Archived Files

- Commit reusable templates as `configs/*.example.yaml`.
- Keep machine-specific values in ignored `configs/*.local.yaml` files.
- Keep reusable workloads under `configs/workloads/`.
- Keep historical experiment-only configuration under `configs/archive/`.
- Never commit tokens in `env`; use process-level secret injection for real deployments.
