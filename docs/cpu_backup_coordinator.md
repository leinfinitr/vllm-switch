# CPU Backup Coordinator

本文档描述为 vLLM pinned CPU backup pool coordinator 添加的控制器侧守护进程支持。

## 架构

Coordinator 是进程本地 vLLM CPU backup pool 的仅元数据协调器。

```text
vLLM 进程
  本地分配 pinned CPU backup 张量
  本地执行 D2H/H2D
  向控制器上报元数据
  轮询控制器以获取驱逐请求

控制器守护进程
  跟踪客户端和 backup 记录
  进行全局字节数记账
  在内存压力下加入驱逐请求
  选择受害者时应用模型优先级策略
```

控制器永远不拥有 vLLM 的 pinned memory，也不执行 CUDA 拷贝。这样可以避免跨进程 pinned memory 共享、CUDA context 所有权以及 shared-memory 注册问题。

## Backup 状态

控制器为每个 vLLM backup id 跟踪一条 `BackupRecord`。

重要状态：

- `allocated`：vLLM 已将本地 backup memory 关联到一次分配。
- `required_for_restore`：GPU 权重已经取消映射；CPU backup 是唤醒模型所必需的。控制器不能自动驱逐该状态。
- `cache_only`：GPU 权重仍然存在；CPU backup 只用于改进下一次 sleep，可被驱逐。
- `invalid`：backup 内容已经过期。当前自动容量上限策略不会选择该状态，因为它可能来自模型睡眠期间的保守失效处理。
- `free_local`：本地 free-list memory，未来策略可能释放它。
- `released`：vLLM 报告该 backup 不再被持有；控制器会将其从活跃元数据和记账中移除。

安全不变式是：

```text
required_for_restore backup 永远不会被自动驱逐。
```

## HTTP API

所有端点都位于 `/admin/cpu-backup` 下。

### `POST /register`

注册或刷新一个 vLLM 客户端。

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "qwen2p5_0p5b",
  "gpu_uuid": null,
  "metadata": {"hostname": "node-a"}
}
```

### `POST /allocated`

记录一次本地 vLLM backup 分配。

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "backup_id": "123:0:...:weights",
  "size_bytes": 1048576000,
  "tag": "weights",
  "model_id": "qwen2p5_0p5b",
  "engine": "vllm",
  "pinned": true,
  "generation": 0,
  "metadata": {"pool_hit": false}
}
```

### `POST /state`

更新 backup 状态。

```json
{
  "backup_id": "123:0:...:weights",
  "state": "cache_only",
  "valid": true,
  "generation": 0
}
```

### `POST /invalidate`

将匹配的 backup 标记为 invalid。

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "model_id": "qwen2p5_0p5b",
  "tag": "weights",
  "generation": 1,
  "reason": "allocator_invalidation"
}
```

### `POST /released`

在 vLLM 释放 backup 后，将一条 backup 从活跃元数据和记账中移除。

```json
{"backup_id": "123:0:...:weights"}
```

### `POST /evict`

为某个客户端手动加入驱逐请求。

```json
{
  "client_id": "phase-run:qwen2p5_0p5b",
  "backup_ids": ["123:0:...:weights"],
  "reason": "manual"
}
```

### `GET /evictions/{client_id}`

轮询并清空某个客户端的待处理驱逐请求。vLLM 会在安全点调用该端点，并且只释放它仍然拥有的本地 cache-only backup。

响应：

```json
{
  "ok": true,
  "backup_ids": ["123:0:...:weights"]
}
```

### `POST /events`

vLLM HTTP 协调器使用的批量端点。它接收一组事件，事件的 `type` 可以是 `register`、`allocated`、`state`、`invalidate` 或 `released`。处理完一批事件后，控制器会评估容量上限压力，并可能加入新的驱逐请求。

### `GET /stats`

返回控制器记账信息和活跃元数据。

重要字段：

```json
{
  "stats": {
    "client_count": 2,
    "backup_count": 117,
    "total_bytes": 4299161600,
    "global_cap_bytes": 1,
    "over_cap_bytes": 4299161599,
    "required_for_restore_bytes": 4299161600,
    "evictable_bytes": 0,
    "default_model_priority": 0,
    "model_priorities": {
      "qwen2p5_1p5b": 10
    },
    "pending_eviction_count": 0
  }
}
```

## 配置

在 YAML 配置的 `controller` 下添加 CPU backup 协调器设置。

```yaml
controller:
  host: 127.0.0.1
  port: 19090
  metrics_path: results/controller_events.jsonl

  # 可选。如果未设置，协调器只记录元数据，
  # 不会自动加入由容量上限触发的驱逐请求。
  cpu_backup_global_cap_bytes: 4294967296

  # 可选。数值越高表示“保留越久”。未配置的模型使用默认值。
  cpu_backup_default_model_priority: 0
  cpu_backup_model_priorities:
    qwen2p5_0p5b: 0
    qwen2p5_1p5b: 10
```

## 驱逐策略

当设置了 `cpu_backup_global_cap_bytes` 且活跃元数据字节数超过上限时，控制器只会从安全状态中选择驱逐受害者：

```text
cache_only
free_local
```

它不会自动选择：

```text
required_for_restore
invalid
```

Phase 3 模型优先级策略的受害者排序为：

```text
先选择模型优先级更低者
然后选择 updated_at 更早者（同优先级内 LRU）
然后选择 backup 更大者
```

优先级语义：

- 更高的整数优先级表示该模型的 backup 应保留更久。
- 更低的整数优先级表示它的驱逐成本更低。
- 未配置的模型使用 `cpu_backup_default_model_priority`。

示例：

```yaml
cpu_backup_default_model_priority: 0
cpu_backup_model_priorities:
  hot-model: 10
  cold-model: 0
```

如果 `hot-model` 和 `cold-model` 都有 cache-only backup，容量上限压力会先驱逐 `cold-model`，即使 `hot-model` 更旧或更大。

## vLLM 环境变量

vLLM 进程通过以下配置启用 HTTP 协调器：

```bash
export VLLM_CPU_BACKUP_COORDINATOR=daemon
export VLLM_CPU_BACKUP_COORDINATOR_URL=http://127.0.0.1:19090
export VLLM_CPU_BACKUP_COORDINATOR_TIMEOUT_S=1.0
export VLLM_CPU_BACKUP_COORDINATOR_CLIENT_ID=<unique-client-id>
export VLLM_CPU_BACKUP_COORDINATOR_MODEL_ID=<model-id>
```

如果缺少这些变量，vLLM 会使用 no-op 协调器，控制器也不会收到 CPU backup 元数据。

## 验证

控制器专项验证：

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
.venv/bin/python -m pytest tests/test_backup_pool.py -q
.venv/bin/python -m ruff check \
  controller/backup_pool.py \
  controller/config.py \
  controller/main.py \
  controller/router.py \
  controller/schemas.py \
  tests/test_backup_pool.py
```

已覆盖行为：

- 元数据注册、分配和状态 API。
- 批量事件端点。
- 基于容量上限的驱逐队列。
- `released` 事件会从活跃记账中移除字节数。
- 模型优先级策略会让高优先级 backup 优先于低优先级 backup 被保留，即使高优先级 backup 更旧或更大。

真实低上限验证已通过 `llm-switch-bench` 运行，并设置 `cpu_backup_global_cap_bytes: 1`。预期结果是 vLLM 在 wake 后释放 cache-only backup，因此下一次 sleep 会再次执行 D2H，而不是复用 clean backup。
