# CPU Backup Coordinator

本文档描述 controller 对 vLLM process-local pinned CPU backup 的聚合控制与主机内存压力策略。数据平面和 correctness 状态始终由 vLLM 持有；controller 只下发字节目标。

## 1. 设计边界

```text
vLLM worker                             controller
-----------                             ----------
持有 pinned tensor                      不持有 tensor/backup_id
执行 D2H/H2D                            汇总 per-client/model bytes
维护 tensor state/validity              读取主机 MemAvailable
选择具体释放对象                         下发 target_free_bytes
```

CPU backup 是机会式、应用可回收缓存：内存充足时保留以跳过后续 D2H；压力出现时释放，下一次 sleep 按需重建。exact disk backup 的文件、有效性和 restore 仍归 vLLM 数据平面所有。controller 只有在 worker 明确上报 required RAM 已有 current/reserved exact disk source 时才把相应 bytes 加入 byte-target reclaim；仅配置 disk 目录或仅有 reserved telemetry 不足以推断可释放。

## 2. 聚合协议与 invariant

vLLM 使用以下端点：

- `POST /admin/cpu-backup/register`
- `POST /admin/cpu-backup/usage`
- `GET /admin/cpu-backup/release-requests/{client_id}`
- `GET /admin/cpu-backup/stats`

管理员可通过 `POST /admin/cpu-backup/release` 为一个 client 请求释放字节。

usage schema：

```json
{
  "client_id": "run:model-a",
  "pid": 12345,
  "engine": "vllm",
  "model_id": "model-a",
  "total_bytes": 3250585600,
  "released_bytes_total": 0,
  "required_for_restore_bytes": 0,
  "cache_only_bytes": 3250585600,
  "invalid_bytes": 0,
  "free_local_bytes": 0,
  "disk_backup_current_bytes": 0,
  "disk_backup_reserved_bytes": 3250585600,
  "ram_reclaimable_with_disk_bytes": 0
}
```

controller 对每次上报强制：

```text
total_bytes == required_for_restore_bytes
             + cache_only_bytes
             + invalid_bytes
             + free_local_bytes
```

可释放量为后三类之和。vLLM 本地释放顺序是：

```text
FREE_LOCAL -> INVALID -> CACHE_ONLY
```

disk-aware 协议另有：

```text
0 <= ram_reclaimable_with_disk_bytes <= required_for_restore_bytes
ram_reclaimable_with_disk_bytes > 0 requires current_bytes > 0
                                      or reserved_bytes > 0

ram_reclaimable_without_disk_bytes = cache_only_bytes
                                     + invalid_bytes
                                     + free_local_bytes

evictable_bytes = ram_reclaimable_without_disk_bytes
                  + ram_reclaimable_with_disk_bytes
```

`disk_backup_current_bytes` 与 `disk_backup_reserved_bytes` 是独立 disk telemetry，不加入 `total_bytes`（它只表示 RAM backup footprint）。current 表示当前 exact restore source 的逻辑内容；reserved 表示 worker 的 disk storage footprint。两者均不能替代 worker 对 `ram_reclaimable_with_disk_bytes` 的显式安全判断。controller 仍只发一个 cumulative byte target；worker 决定先释放普通 cache RAM还是有 exact disk source 的 required RAM，并通过既有 `released_bytes_total` ack。pending obligation、priority/age/size 顺序和 pressure hysteresis 均不改变。

`COPYING_D2H`、`RESTORING_H2D` 始终计入 non-evictable bytes。`REQUIRED_FOR_RESTORE` 默认同样不可释放；只有 worker 已验证 exact disk restore source 并把对应 RAM 计入 `ram_reclaimable_with_disk_bytes` 时，才成为 cooperative reclaim candidate。

### Release acknowledgement

`GET release-requests` 返回 `request_epoch` 与单调递增的 `requested_release_bytes_total`。vLLM 只执行同一 epoch 中尚未观察到的 delta，因此 GET 是幂等的：响应丢失可安全重试，重复响应不会重复释放。controller 重启会生成新 epoch。vLLM 将配置的 client ID 视为逻辑前缀，并为每个 worker process 追加 PID + high-resolution timestamp suffix；controller 的 record key 是这个不可复用的实际 process-incarnation ID。重启后的 worker 不会继承前一进程的累计命令或 pending 状态，新旧 worker 若短暂重叠则保留两条 record；不能按逻辑前缀替换旧 record，否则会低估仍存活进程的 pinned memory。

