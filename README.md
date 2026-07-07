# vLLM Model Switch Controller

这是一个外部模型切换控制器，用于评估 vLLM Sleep Mode 作为多模型服务基线时的表现。

## 第一阶段目标

在多个单模型 vLLM 服务进程之前提供一个兼容 OpenAI 的统一端点。

1. 根据 OpenAI `model` 字段路由请求。
2. 如果目标模型不同于当前活跃模型，则根据调度策略决定何时让上一个活跃的 vLLM 服务进入睡眠。
3. 唤醒目标 vLLM 服务。
4. 转发请求，并记录切换、TTFT 和 E2E 指标。

第一阶段刻意将该项目放在 vLLM 外部实现，以便对比冷重载、专用服务、SwapServeLLM 和 ServerlessLLM 的端到端行为。

## 已实现组件

- Config 驱动的模型池：`controller/config.py`
- 模型调度策略：`controller/policies.py`
- 运行时状态机：`controller/state.py`
- vLLM 管理/代理客户端：`controller/vllm_client.py`
- 兼容 OpenAI 的 FastAPI 控制器：`controller/main.py`、`controller/router.py`
- 仅记录元数据的 CPU backup coordinator：`controller/backup_pool.py`
- JSONL 指标记录器：`controller/metrics.py`
- 顺序启动/停止 vLLM 池的辅助脚本：`scripts/launch_vllm_pool.py`、`scripts/stop_vllm_pool.py`
- GPU 指标采集辅助脚本：`scripts/collect_gpu_metrics.py`
- 工作负载运行器和分析器：`benchmarks/run_workload.py`、`benchmarks/analyze_results.py`

## CPU backup coordinator

仅记录元数据的守护进程，用于协调 vLLM 的 pinned CPU backup pool。vLLM 仍然在本地分配 pinned CPU backup tensor，并自行执行 D2H/H2D 拷贝；控制器只记录元数据、进行全局字节数记账，并在策略压力下将 cache-only 备份加入驱逐请求队列。

已实现的 CPU backup 策略能力：

- 跟踪客户端和 backup 元数据；
- 跟踪包含 `required_for_restore` 和 `cache_only` 在内的 backup 状态；
- 基于全局上限由守护进程发起驱逐请求；
- 基于模型优先级的驱逐策略，优先级更高的模型会保留更久；
- `/admin/cpu-backup/*` 下的统计和批量事件 API。

API、配置、安全不变式和验证细节见 `docs/cpu_backup_coordinator.md`。

## 快速开始

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv sync --dev
uv run python -m pytest -q
uv run ruff check .
```

另见：

- `docs/implementation_plan.md`
- `docs/first_stage_usage.md`
- `docs/cpu_backup_coordinator.md`
