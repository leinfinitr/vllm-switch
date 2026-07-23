# 请求驱动的多模型 vLLM 快速切换开发计划（历史归档）

> 本文记录已完成阶段的实施过程，不是当前运行指南。现行架构与命令见
> [`../../architecture.md`](../../architecture.md) 和
> [`../../operations.md`](../../operations.md)。

> **原执行说明：** Use `development-workflows` 的 TDD / pre-commit review 流程逐项实现；本计划须由用户审阅通过后才能开始执行。

**Goal:** 用户只向 controller 的 OpenAI-compatible API 发送带 `model` 的推理请求；controller 自动选择对应的长期存活 vLLM backend，安全完成 sleep/wake 后转发请求，并在主机内存宽裕时利用 vLLM process-local pinned CPU backup 加速回切。

**Architecture:** 保持“外部 controller + 每个模型一个长期存活的单模型 vLLM engine”。controller 负责逻辑 model alias 路由、请求 reservation、切换串行化和 CPU backup 的 aggregate pressure policy；vLLM 负责 tensor-local sleep/wake、D2H/H2D、clean backup reuse 与具体回收。下一阶段不新建通用调度框架，而是补齐真实环境接线、最小并发语义验证和一个独立、可冻结 workload 的 benchmark 入口。

**Tech Stack:** Python 3.11/3.12、FastAPI、httpx、vLLM Sleep Mode、CUDA/PyTorch pinned memory、pytest、ruff、JSONL/CSV。

---

## 1. 当前事实与阶段判断

### 1.1 已经具备的机制（不能重复开发）

`vllm-model-switch-controller` 当前分支 `research/pinned-backup-pool`（审阅时 commit `ef656aed`）已经具备目标的主要 request path：

```text
POST /v1/chat/completions 或 /v1/completions
  -> 读取外部 model alias
  -> switch_lock 内确保目标 model ready
  -> 在释放 switch_lock 前建立该 model 的 active-request reservation
  -> 将 model 改写为 backend served_model_name
  -> JSON 或 SSE 流式转发
  -> 上游完成/断连/取消后恰好释放一次 reservation
```

对应实现：

- `controller/router.py::handle_openai_proxy`
- `controller/router.py::ensure_model_ready_locked`
- `controller/state.py::track_request`
- `controller/state.py::wait_for_other_model_requests_to_finish`
- `controller/vllm_client.py::{sleep,wake_up,proxy_json,proxy_stream}`
- `controller/policies.py::AlwaysSleepPreviousPolicy`

因此，本阶段不是“从零把 API 转发和切换拼起来”，而是把已有路径接到**研究版 vLLM checkout**，用两个不同模型验证请求驱动的自动切换、并发 drain 和 CPU backup 回切收益。

`vllm` 当前分支 `research/pinned-backup-pool`（审阅时 commit `b2057ef6`）已经实现：

- level-1 sleep 时创建 process-local pinned CPU backup；
- pure-inference 权重不变时，wake 后保留 clean backup；
- 后续 sleep 复用 clean backup，跳过重复 D2H；
- 外部 metadata-only coordinator 汇报 aggregate bytes；
- 根据 byte target 只回收 `free_local / invalid / cache_only`，保护 restore-required/in-flight bytes；
- 释放后 flush PyTorch pinned-host cache，并上报单调 `released_bytes_total`。

`llm-switch-bench` 当前分支 `research/pinned-backup-pool-bench`（审阅时 commit `0d0b9d0f`）已经有 lifecycle、repeated sleep、ServerlessLLM、SwapServeLLM adapter 以及 OS resource 读取工具。它应成为下一阶段 benchmark 的唯一主位置；controller 内现有 `benchmarks/run_workload.py` 只保留为串行 smoke client，不继续扩展成第二套 benchmark 框架。

### 1.2 当前最重要的接线问题

controller 的 `.venv` 当前 import 的是 installed vLLM wheel：

```text
/home/ljl/research-systems/vllm-model-switch-controller/.venv/lib/python3.11/site-packages/vllm
```

这个 wheel 不含研究分支的 CPU backup pool；而 `llm-switch-bench/.venv` 会 import：

```text
/home/ljl/research-systems/vllm/vllm
```

所以下一阶段的真实 backend 必须显式使用：

```text
/home/ljl/research-systems/llm-switch-bench/.venv/bin/vllm
```

并从 `/home/ljl/research-systems/vllm` 启动。否则即使 API 自动切换成功，也无法验证本项目的 pinned backup reuse/reclaim 技术。

### 1.3 现有 launcher 的局限

