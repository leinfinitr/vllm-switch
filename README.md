# vLLM Model Switch Controller

这是一个位于多个单模型 vLLM backend 前的外部控制器，用于研究模型切换、Sleep Mode 与主机 pinned backup 回收。

## 当前职责

1. 暴露兼容 OpenAI 的 `/v1/models`、`/v1/chat/completions` 和 `/v1/completions`。
2. 根据请求中的逻辑 model alias 选择 backend，并在转发前重写为 backend 的 `served_model_name`。
3. 在切换前等待旧模型的 in-flight 请求完成，然后 sleep 旧模型、wake 目标模型。
4. 传播 backend 的真实 HTTP status 和 end-to-end headers/stream，避免把上游错误伪装成 200。
5. 记录切换、TTFT 和 E2E JSONL metrics。
6. 作为 metadata-only CPU backup coordinator，基于 host pressure 协作回收 vLLM process-local pinned backup。

controller 不分配 pinned tensor、不执行 D2H/H2D，也不镜像 per-tensor state；这些 correctness 决策由 vLLM allocator 持有。

## 代码结构

- `controller/config.py`：模型池、切换和 memory-pressure 配置。
- `controller/policies.py`：切换策略。
- `controller/state.py`：模型状态、request reservation 与 in-flight drain。
- `controller/vllm_client.py`：不继承环境 proxy 的 backend 管理/代理 client。
- `controller/router.py`：OpenAI proxy 和 `/admin/cpu-backup/*` control API。
- `controller/backup_pool.py`：per-client aggregate accounting、priority 和 outstanding release obligation。
- `controller/memory_pressure.py`：Linux `MemAvailable` 双水位、debounce、cooldown 与 unresolved pressure telemetry。
- `scripts/launch_vllm_pool.py`、`scripts/stop_vllm_pool.py`：顺序启动和停止预配置 backend。
- `benchmarks/`：controller workload 与结果分析。

## CPU backup 协议

vLLM 汇报：

```text
required_for_restore_bytes
+ cache_only_bytes
+ invalid_bytes
+ free_local_bytes
= total_bytes
```

controller 只发送 byte target。release GET 返回 controller epoch 和单调累计 command counter，vLLM 只执行未观察到的 delta，因此 HTTP 响应丢失可安全重试。vLLM 另上报单调 `released_bytes_total`，只在 allocator storage 实际下降时确认 pending；`cache_only ↔ required` 状态转换不算释放。

主策略是 `MemAvailable` low/high watermark hysteresis；`cpu_backup_global_cap_bytes` 只是可选 hard guard。具体 tensor 选择和 required/copying/restoring 保护始终由 vLLM 决定。

完整 API、invariants、failure semantics、telemetry 和 limitations：`docs/cpu_backup_coordinator.md`。

## 快速开始

```bash
uv sync --dev
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```

准备配置并启动：

```bash
cp configs/models.example.yaml configs/models.local.yaml
$EDITOR configs/models.local.yaml
uv run python scripts/launch_vllm_pool.py --config configs/models.local.yaml
uv run python -m controller.main --config configs/models.local.yaml
```

controller 管理的是已配置 backend；若配置 `launch_command`，辅助脚本负责启动并等待 health。内部 localhost/private HTTP 明确忽略环境 proxy。

真实双模型 request-driven smoke：

```bash
uv run python scripts/smoke_openai_switch.py \
  --base-url http://127.0.0.1:9000 \
  --models qwen-1.5b qwen-3b \
  --output results/tmp/request-switch/smoke.jsonl
```

该 client 只向 `/v1/chat/completions` 发送推理请求；切换完全由请求中的 `model` 字段触发。结束时只读 `/admin/state`，检查 reservation 已清零。

## 文档

- 当前 coordinator 设计：`docs/cpu_backup_coordinator.md`
- 使用说明：`docs/first_stage_usage.md`
- 历史实施计划：`docs/implementation_plan.md`（仅用于追溯，不作为当前 API）
