# 第一阶段使用指南

> 当前配置与 CPU backup 协议以本指南和 `docs/cpu_backup_coordinator.md` 为准；`implementation_plan.md` 仅为历史归档。

本文档记录如何复现第一阶段 vLLM 外部模型切换控制器实验。

## 1. 安装依赖

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv sync --dev
```

## 2. 配置模型

复制示例配置，并编辑模型名称、端口以及可选的启动命令：

```bash
cp configs/models.example.yaml configs/models.yaml
```

最小配置假定 vLLM 后端已经在运行：

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

当 vLLM 进程启用 `VLLM_CPU_BACKUP_COORDINATOR=daemon` 时，可以添加可选的 CPU backup 协调器配置：

```yaml
controller:
  cpu_memory_reclaim_available_ratio: 0.15
  cpu_memory_recovery_available_ratio: 0.20
  cpu_memory_reclaim_available_bytes: 8589934592
  cpu_memory_recovery_available_bytes: 12884901888
  cpu_memory_poll_interval_s: 0.5
  cpu_memory_pressure_consecutive_samples: 3
  cpu_memory_reclaim_cooldown_s: 2.0

  # Optional hard guard; null keeps live pressure as the primary policy.
  cpu_backup_global_cap_bytes: null
  cpu_backup_default_model_priority: 0
  cpu_backup_model_priorities:
    qwen-0.5b: 0
    qwen-1.5b: 10
```

这些设置不会把 pinned memory 移入控制器。vLLM 汇报 process-local aggregate bytes，controller 根据 `MemAvailable` 双水位与优先级发送累计 byte targets；具体 tensor 与 required/in-flight 安全性仍由 vLLM 决定。详情见 `docs/cpu_backup_coordinator.md`。

如果希望 `scripts/launch_vllm_pool.py` 负责启动进程，请为每个模型添加 `launch_command`，例如：

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

启动器默认设置 `VLLM_SERVER_DEV_MODE=1`，因为 vLLM 的 sleep/wake HTTP 端点属于开发管理端点。

## 3. 启动 vLLM 池

```bash
uv run python scripts/launch_vllm_pool.py --config configs/models.yaml --pid-file pids.json
```

启动器会顺序启动或探测每个后端，让非启动模型进入睡眠，然后唤醒 `startup_awake_model`。

停止已启动的进程：

```bash
uv run python scripts/stop_vllm_pool.py --pid-file pids.json
```

## 4. 启动控制器

```bash
uv run python -m controller.main --config configs/models.yaml
```

检查状态：

```bash
curl http://127.0.0.1:9000/admin/state
curl http://127.0.0.1:9000/v1/models
```

手动切换：

```bash
curl -X POST http://127.0.0.1:9000/admin/switch/qwen-1.5b
```

## 5. 运行 A/B 交替工作负载

```bash
uv run python benchmarks/run_workload.py \
  --config configs/workloads/ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/ab_alternating_client.jsonl
```

控制器还会将详细切换指标记录到 `results/controller_events.jsonl`。

可以分析任一文件：

```bash
uv run python benchmarks/analyze_results.py \
  --input results/controller_events.jsonl \
  --output results/controller_summary.json
```

## 6. 运行期间采集 GPU 指标

在另一个终端中运行：

```bash
uv run python scripts/collect_gpu_metrics.py \
  --output results/gpu_metrics.csv \
  --interval-s 1 \
  --duration-s 300
```

## 7. 验证命令

```bash
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```

## 8. 第一阶段预期输出

- `results/controller_events.jsonl`：每个控制器请求的记录，包含 sleep/wake/switch/backend TTFT/E2E 延迟。
- `results/ab_alternating_client.jsonl`：客户端观测到的请求指标。
- `results/controller_summary.json`：汇总统计，包括 mean、p50、p95、p99、min、max。
- `results/gpu_metrics.csv`：采样得到的 GPU 显存和利用率时间线。