`scripts/launch_vllm_pool.py` 当前行为是：启动 `startup_awake_model` 后让它保持 awake，再启动后续 engine。对于两模型不能同时驻留的目标配置，这会使后续 engine 初始化时与启动模型争用 GPU memory。

本阶段需要把顺序改成：

```text
launch A -> health A -> sleep A
launch B -> health B -> sleep B
...
wake startup model
```

只需新增 `/is_sleeping` probe 并验证 lifecycle post-condition；不扩展为生产级 reconciliation / rollback 状态机。probe 必须带短轮询 deadline，而不是在 lifecycle 2xx 后只读取一次，因为 vLLM 源码明确说明 frontend multiprocessing 下命令响应可能早于实际 transition 完成。

### 1.4 当前验证证据

只读审阅期间已经运行：

- controller：`59 passed`，ruff 全部通过；
- llm-switch-bench：`81 passed`，ruff 全部通过；
- vLLM focused pytest 被缺少 `tblib` 阻塞（不是测试失败）：`ModuleNotFoundError: tblib`。

三个仓库审阅前后均为 clean working tree。

---

## 2. 研究问题与最小成功标准

### RQ1：透明 API 路径是否正确？

只改变 OpenAI 请求的 `model` 字段，能否让 client 在不知道 backend 端口和 sleep/wake API 的情况下连续请求 A、B、B、A，并收到正确模型响应？

**成功标准：**

- `/v1/models` 列出两个逻辑 alias；
- `A -> B -> B -> A` 四个 streaming 请求全部成功；
- 只有 `A->B` 和 `B->A` 触发切换，连续 `B->B` 不切换；
- controller metrics、backend logs 和 client records 可按 request ID/model 顺序核对。

### RQ2：切换是否尊重活跃请求？

当 A 的 streaming 请求仍在生成时到达 B 请求，controller 是否等待 A 完成后才 sleep A，并随后 wake/route B？

**成功标准：**

```text
A first token
B scheduled/queued
A completion
sleep A
wake B
B first token
```

且 A 请求不中断、reservation 最终归零。这个实验只验证最简单的全局 FCFS/drain 语义，不实现抢占、优先级或取消运行中的推理。

### RQ3：CPU 余量是否能降低回切成本？

在相同两模型 trace 下，保留 clean CPU backup 与回收 backup 后的回切延迟差多少？

**成功标准：**

- 无压力时第二次及后续 sleep 的 profile 出现 `cpu_backup_reuse_count > 0` 且 `copy_d2h_s == 0`（或接近计时分辨率）；
- clean backup 的主要收益是让**后续 sleep**跳过 pinned allocation + D2H；只要 backup 仍存在，wake 仍需 H2D。因此比较的是完整切换（sleep old + wake target）和请求 TTFT，而不是声称 H2D 本身被消除；
- 压力实验中 controller 有 release request，vLLM 有 `released_bytes_total` 增长，并且 worker process-tree RSS **和** host `MemAvailable` 都按预注册阈值提供物理证据；
- backup 被回收后下一次 sleep 重新执行 pinned allocation/D2H，输出仍正确；之后的 wake 再从新 backup 执行 H2D。

### RQ4：请求驱动切换在什么 workload 上有效？

模型访问具有 locality 时，保留 CPU backup 能否把切换惩罚从 cold reload 级别降到 PCIe copy 级别，并改善 TTFT/完成时间？在细粒度交错访问中，每请求都触发切换的最坏开销有多大？

**成功标准：**

- alternating 和 burst-locality 两类冻结 trace 的结果能解释切换次数、switch miss/steady hit 和 TTFT；
- 不把本方案描述成 Prism 的等价实现；清楚指出本方案只做单 GPU temporal sharing，而 Prism 同时做 GPU memory ballooning、空间+时间共享和多 GPU placement/scheduling。

---

## 3. 最小架构与明确非目标

### 3.1 数据平面

```text
OpenAI client
  -> controller :9000
       -> alias -> ModelSpec
       -> switch_lock
          -> wait old model active requests == 0
          -> POST old /sleep?level=1
          -> GET old /is_sleeping == true
          -> POST target /wake_up
          -> GET target /is_sleeping == false
          -> reserve target request
       -> proxy /v1/chat/completions or /v1/completions
  -> one of long-lived vLLM backends :8101/:8102
```

生命周期 probe 用于真实 smoke test 的确定性，不做复杂 timeout unknown-outcome reconciliation。

### 3.2 CPU backup 控制面

```text
vLLM process-local CuMemAllocator
  -> usage(client_id, model_id, required/cache_only/invalid/free bytes)
controller BackupPoolState + MemoryPressureMonitor
  -> byte release target
vLLM
  -> locally evict only safe states
  -> flush host pinned cache
  -> released_bytes_total acknowledgement
```

