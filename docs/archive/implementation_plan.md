# vLLM 外部模型切换控制器实现计划（历史归档）

> 本文记录早期 implementation plan，其中 per-backup record、固定 cap 和 eviction 命名已被 aggregate usage、动态 pressure 与 bytes release 协议取代。当前行为以仓库 `README.md`、[`../operations.md`](../operations.md) 和 [`../cpu_backup_coordinator.md`](../cpu_backup_coordinator.md) 为准。

**目标：** 为多模型 vLLM Sleep Mode 实验构建第一阶段外部控制器。

**架构：** 多个单模型 vLLM 服务进程由一个 FastAPI 控制器管理。控制器暴露兼容 OpenAI 的端点，用锁串行化模型切换，调用 vLLM `/sleep` 和 `/wake_up`，代理请求，并记录每个请求的指标。

**技术栈：** Python、FastAPI、httpx、pydantic、pytest、uv、vLLM HTTP API。

## 第一阶段范围

- 配置驱动的模型池。
- 始终睡眠上一个模型的策略。
- 控制器状态机。
- vLLM 管理客户端。
- 兼容 OpenAI 的 `/v1/models`、`/v1/chat/completions`、`/v1/completions`。
- 流式代理和 TTFT 指标。
- vLLM 池启动/停止辅助脚本。
- 面向第一阶段 A/B 工作负载的运行器和分析器。
- 覆盖核心行为和模拟路由路径的测试。

## CPU backup 协调器扩展

控制器还包含一条实验性的、仅记录元数据的协调路径，用于协调 vLLM pinned CPU backup 池。该能力是在第一阶段切换控制器之后添加的，用于在不把 pinned memory 所有权移出 vLLM 的前提下，支持本地 backup 保留的协调策略。

目前已实现的范围：

1. `/admin/cpu-backup/*` 下的元数据协调器 API。
2. 客户端和 backup 记录，并包含 `required_for_restore`、`cache_only`、`invalid`、`released` 等状态。
3. 全局 backup 字节数上限，以及由守护进程入队的驱逐请求。
4. 安全的自动驱逐，仅限 `cache_only` 和 `free_local` 记录。
5. 基于模型优先级的驱逐策略：低优先级模型先于高优先级模型被驱逐；LRU 和 backup 大小用于打破平局。

该设计刻意将数据平面保留在 vLLM 内。pinned CPU backup 张量以及所有 D2H/H2D 拷贝都由 vLLM 拥有；控制器只负责记账和策略。

## 明确的非目标

- 单进程多模型 vLLM 内部机制。
- 部分驱逐或懒加载。
- 生产级认证。
- 多 GPU 调度。

## 验证

- 单元测试通过 `uv run pytest`。
- 代码检查通过 `uv run ruff check .`。
- 手动 mock server 路径已有文档说明。
- 按实现阶段保留 Git 提交。
- CPU backup 协调器专项测试位于 `tests/test_backup_pool.py`。
- CPU backup API、基于上限的驱逐、released 记账以及基于模型优先级的受害者排序记录在 `docs/cpu_backup_coordinator.md` 中。