command 不等于完成。vLLM 只在 process-local pool 的 `reserved_bytes` 实际下降时累加 `released_bytes_total`；controller 用该单调 counter 的 delta 确认进度：

```text
released = new_released_total - old_released_total
pending  = max(pending - released, 0)
```

累计 acknowledgement 可跨过 latest-wins usage coalescing：即使释放后立刻重新分配、最终 `total_bytes` 与旧 snapshot 相同，真实 release 仍不会丢失。旧 client 未发送该字段时仍可用观测到的 footprint drop 兼容确认。该 counter 证明 allocator storage drop，不代表内存已归还 OS；physical reclaim 仍需结合 host-cache flush telemetry、worker RSS 和 `MemAvailable`。

`cache_only -> required_for_restore` 等状态转换不能取消 pending。否则相同存储再次变为 cache-only 后，policy 会重复下发同一批 bytes。暂时无法满足的请求保留为 outstanding obligation。

## 3. 主机内存压力策略

当前信号来自 controller 所在 Linux host：

```text
/proc/meminfo: MemTotal, MemAvailable
```

`MemAvailable` 比 `MemFree` 更合适，因为它包含内核预计可无交换回收的 page cache。ratio 与绝对 bytes 同时配置时取更保守的较高水位：

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

在 reclaiming 中：

```text
target_release   = max(high - MemAvailable, 0)
additional_bytes = max(target_release - pending_release_bytes, 0)
```

连续样本抵抗短时噪声，双水位防止抖动，cooldown 限制 command 频率。已有 pending 会从 target 中扣除，防止过度回收。

### Victim order

跨 client 分配顺序为：

1. model priority 较低；
2. usage `updated_at` 较早；
3. 剩余 evictable footprint 较大。

该顺序只决定 client 的 byte budget，具体 tensor 仍由 vLLM 选择。

### Optional hard cap

`cpu_backup_global_cap_bytes` 是可选 safety/debug guard，不是主策略。cap 作用于总 backup：

```text
over_cap = max(total_bytes - cap - pending_release_bytes, 0)
```

实际 request 受 evictable bytes 限制。当 required bytes 已超过 cap 时，controller 请求全部可释放内容，但不会破坏 required backup；剩余超额会继续显示在 `over_cap_bytes`。

## 4. 模型切换并发语义

`switch_lock` 串行化模型状态迁移。会 sleep 旧模型的 policy 必须先等待该模型所有 in-flight 请求完成。

OpenAI proxy 在同一 `switch_lock` 临界区内完成：

1. 使目标模型 ready；
2. 注册该请求的 active reservation；
3. 释放 lock 后转发到 backend。

因此不存在“模型 ready 后、请求计数增加前”被另一个切换 sleep 的窗口。streaming 请求一直持有 reservation，直到 upstream body 完成或断开。

backend sleep/wake 失败、取消或其他异常时模型进入 `ERROR`；若它原是 active model，同时清除 `active_model`，避免状态快照自相矛盾。管理端 sleep/wake 只有 HTTP 2xx 表示成功，redirect 不会被误记为状态转换完成。proxy 保留 backend HTTP status 和 end-to-end headers（过滤 hop-by-hop headers），`served_model_name` 与外部 route alias 不同时会在转发前重写。

proxy 只在目标模型 ready 后、仍持有 switch lock 时创建 active-request reservation，避免 readiness 与 reservation 之间的 sleep race。reservation enter 与 exit 都运行在独立 task 中：caller cancellation 不会中断半完成的 enter；enter 成功后会先完成 exactly-once exit，再传播一次或重复 cancellation。JSON 与 streaming path 的所有调用者反复 shield 同一 cached teardown。streaming ownership 在 await upstream setup 之前单向移交，context factory也位于统一 cleanup boundary内，因此 downstream 在 body iterator 启动前断开、factory/setup/send 失败或 iterator 被取消均不会形成 double-exit/漏 exit 窗口。request metrics 写入是 best-effort observability；本地 JSONL 写失败会记录 controller error log，但不会覆盖 proxy/backend 的主要 HTTP、stream 或 cancellation 结果。