controller 不跟踪 tensor、不拷贝权重、不在每个请求前主动 prepare backup。

### 3.3 本阶段非目标

- 不支持一个模型多个 replicas、多个 GPU resource groups 或 TP placement；
- 不实现 Prism 的 kvcached、KV memory ballooning、shared per-GPU queue、KVPR placement 或 slack-aware arbitration；
- 不做 distributed lock、持久化 request lease、crash recovery、复杂 rollback；
- 不支持 OpenAI Responses/Assistants/Batch 等 stateful API；
- 不实现请求抢占、优先级、预测式 prefetch 或自动 idle timeout；
- 不改 vLLM allocator/source，除非真实集成测试证明现有 API 有明确缺口；
- 不追求大规模统计或 58-model/32-H100 复现。

---

## 4. 分仓库最小改动矩阵

| 仓库 | 最小改动 | 明确不改 |
|---|---|---|
| `vllm-model-switch-controller` | launcher 安全顺序；`VLLMClient.is_sleeping()`；sleep/wake 后 probe；研究 checkout 的本地两模型配置；少量 metrics 字段（queue/drain、switch_id） | CPU tensor 管理、通用 scheduler、生产级恢复框架 |
| `vllm` | 预期 **0 source change**；只补齐测试环境并运行现有 focused tests/真实 sleep profile | allocator 结构、backup policy、OpenAI routing |
| `llm-switch-bench` | 新增 request-driven open-loop trace runner + 分析；复用 `benchlib.http/resources/schema`；冻结两个小 trace；加入 controller adapter | 另一套 lifecycle engine、Prism simulator、通用集群框架 |

---

## 5. 实施任务（用户批准后执行）

### Task 1：补齐真实 vLLM pool 的安全生命周期验证

**Objective:** 保证两模型即使不能同时驻留，也能逐个启动并 sleep；controller 的状态转换只有在 `/is_sleeping` post-condition 符合后才提交。

**Files:**

- Modify: `controller/main.py`
- Modify: `controller/vllm_client.py`
- Modify: `controller/router.py`
- Modify: `scripts/launch_vllm_pool.py`
- Test: `tests/test_vllm_client.py`
- Test: `tests/test_router.py`
- Create: `tests/test_launch_vllm_pool.py`

**TDD steps:**

1. 写 `is_sleeping(model) -> bool` 和 `wait_until_sleeping(model, expected, timeout)` 的 fake backend tests：支持 `{"is_sleeping": true/false}` 响应、异步状态延迟变化与 deadline；非 2xx/非法响应抛 `VLLMClientError`。
2. 运行 focused test，确认缺少方法而失败。
3. 在 `VLLMClient` 增加一个窄 `/is_sleeping` adapter，并把 constructor 拆为 `request_timeout_s`（OpenAI proxy）与 `switch_timeout_s`（sleep/wake/probe）；由 `controller/main.py::create_app()` 显式传入两者。
4. 写 router test：默认 `always_sleep_previous` 策略下，A 有 active request 时 B 必须等待；sleep 返回 2xx 后状态延迟变 sleeping 时应等待；直到 deadline 仍 awake 时不允许 wake 目标；wake 后直到 deadline 仍 sleeping 时不代理请求。
5. 在 `ensure_model_ready_locked()` 的 sleep/wake 后使用 lifecycle client 的 `switch_timeout_s` 限制 transition + poll，增加 post-condition check，并把 probe latency 记录到 metrics（不做 retries/rollback state machine）。
6. 写 launcher ordering test，断言事件严格为 `launch A, health A, sleep A, probe A, launch B, health B, sleep B, probe B, wake A, probe A`。
7. 重构 launcher：每个 backend（包括 startup）在初始化后都先 sleep 并验证；全部准备好后再 wake startup 并验证。
8. 运行 focused + full tests 和 ruff。

