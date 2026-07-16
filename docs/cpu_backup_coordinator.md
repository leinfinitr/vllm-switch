# CPU Backup Coordinator

本文档描述 vLLM pinned CPU backup 的外部协调与系统内存压力回收机制。

## 目标

CPU backup 被视为机会式、应用可回收缓存：

- 内存充足时保留 pinned backup，后续 sleep 可跳过 D2H；
- 系统 `MemAvailable` 低于安全水位时，controller 请求 vLLM 释放可回收 backup；
- vLLM 本地决定释放哪些 tensor，并保证不破坏 sleep/wake correctness；
- controller 不拥有 pinned memory，也不保存 per-tensor `backup_id` 或状态。

## 架构

```text
vLLM 进程
  分配和持有 pinned CPU tensor
  执行 D2H/H2D
  维护 per-tensor correctness 状态
  上报 per-client aggregate usage
  接收 target_free_bytes 并释放安全 tensor

controller
  读取 /proc/meminfo 的 MemTotal/MemAvailable
  维护双水位 pressure state
  汇总每个 client/model 的 usage
  按模型优先级下发 bytes-based release request
```

## Aggregate usage

vLLM 上报：

```json
{
  "client_id": "run:qwen2p5_1p5b",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "qwen2p5_1p5b",
  "total_bytes": 3250585600,
  "required_for_restore_bytes": 0,
  "cache_only_bytes": 3250585600,
  "invalid_bytes": 0,
  "free_local_bytes": 0
}
```

controller 只知道聚合字节数，不知道具体 tensor。可回收量为：

```text
evictable_bytes = cache_only_bytes + invalid_bytes + free_local_bytes
```

vLLM 本地释放顺序：

```text
FREE_LOCAL -> INVALID -> CACHE_ONLY
```

以下状态不能释放：

```text
REQUIRED_FOR_RESTORE
COPYING_D2H
RESTORING_H2D
```

## HTTP API

所有端点位于 `/admin/cpu-backup`：

- `POST /register`：注册或刷新 client；
- `POST /usage`：上报 aggregate usage；
- `GET /release-requests/{client_id}`：返回并消费 `target_free_bytes`；
- `POST /release`：管理员手动请求某个 client 释放 bytes；
- `POST /events`：批量 `register`/`usage` 兼容入口；
- `GET /stats`：返回 usage、pending request 和 memory pressure 状态。

release response：

```json
{
  "ok": true,
  "target_free_bytes": 3250585600
}
```

释放完成后不逐 tensor ack。vLLM 重新上报 usage，controller 根据 `total_bytes` 的实际下降量确认释放进度。

## MemAvailable 双水位策略

不要使用 `MemTotal - MemFree` 作为压力指标，因为 Linux page cache 可被内核回收。当前实现读取：

```text
/proc/meminfo: MemTotal, MemAvailable
```

水位为 ratio 与 absolute bytes 的较大值：

```text
low  = max(MemTotal * reclaim_ratio, reclaim_bytes)
high = max(MemTotal * recovery_ratio, recovery_bytes)
```

状态机：

```text
NORMAL
  连续 N 次 MemAvailable < low
  -> RECLAIMING

RECLAIMING
  MemAvailable >= high
  -> NORMAL
```

进入 `RECLAIMING` 后：

```text
target_release = max(high - MemAvailable, 0)
additional_request = max(target_release - pending_release_bytes, 0)
```

controller 按以下顺序分配请求：

1. 模型优先级更低；
2. `updated_at` 更早；
3. 可回收 footprint 更大。

`cpu_backup_global_cap_bytes` 仍保留为可选 hard guard，但动态压力策略不依赖它。

## 配置

```yaml
controller:
  cpu_memory_reclaim_available_ratio: 0.15
  cpu_memory_recovery_available_ratio: 0.20
  cpu_memory_reclaim_available_bytes: 8589934592       # 8 GiB
  cpu_memory_recovery_available_bytes: 12884901888     # 12 GiB
  cpu_memory_poll_interval_s: 0.5
  cpu_memory_pressure_consecutive_samples: 3
  cpu_memory_reclaim_cooldown_s: 2.0

  # 可选 hard guard
  cpu_backup_global_cap_bytes: null

  cpu_backup_default_model_priority: 0
  cpu_backup_model_priorities:
    cold-model: 0
    hot-model: 10
```

如果 ratio 和 absolute bytes 同时配置，采用更保守（更高）的水位。

## 物理内存回收

仅删除 `torch.empty(..., pin_memory=True)` tensor 引用不足以向 OS 归还内存：PyTorch host caching allocator 会继续保留 pinned block。

vLLM 在 daemon-driven reclaim 实际释放 tensor 后调用：

```python
torch._C._host_emptyCache()
```

这只发生在外部回收路径，不影响正常 backup reuse。若当前 PyTorch 缺少该内部 API或调用失败：

- sleep/wake correctness 不受影响；
- 记录 `cpu_backup_host_cache_flush_errors`；
- 物理内存可能仍被 PyTorch cache 保留。

相关 profile fields：

```text
cpu_backup_host_cache_flush_count
cpu_backup_host_cache_flush_errors
cpu_backup_eviction_released_bytes
```

## 真实验证（2026-07-16）

使用 `bench_vllm_repeated_sleep_l1.py`、Qwen2.5-1.5B、两轮 sleep/wake。

### 未 flush PyTorch host cache

```text
logical released bytes: 3,250,585,600
worker RSS delta:       +327,680 bytes
MemAvailable delta:     +5,013,504 bytes
```

说明逻辑 accounting 下降，但物理内存未归还。

### 加入 host cache flush

```text
logical released bytes: 3,250,585,600
worker RSS delta:       -3,911,061,504 bytes
MemAvailable delta:     +4,015,022,080 bytes
flush errors:           0
```

### 动态压力策略，固定 cap 为 null

使用 90%/95% 测试水位稳定触发 pressure path：

```text
memory_pressure.state:  reclaiming
global_cap_bytes:       null
requested/released:     3,250,585,600 bytes
worker RSS delta:       -3,944,521,728 bytes
MemAvailable delta:     +4,040,409,088 bytes
```

### 无压力对照

使用 5%/10% 水位：

```text
memory_pressure.state: normal
requested/released:    0
reuse bytes:           3,250,585,600
D2H time:              0
```

因此实现满足：内存充足时保留并复用；出现压力时主动释放且物理内存返回 OS。

## 当前限制

- 第一版只监控 controller 所在主机；多机部署需要 host agent 或按 host 分组；
- 尚未读取 cgroup v2 `memory.current/memory.max/memory.pressure`；
- 尚未使用 PSI 作为辅助压力信号；
- 当全部 backup 为 `REQUIRED_FOR_RESTORE` 时，controller 只能报告 `unresolved_pressure_bytes`，不能破坏 correctness。