## 5. 配置

```yaml
controller:
  cpu_memory_reclaim_available_ratio: 0.15
  cpu_memory_recovery_available_ratio: 0.20
  cpu_memory_reclaim_available_bytes: 8589934592
  cpu_memory_recovery_available_bytes: 12884901888
  cpu_memory_poll_interval_s: 0.5
  cpu_memory_pressure_consecutive_samples: 3
  cpu_memory_reclaim_cooldown_s: 2.0

  cpu_backup_global_cap_bytes: null
  cpu_backup_default_model_priority: 0
  cpu_backup_model_priorities:
    cold-model: 0
    hot-model: 10
```

reclaim/recovery ratio 必须成对出现，recovery 不得低于 reclaim；bytes 水位同理。

通过 `scripts.launch_vllm_pool.py` 启动时，每个未显式覆盖的 model env 默认获得：

```text
VLLM_CPU_BACKUP_DISK_DIR=/home/ljl/research-systems/vllm-model-switch-controller/tmp
```

该目录已由仓库 `.gitignore` 排除。生产或多盘实验应在 model `env` 中显式覆盖，并确保多个 worker 的文件命名和 ownership 由 vLLM 正确隔离。

## 6. 可观测性与物理回收

`GET /admin/cpu-backup/stats` 返回：

- 全局 `total/required/cache_only/invalid/free_local/evictable/pending`；
- 全局和 per-client `disk_backup_current_bytes`、`disk_backup_reserved_bytes`、`ram_reclaimable_without_disk_bytes`、`ram_reclaimable_with_disk_bytes`，以及全局 `disk_backup_client_count`；
- 每个 client 的同类 accounting；
- 尚未被 client 消费的 release commands；
- memory-pressure state、水位、连续样本、target、unresolved bytes 和 probe errors。

vLLM profile 关键字段：

```text
cpu_backup_release_bytes
cpu_backup_host_cache_flush_count
cpu_backup_host_cache_flush_errors
cpu_backup_coordinator_request_errors
```

这些 counter 是累计值，benchmark 应计算 step delta。`total_bytes` 下降只证明 application-level logical release；物理回收还需观测 worker RSS 和 host `MemAvailable`。

PyTorch 默认可能把已删除 pinned tensor 留在 host caching allocator。vLLM 外部回收路径 best-effort 调用私有、进程级 `torch._C._host_emptyCache()`。调用失败不影响 sleep/wake correctness，但 RSS 可能不下降，必须通过 flush error 和 OS 指标报告，不能宣称物理回收成功。

## 7. 验证方法

论文级验证至少包含两组对照：

1. pressure：触发 release，要求 positive release delta、flush 无错误、worker RSS 显著下降、`MemAvailable` 恢复；
2. no-pressure：controller 保持 normal、release delta 为零、后续 sleep reuse 为正且 D2H 为零。

提高测试水位可在共享机器上安全触发策略，不应通过耗尽系统 RAM 验证。benchmark 输出必须记录参数、代码版本、模型列表、host/GPU 信息和自动 assertion 结果。

## 8. 已知限制

- 当前 `/proc/meminfo` 是 host-global 信号；多机部署需要 per-host controller/agent。
- controller 当前没有 worker lease/heartbeat。异常退出的 worker record 会保守地留在 accounting 中，可能造成过度回收请求，但不会低估已知 pinned usage；生产化需要显式 liveness/lease，而不能按静默时长猜测删除。
- 尚未读取 cgroup v2 `memory.current/memory.max/memory.events/memory.pressure`。
- 尚未使用 PSI 或 NUMA locality。
- policy 无法释放 in-flight 或没有 worker-reported exact disk source 的 required backup；`unresolved_pressure_bytes` 会保留该缺口。
- controller 没有独立验证 host-cache flush 是否物理成功，只能结合 worker/OS telemetry 判断。
