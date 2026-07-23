# 模型切换控制器架构

## 范围

该进程位于多个单模型 vLLM backend 前，负责请求驱动的模型选择、
sleep/wake 生命周期串行化、OpenAI 请求代理和 CPU backup 聚合策略。它不
实现模型执行，也不拥有 CPU/GPU backup 内容。

```text
client
  -> model-switch controller (:9000)
       -> vLLM backend A (:8101)
       -> vLLM backend B (:8102)

vLLM worker -- aggregate usage/release acknowledgement --> controller
vLLM worker <-- cumulative target_free_bytes ------------ controller
```

## 运行时组件

- `controller/router.py`：OpenAI-compatible 数据面及 `/admin/*` 管理面。
- `controller/state.py`：模型状态、active request reservation、drain 和切换串行化。
- `controller/vllm_client.py`：sleep/wake/health 调用和请求代理；loopback/private traffic 不继承环境 proxy。
- `controller/policies.py`：决定目标模型 ready 后是否 sleep 旧模型。
- `controller/backup_pool.py`：只保存 per-process aggregate bytes、priority 和 release obligation。
- `controller/memory_pressure.py`：读取 host `MemAvailable`，执行 debounce、双水位和 cooldown。

## 请求切换边界

会 sleep 旧模型的策略在切换前等待旧模型所有 in-flight 请求结束。目标模型
ready 后，controller 在仍持有 `switch_lock` 时创建该请求的 reservation，随后
才释放锁并转发。因此不存在“目标模型 ready，但尚未计入请求”时被并发切换
重新 sleep 的窗口。streaming 请求持有 reservation 直到 upstream body 完成或
连接终止。

backend 生命周期异常使模型进入 `ERROR`；controller 不把不确定状态标记为
awake/sleeping。数据面保留 backend 的 HTTP status 和 end-to-end headers，并在
外部 alias 与 `served_model_name` 不同时重写请求中的 model。

## CPU backup 边界

vLLM allocator 持有 pinned tensors、有效性、D2H/H2D 和具体释放选择；controller
只接收 aggregate usage 并下发累计 byte target。controller 故障不会使 invalid
backup 变为 valid，也不能释放 `REQUIRED_FOR_RESTORE` 或 copy in-flight storage。

协议和物理回收证据要求见 [`cpu_backup_coordinator.md`](cpu_backup_coordinator.md)。

## 跨仓库关系

```text
vllm/
  allocator、eager backup、版本失效、两阶段 sleep、coordinator client

vllm-model-switch-controller/
  多 backend 生命周期、请求 drain、pressure policy、aggregate control plane

llm-switch-bench/
  独立 benchmark、raw/curated artifact、跨系统比较和报告
```

实现仓库不保存论文聚合图；benchmark 仓库不复制 allocator correctness 逻辑。
