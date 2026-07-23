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

## 仓库结构

```text
controller/                  运行时代码
  backup_pool.py             CPU backup 聚合记账与 release obligation
  memory_pressure.py         MemAvailable 双水位策略
  router.py                  OpenAI proxy 与管理 API
  state.py                   模型状态、reservation 与 in-flight drain
  vllm_client.py             backend 管理及代理 client
benchmarks/                  controller workload 与结果分析
configs/
  models.example.yaml        最小模型池模板
  models.request_switch.example.yaml
                              request-driven + coordinator 模板
  workloads/                 当前 workload 模板
  archive/                   历史实验专用配置
scripts/                     启停、smoke、GPU 采样和回收验证
tests/                       单元、路由、生命周期和压力策略测试
docs/
  architecture.md            当前控制面结构和跨仓库边界
  operations.md              当前运行与验证指南
  cpu_backup_coordinator.md  CPU backup 聚合协议
  archive/                   历史计划和实验报告
results/
  exp_001/                   历史首阶段 curated 结果
```

本地运行产生的 PID、日志、机器路径配置和临时验证数据放在 ignored 的
`results/tmp/`、`tmp/` 或 `configs/*.local.yaml`，不作为代码结构的一部分。

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

- 当前架构与跨仓库边界：`docs/architecture.md`
- 当前运行指南：`docs/operations.md`
- coordinator 协议：`docs/cpu_backup_coordinator.md`
- 历史计划与实验：`docs/archive/`