**Verification:**

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv run python -m pytest tests/test_vllm_client.py tests/test_router.py tests/test_launch_vllm_pool.py -q
uv run python -m pytest tests -q
uv run ruff check controller tests benchmarks scripts
```

**Commit:** `feat: verify vllm lifecycle transitions`

---

### Task 2：补齐最小 request/switch 可观测性

**Objective:** 区分请求等待 switch lock/旧模型 drain、实际 sleep/wake 和 steady-resident 路径。当前全局 `switch_lock` 会让后续请求串行进入并重新判定状态，所以本阶段只给实际执行 transition 的请求分配 `switch_id`；未切换请求统一记为 steady-resident，由 `queue_wait_ms` 表达其等待，不增加跨请求 coalescing registry/分类状态机。

**Files:**

- Modify: `controller/metrics.py`
- Modify: `controller/router.py`
- Modify: `controller/state.py`（仅在确实需要读取 active count 时）
- Modify: `benchmarks/analyze_results.py`
- Test: `tests/test_metrics.py`
- Test: `tests/test_router.py`
- Test: `tests/test_analyze.py`

**字段（保持最小）：**

```text
request_id
switch_id | null
route_class = steady_resident | switch_owner
queue_wait_ms
request_drain_ms
sleep_latency_ms
wake_latency_ms
switch_latency_ms
e2e_ttft_ms
e2e_latency_ms
```

**TDD steps:**

1. 写 failing tests：同模型/等待后无需再切换的请求标为 `steady_resident` 且 `switch_id=null`；实际触发切换的请求有 `switch_id` 和 drain/sleep/wake 分解。
2. 写并发 fake-backend test：A 长请求期间 B 到达，B 的 `request_drain_ms > 0`，而 sleep 只发生在 A 完成后。
3. 实现最小时间戳记录；不要添加 tracing framework 或 Prometheus dependency。
4. 更新 analyzer，仅汇总上述字段并按 route class 计数；失败记录保留在分母。
5. 运行 focused/full tests 和 ruff。

**Verification:** 同 Task 1 的 full commands。

**Commit:** `feat: attribute request-driven model switches`

---

### Task 3：创建使用研究版 vLLM 的本地两模型配置

**Objective:** 用两个不同模型启动长期存活 backend，并启用 CPU backup coordinator 与 sleep profile。

**Files:**

- Modify: `.gitignore`（增加 `configs/*.local.yaml`）
- Create: `configs/models.request_switch.local.yaml`（gitignored，不提交机器专属路径）
- Create: `configs/models.request_switch.example.yaml`（提交 model-agnostic 模板）
- Modify: `docs/operations.md`（实施时名为 `docs/first_stage_usage.md`）

**本机首选模型：**

- A: `/home/ljl/models/hf/Qwen2.5-1.5B-Instruct`
- B: `/home/ljl/models/hf/Qwen2.5-3B-Instruct`

两者应分别独立启动，不能依赖同权重硬链接副本。若 3B 在 10 GiB GPU 上的 awake footprint 使启动/切换不可行，降级到 0.5B + 1.5B；该降级必须记录，不偷偷改变结果标签。

**backend executable/cwd：**

```text
executable: /home/ljl/research-systems/llm-switch-bench/.venv/bin/vllm
cwd: /home/ljl/research-systems/vllm
```

**每个 engine 环境：**

```text
VLLM_SERVER_DEV_MODE=1
VLLM_CPU_BACKUP_COORDINATOR=daemon
VLLM_CPU_BACKUP_COORDINATOR_URL=http://127.0.0.1:9000
VLLM_CPU_BACKUP_COORDINATOR_MODEL_ID=<alias>
VLLM_CPU_BACKUP_COORDINATOR_CLIENT_ID=<alias>
VLLM_SLEEP_PROFILE_PATH=<per-engine-jsonl>
```

**Steps:**

1. 用 `--help` 和 import path 验证所选 executable 来自研究 checkout。
2. 生成 local config，设置不同端口、alias、`--enable-sleep-mode`、相同 dtype/max-model-len/sampling 约束；先确认两个模型的 chat template 均可用于 `/v1/chat/completions`，否则统一改用 `/v1/completions`，避免把模板差异误判为切换失败。
3. controller 先启动（coordinator API 必须可达），再运行 launcher；launcher 完成前禁止发送推理请求，因为此时 controller 的配置态与 backend 初始化中间态可能暂时不一致。
4. 检查 `/admin/cpu-backup/stats` 出现两个 model_id/client。
5. 检查 `/v1/models` 返回两个 alias。

**Verification:**

```bash
curl -fsS http://127.0.0.1:9000/v1/models
curl -fsS http://127.0.0.1:9000/admin/cpu-backup/stats
curl -fsS http://127.0.0.1:8101/is_sleeping
curl -fsS http://127.0.0.1:8102/is_sleeping
```

**Commit:** `docs: add request-driven pool configuration`（只提交 example/docs，不提交 local config/logs）

---

### Task 4：运行真实 API 集成 smoke test

**Objective:** 证明 OpenAI client 无需调用管理 API 即可驱动 `A -> B -> B -> A`，以及 B 会等待 A 的长 streaming 请求完成。

**Files:**

- Create: `scripts/smoke_openai_switch.py`
- Test: `tests/test_smoke_openai_switch.py`（只测序列生成/结果校验函数，真实 GPU 运行不进 pytest）
- Modify: `README.md`

**Scenario S1 — basic routing:**

```text
A(short) -> B(short) -> B(short) -> A(short)
```

断言请求全部 200、返回 model/内容非空、切换次数为 2、最后 active model 为 A。

**Scenario S2 — active-request drain:**

```text
t=0: A(short prompt, max_tokens=160, stream=true)
t≈first token: B(short prompt, max_tokens=24, stream=true)
```

断言 A 完成前 backend A 未收到 sleep；B 最终成功；controller active request count 清零。

**Steps:**

1. 写 fake-controller tests，验证 smoke script 只发送标准 OpenAI API 请求，不调用 `/admin/switch`。
2. 实现短小 asyncio client。
3. 运行 fake tests。
4. 启动两个真实 backend + controller。
5. 执行 S1、S2，各保存一个 JSONL 到未跟踪的 run directory。
6. 对照 controller metrics、vLLM sleep profiles 和 backend logs。

**Verification:**

```bash
uv run python scripts/smoke_openai_switch.py \
  --base-url http://127.0.0.1:9000 \
  --models <A> <B> \
  --output results/tmp/request_switch_smoke.jsonl
```

期望：exit 0；所有请求成功；S1 switch count=2；S2 事件顺序符合 RQ2。

**Commit:** `test: add openai model switch smoke client`

---

### Task 5：在 llm-switch-bench 新增冻结 trace 的 open-loop runner

**Objective:** 用独立、可复用的 workload runner 暴露并发等待、切换 contention 和 locality，而不是沿用 controller 里的串行 closed-loop client。

**Files（`/home/ljl/research-systems/llm-switch-bench`）：**

- Create: `src/bench_request_driven_switch.py`
- Create: `src/benchlib/request_trace.py`
- Modify: `src/benchlib/http.py`
- Modify: `src/benchlib/resources.py`（仅复用/补 process-tree + MemAvailable sampler 所需小接口）
- Create: `tests/test_request_trace.py`
- Create: `tests/test_bench_request_driven_switch.py`
- Modify: `README.md`
- Create: `scripts/run_request_switch.sh`

**冻结 manifest row：**

```json
{
  "request_id": "r0001",
  "scheduled_offset_s": 0.0,
  "model": "model-a",
  "endpoint": "/v1/chat/completions",
  "prompt_name": "short_short",
  "max_tokens": 32,
  "temperature": 0,
  "stream": true,
  "seed": 1
}
```

**client record 至少包含：**

```text
request_id, model, scheduled_offset_s
client_dispatch_offset_s, dispatch_lag_ms
status/error
transport_first_byte_ms
semantic_ttft_ms
completion_latency_ms
completion_tokens, tpot_ms（token 数准确且 >=2 时）
```

**Timing semantics：**

- `transport_first_byte`：任意 response byte；
- `semantic first token`：第一个非空生成 content/text，忽略 role-only、metadata、heartbeat 和 `[DONE]`；
- API TTFT：从实际 client dispatch 到 semantic token；
- trace TTFT：从 scheduled arrival 到 semantic token；
- runner 按 absolute `time.monotonic()` deadline 创建独立 task，不等待前一个请求结束；
- 所有请求共享一个 `httpx.AsyncClient`；失败/timeout 保留为行，不从汇总中删除。

**最小测试矩阵（fake SSE server）：**

1. 同一 raw network chunk 中包含多个 SSE events，以及 role-only event 后 content event：必须按 SSE event boundary 解析，semantic TTFT 取第一个非空 content/text；
2. 两请求 overlap 且后发先完成：输出仍按 request_id 可关联；
3. `[DONE]`、broken stream、timeout 均生成 record；
4. arrival manifest 非单调或 request_id 重复时拒绝运行；
5. 同一 manifest 重放两次，不重采样 arrival/prompt/model。

**Verification:**

```bash
cd /home/ljl/research-systems/llm-switch-bench
.venv/bin/python -m pytest tests/test_request_trace.py tests/test_bench_request_driven_switch.py -q
.venv/bin/python -m pytest tests -q
uv run --with ruff ruff check src tests
```

**Commit:** `feat: add request-driven model switch benchmark`

---

### Task 6：运行机制消融实验（核心结果）

**Objective:** 用两模型、单 GPU、小 trace 隔离 API overhead、cold reload、clean backup reuse 与 backup reclaim 的差异。

**固定条件：**

- 同一 GPU、模型 revision/path、dtype、max-model-len、sampling、prompt manifest；
- `temperature=0`，固定 seed；
- warmup 与 measurement 分开；
- 每种机制至少 3 次独立 run；若方差明显再扩到 5 次；
- 轮换 baseline 顺序，避免第一次编译/cache 只影响一个方法；
- 不 drop Linux page cache，不耗尽共享主机内存。

**Workload W0 — steady model（controller overhead）：**

```text
A x 20，低并发，无切换
```

比较 direct vLLM endpoint vs controller。指标：median/p95 semantic TTFT、E2E、controller overhead。

**Workload W1 — worst-case alternating：**

```text
A,B,A,B,... 共 20 请求；低 offered load，确保每次切换可独立观测
```

用途：测每次回切的 switch latency 与机制上限，不宣称是现实 trace。

**Workload W2 — burst locality：**

```text
A×5, B×5, A×5, B×5
```

用途：展示访问 locality 如何降低每请求摊销的切换成本。

W1/W2 先做低 offered load 的**机制实验**，使每次 switch 可分解；只有 Gate 2 通过后，再用同一 runner 增加一个短的 overlap trace（A 长请求期间 B 到达）展示 queue/drain，不在本阶段把它扩成吞吐/SLO 饱和曲线。

**机制 M0–M3：**

| ID | 机制 | 实现/意义 |
|---|---|---|
| M0 | Dedicated direct/controller | 单模型 steady upper bound；两个模型同时常驻若显存允许，否则只做逐模型 dedicated calibration，不把它算同 GPU 多模型方案 |
| M1 | Cold reload | 必须在同一 request-driven adapter、同一 frozen manifest 下显式 stop/start；`bench_vllm_lifecycle.py` 的单模型 lifecycle 数据只作为机制背景，不与 W1/W2 E2E 结果混合 |
| M2 | Upstream vLLM level-1 | 在研究改动前的 vLLM commit `0decac0d96c42b49572498019f0a0e3600f50398` 建独立 worktree/environment；同一 long-lived two-engine controller + manifest。若该 commit 在当前 CUDA/PyTorch 环境不可运行，输出 blocker，并以 proposed 的 first-miss vs clean-reuse 作为补充消融 |
| M3 | Proposed | 研究版 vLLM + long-lived per-model engines + request-driven controller + clean pinned backup reuse |

M2 不通过修改 proposed engine 来“模拟 upstream”。若独立 upstream worktree 无法运行，本阶段不为其做兼容性移植；改用研究版的**first switch/cold backup miss**与**subsequent clean reuse**做 within-process 补充消融，并清楚标注它不是 upstream baseline。

**核心指标：**

- switch latency、sleep、wake、request drain；
- semantic TTFT、E2E/TTLT；
- switch count、steady-hit/switch-miss 数；
- profile 的 D2H/H2D/backup alloc/reuse bytes，并由 raw bytes / copy seconds 计算有效 PCIe bandwidth；
- GPU memory；engine process-tree RSS、VmLck、host `MemAvailable`。

切换开销按两种口径同时报告：`switch_latency`（controller 控制路径）以及“从 scheduled arrival 到 semantic first token”的 request-visible stall；不把 sleep-old 和 wake-target 的相加机械地归因成 Prism/其他系统的 activation latency。

**产物：** 原始 run 放 `results/tmp/request_switch/<run-id>/`（ignored）；版本库 `results/` 只更新每个方法最新 curated summary/figure。

---

### Task 7：运行 CPU pressure/no-pressure 成对实验

**Objective:** 证明 CPU backup 是“有余量时保留、压力时回收”的机会式缓存，并验证逻辑回收对应物理 host memory 变化。

**No-pressure P0：**

- 设置足够低的 reclaim watermark，使 monitor 保持 normal；
- trace：`A -> B -> A -> B -> A`，每个请求完成后切换；
- 预期：clean backup 保留；第二次起 sleep D2H 被跳过；回切 wake 走 H2D backup；release request 为 0。

**Controlled-pressure P1：**

- 不申请大量 RAM；运行前读取实际 `MemAvailable`，将 low/high watermarks 安全地设在当前值之上以触发 monitor；
- 使用同一个 frozen trace；
- 预期：controller request release > 0，vLLM `released_bytes_total` 增长，worker process-tree RSS 下降达到预注册最小值，且 host `MemAvailable` 上升达到预注册最小值；再次访问被回收模型后，后续 sleep 恢复 pinned allocation/D2H。

**结果解释：**

- `required_for_restore_bytes` 在 engine sleeping 时不可回收；clean backup 在 wake 后转成 `cache_only` 才可被 controller 请求释放；
- 因此 pressure trace 需要在 wake 后留短 observation window，让 release poller 和 OS sampler 捕捉事件；
- logical counter 成功但 RSS 或 `MemAvailable` 任一未达到预注册阈值，实验判为 physical reclaim 未完整验证，不能写成成功；`VmLck` 只作辅助（PyTorch pinned allocation 不保证等同于 mlock accounting）。

**复用：** 优先复用 `bench_vllm_repeated_sleep_l1.py` 的 assertion 与 resource sampling，不再写第三套 reclaim checker。

---

### Task 8：结果分析、相关工作边界与阶段报告

**Objective:** 生成简短、可审计的阶段报告，回答 RQ1–RQ4，不夸大为 Prism 的直接替代。

**Files（`llm-switch-bench`）：**

- Create: `src/tool/analyze_request_switch.py`
- Create: `tests/test_analyze_request_switch.py`
- Create: `docs/reports/request-driven-multi-model-switch.md`
- Curate: `results/request_switch/latest/`（仅最新 summary/figure/manifest checksum/metadata）

**报告图表（最多 4 个）：**

1. W1/W2 各机制 semantic TTFT 分布（median + IQR + p95）；
2. 每次 switch 的 sleep/wake/drain 分解；
3. P0/P1 的 clean reuse、D2H/H2D 与 switch latency；
4. P1 的 logical release + process RSS + host `MemAvailable` 时间线。

**报告表：**

- 模型、GPU、host memory、三个 repo commit/dirty state；
- frozen manifest checksum；
- 成功/失败/timeout 数；
- offered/achieved request rate；
- switch count；
- TTFT/E2E median/p95；
- CPU backup logical/physical counters。

**Commit:** `bench: evaluate request-driven model switching`

---

## 6. 与 Prism 及相关工作的比较设计

### 6.1 Prism 的可核实设计与环境

Prism（OSDI ’26）是基于 SGLang 的 GPU-memory-centric multi-LLM co-serving system：

- kvcached/CUDA VMM 实现按需 GPU physical memory ballooning；
- 同时支持 spatial sharing 与 temporal sharing；
- reusable engine pools 与 parallel weight loading 加速 activation；
- global KVPR placement + local slack-aware request arbitration；
- 论文环境为 4 nodes × 8 H100-80G（最多 32 GPUs），每节点 1.7 TB DRAM、NVLink 600 GB/s、100 Gbps Ethernet；
- 论文 baseline：static partition、MuxServe++、QLM、ServerlessLLM；主指标是 per-model dedicated calibration 后的 TTFT/TPOT SLO attainment。

本项目当前环境为单 RTX 3080 10 GiB、约 64 GiB host RAM、PCIe 4.0 x16。因此不能把 Prism 论文的绝对数字或 2×/3.5× 结果与本机数字直接横向比较。

### 6.2 可执行的公平层级

**Level A — 必做、同引擎机制对比：**

- vLLM cold reload；
- vLLM level-1 first backup miss；
- proposed clean pinned backup reuse；
- backup reclaimed 后的 miss；
- direct/dedicated calibration。

这些能直接支撑本阶段核心 claim。

**Level B — 尽力执行的外部 time-sharing baseline：**

- ServerlessLLM（仓库已有 adapter）；
- SwapServeLLM（仓库已有 adapter）。

前提是固定同模型、dtype、GPU budget、storage tier、请求 manifest。artifact 无法在 RTX 3080/CUDA 环境运行时，输出 structured blocker；不简化成自制 simulator。

**Level B2 — 优先于完整 Prism 的 Prism-like 子集：**

- `kvcached` main 的 vLLM integration + 官方 OpenAI-compatible controller/router（request `model` routing、idle sleep、request-triggered wake）；
- pin `kvcached` commit `623dbf2642dce1f9d27a154b7367605d26221c3c`（审阅时 main；执行时若更新则重新记录），使用其明确支持的 vLLM 版本和同一 W1/W2 manifest；
- 分成两个结果域：`switch micro` 比较 request-triggered L1 lifecycle；另一个可选 `hybrid trace` 展示 elastic KV/多模型共驻。后者不与本项目 pure temporal-sharing 的 switch latency 合并成单一排名。

这是当前单机单 GPU 上最接近 Prism memory mechanism、又比完整 Prism/SGLang artifact 更容易执行的外部 baseline。首轮核心机制结果完成后，优先尝试它；ServerlessLLM/SwapServeLLM 只在仍有时间时补充。

**Level C — Prism artifact smoke / micro comparison：**

Prism artifact 当前安装文档使用 `lmsysorg/sglang:v0.3.4.post2-cu121`、Redis、kvcached `prism/shm`。先 pin Prism commit `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8`（审阅时 main），并在执行时记录 `prism/shm` 实际解析到的 kvcached commit。当前 main 已删除旧的 run instructions，因此先验证 container/install/import，再从该 commit 现存的最小 model config 构造并完整记录一个**本地 artifact smoke**；不得称为官方 reproduction。若能运行，只做：

- 1 GPU、2 small models；
- 相同 W1/W2 manifest 的 adapter 转换；
- 报告成功率、TTFT/E2E、GPU memory；
- 明确这是单 GPU artifact micro-run，未启用/无法体现多 GPU parallel weight loading 与 cluster placement。

若 artifact 因 H100/NVLink、旧 CUDA/SGLang dependency 或 GPU memory 不满足而失败，报告 blocker 后停止；不为跑通 Prism 大规模修改共享机器环境。

### 6.3 不能声称公平的比较

- 本机 proposed vs Prism 论文 Figure 5/9 数值；
- 单 GPU temporal switching vs Prism 的 32-GPU cost reduction；
- 本项目 CPU backup pool vs Prism GPU KV ballooning；
- 两模型 synthetic trace vs 58-model Hyperbolic/Arena trace；
- RTX 3080 PCIe copy vs H100 + NVLink parallel loading。

### 6.4 可借鉴但不在本阶段实现的 Prism 实验思想

- 用 dedicated per-model p95 乘统一 scale 定义 SLO，而不是每个系统各定阈值；
- 同一 frozen trace 对不同系统重放；
- 同时报 TTFT 和 TPOT attainment，失败保留在分母；
- 两模型 burst shifting trace 用于解释 memory sharing；
- 之后若扩展到多 GPU，再考虑 working-set placement 与 spatial/temporal hybrid。

---

## 7. 阶段门禁与停止条件

### Gate 1：正确性

Task 1–4 通过；真实 `A -> B -> B -> A` 与 concurrent drain test 成功。否则不进入性能实验。

### Gate 2：机制证据

P0 能看到 reuse + D2H skip；P1 能看到 release ack + physical memory evidence + 后续 D2H return。任一缺失，就先定位机制，不扩大 workload。

### Gate 3：性能结果

W1/W2 至少 3 repeats，结果可重复且能被 switch count/bytes profile 解释。只有通过后才尝试外部 baseline。

### 停止/降级条件

- 1.5B+3B 无法在 10 GiB 上完成安全 sequential initialization/switch：降为 0.5B+1.5B；
- Prism artifact install/import 或本地最小 smoke 在当前硬件/软件不支持：记录 blocker，不继续移植；
- 外部 baseline 需要系统级 CUDA/driver/container 改动：不改共享服务器，记录 blocker；
- proposed 与 first-miss 没有可测差异：先检查 profile 是否真的命中 clean backup，不增加模型数/并发掩盖问题。

---

## 8. 完成定义

本阶段只有满足以下条件才算完成：

- [ ] 用户可用标准 OpenAI client 在一个 base URL 下请求两个不同模型；
- [ ] model 字段自动驱动 sleep/wake，不需要 client 调 `/admin/switch`；
- [ ] 两个 backend 来自研究版 vLLM checkout，而非无 CPU pool 的 installed wheel；
- [ ] A 活跃 streaming 请求不会被 B 请求提前 sleep；
- [ ] `A -> B -> B -> A` 真实 GPU smoke 通过；
- [ ] open-loop frozen trace runner 有 fake SSE 并发/错误 tests；
- [ ] no-pressure 下 clean backup reuse 与 D2H skip 被 profile 证明；
- [ ] controlled-pressure 下 logical 和 physical reclaim 同时有证据；
- [ ] W1/W2 至少 3 repeats，raw failures 未被过滤；
- [ ] 报告清楚区分本机结果、外部 artifact 结果与 paper-reported 结果；
- [ ] controller、bench full tests/ruff 通过，vLLM focused tests 在依赖补齐后通过；
- [ ] 三仓库每个 review unit 都有独立实际 review summary，发现项已针对当前 commit 复核并回归；
- [ ] `llm-switch-bench/results/` 只保留最新 curated 输出，原始 run 不进 git。

---

## 9. 推荐执行顺序与预计 review units

1. **Controller lifecycle contract**（Task 1，单独 commit/review）
2. **Controller switch observability**（Task 2，单独 commit/review）
3. **真实 vLLM 接线 + smoke**（Task 3–4，配置/docs 与 smoke client 分 commit）
4. **Open-loop benchmark**（Task 5，单独 commit/review）
5. **机制与 pressure 实验**（Task 6–7，不先改代码，先执行冻结方案）
6. **外部 baseline：优先 kvcached/vLLM controller，其次 ServerlessLLM/SwapServeLLM；完整 Prism smoke 最后**（核心结果通过后再做）
7. **分析与报告**（Task 8）

每个 review unit：RED → minimal GREEN → focused tests → full tests/lint → git diff/secrets inspection → 实际独立 review summary → 修复后 targeted regression + full verification → commit。
