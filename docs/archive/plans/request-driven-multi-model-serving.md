# Request-Driven Multi-Model vLLM Switching: Archived Development Plan

> This document records a completed implementation stage. It is not an operating guide.
> See the current [architecture](../../architecture.md) and
> [operations guide](../../operations.md).

**Goal:** Let users send standard OpenAI-compatible requests with a `model` field to one
controller. The controller selects a long-lived vLLM backend, safely completes any
sleep/wake transition, forwards the request, and benefits from process-local pinned CPU
backup when host memory permits.

**Architecture:** Retain one long-lived, single-model vLLM engine per model behind an
external controller. The controller owns alias routing, request reservations, lifecycle
serialization, and aggregate host-pressure policy. vLLM owns tensor-local sleep/wake,
D2H/H2D, clean-backup reuse, and concrete reclamation.

**Stack:** Python 3.11/3.12, FastAPI, httpx, vLLM Sleep Mode, CUDA/PyTorch pinned memory,
pytest, Ruff, JSONL, and CSV.

## 1. State at the Start of the Stage

At review time, `vllm-model-switch-controller` branch `research/pinned-backup-pool` at
`ef656aed` already implemented the main request path:

```text
POST /v1/chat/completions or /v1/completions
  -> read external model alias
  -> make target ready under switch_lock
  -> reserve the target before releasing switch_lock
  -> rewrite model to backend served_model_name
  -> proxy JSON or SSE
  -> release the reservation exactly once on completion, disconnect, or cancellation
```

Relevant components were `controller/router.py`, `controller/state.py`,
`controller/vllm_client.py`, and `controller/policies.py`. The work was therefore an
integration and evidence stage, not a new proxy implementation.

The companion vLLM branch `research/pinned-backup-pool` at `b2057ef6` already provided:

- process-local pinned CPU backup during level-1 sleep;
- retention of clean backup after wake for immutable inference weights;
- clean-backup reuse on later sleep, avoiding repeated D2H;
- metadata-only aggregate reporting to an external coordinator;
- release of only `free_local`, `invalid`, and `cache_only` bytes;
- protection of restore-required and copy-in-flight storage;
- host pinned-cache flushing and monotonic `released_bytes_total` acknowledgement.

The `llm-switch-bench` branch `research/pinned-backup-pool-bench` at `0d0b9d0f` already
contained lifecycle and repeated-sleep tools, ServerlessLLM and SwapServeLLM adapters,
and OS resource sampling. It was designated as the only primary benchmark repository.
The controller's `benchmarks/run_workload.py` would remain a sequential smoke client.

### Integration Risk

The controller `.venv` imported an installed vLLM wheel that did not contain the research
CPU backup pool. Real backends had to use the research checkout explicitly. Otherwise
OpenAI routing could succeed while the intended backup reuse/reclaim mechanism was never
exercised.

### Launcher Gap

The launcher initially kept `startup_awake_model` awake while initializing later engines.
That could exceed GPU capacity. The planned sequence was:

```text
launch A -> health A -> sleep and verify A
launch B -> health B -> sleep and verify B
...
wake and verify startup model
```

Lifecycle post-conditions needed polling deadlines because a management response could
precede completion in a multiprocessing frontend. The stage deliberately excluded a
general rollback or reconciliation framework.

### Initial Verification Snapshot

- Controller: 59 tests passed; Ruff passed.
- `llm-switch-bench`: 81 tests passed; Ruff passed.
- Focused vLLM tests were blocked by missing `tblib`, not by a test assertion failure.
- All three repositories were clean before and after the read-only review.

## 2. Research Questions and Success Criteria

### RQ1: Is the API path transparent and correct?

Can a client request `A -> B -> B -> A` by changing only the OpenAI `model` field,
without knowing backend ports or lifecycle APIs?

Success required:

- two logical aliases from `/v1/models`;
- four successful streaming requests;
- switches only for `A -> B` and `B -> A`, not `B -> B`;
- request ID and model order correlated across client records, controller metrics, and
  backend logs.

### RQ2: Does switching respect active requests?

If a B request arrives while A is streaming, the required order was:

```text
A first token
B scheduled and queued
A completion
sleep A
wake B
B first token
```

A must not be interrupted and all reservations must return to zero. This validated simple
global FCFS/drain behavior only, with no preemption or priority scheduling.

### RQ3: Does spare host memory reduce switch cost?

Success required clean-backup reuse and zero/timer-resolution D2H on later sleeps without
pressure. Under controlled pressure, the controller had to request release, vLLM had to
increase `released_bytes_total`, worker process-tree RSS and host `MemAvailable` had to
meet preregistered physical thresholds, and the next sleep after reclamation had to
recreate backup with allocation and D2H.

The claim was intentionally narrow: clean backup accelerates a later **sleep** by avoiding
allocation and D2H. Wake still performs H2D while backup exists.

### RQ4: Which access patterns benefit?

The evaluation would compare worst-case alternating access with burst locality and
explain results through switch count, steady hits, switch misses, TTFT, and completion
time. It would describe this system as single-GPU temporal sharing, not as an equivalent
implementation of Prism's spatial/temporal sharing and cluster scheduling.

## 3. Architecture and Non-Goals

### Data Plane

```text
OpenAI client
  -> controller :9000
       -> alias -> ModelSpec
       -> switch_lock
          -> drain active requests on old model
          -> POST old /sleep and verify /is_sleeping == true
          -> POST target /wake_up and verify /is_sleeping == false
          -> reserve target request
       -> proxy chat or text completion
  -> long-lived vLLM backend :8101 or :8102
```

### CPU Backup Control Plane

```text
vLLM process-local allocator
  -> aggregate required/cache-only/invalid/free bytes
controller backup state and memory-pressure monitor
  -> cumulative byte release target
vLLM
  -> release locally safe states only
  -> flush host pinned cache
  -> acknowledge with released_bytes_total
```

### Explicit Non-Goals

- Multiple replicas per model, multiple GPU resource groups, or tensor-parallel placement
- Prism's kvcached, GPU-memory ballooning, KV placement, or slack-aware arbitration
- Distributed locks, persistent request leases, crash recovery, or complex rollback
- Stateful OpenAI Responses, Assistants, or Batch APIs
- Request preemption, priority, predictive prefetch, or automatic idle timeouts
- Large-scale statistics or reproduction of a 58-model, 32-H100 environment
- vLLM allocator changes unless real integration exposed a specific missing contract

## 4. Planned Work by Repository

| Repository | Minimal work | Explicit exclusions |
|---|---|---|
| `vllm-model-switch-controller` | Safe launcher order, `/is_sleeping` verification, research-backend example, request/switch metrics | Tensor management, general scheduler, production recovery |
| `vllm` | No expected source change; complete focused tests and collect real profiles | Backup policy redesign, OpenAI routing |
| `llm-switch-bench` | Frozen open-loop trace runner, controller adapter, analysis, resource evidence | Second lifecycle engine, Prism simulator, general cluster framework |

## 5. Implementation Units

### Task 1: Verify Real Backend Lifecycle Transitions

Add `is_sleeping()` and deadline-based state polling to the vLLM client. Apply one shared
transition deadline to the management request and its post-condition. Mark uncertain
states as errors instead of assuming a 2xx response means completion.

Change the launcher to initialize every backend, sleep and verify it, then wake and verify
the startup model. Tests would cover delayed state changes, invalid probe responses,
timeouts, active-request drain, and exact launcher event order.

Planned commit: `feat: verify vllm lifecycle transitions`.

### Task 2: Add Minimal Request/Switch Attribution

Record:

```text
request_id
switch_id or null
route_class = steady_resident or switch_owner
queue_wait_ms
request_drain_ms
sleep_latency_ms
wake_latency_ms
switch_latency_ms
e2e_ttft_ms
e2e_latency_ms
```

Only the request that performs the transition owns a `switch_id`. A later request that
waited on the global lock but found the target ready remains `steady_resident`; its delay
is represented by `queue_wait_ms`. The analyzer must keep failed records in its counts.

Planned commit: `feat: attribute request-driven model switches`.

### Task 3: Connect Two Research vLLM Backends

Create a committed machine-agnostic example and an ignored local configuration. Use two
different model aliases, ports, and model paths with the research vLLM executable and:

```text
VLLM_SERVER_DEV_MODE=1
VLLM_CPU_BACKUP_COORDINATOR=daemon
VLLM_CPU_BACKUP_COORDINATOR_URL=http://127.0.0.1:9000
VLLM_CPU_BACKUP_COORDINATOR_MODEL_ID=<alias>
VLLM_CPU_BACKUP_COORDINATOR_CLIENT_ID=<alias>
VLLM_SLEEP_PROFILE_PATH=<per-engine-jsonl>
```

The preferred models were Qwen2.5 1.5B and 3B. If a 10 GiB GPU could not safely
initialize and switch that pair, the documented fallback was 0.5B and 1.5B. Any fallback
had to be reflected in result labels. Controller startup had to precede backend startup,
and inference was prohibited until launcher completion.

Planned commit: `docs: add request-driven pool configuration`, excluding local paths and
logs.

### Task 4: Add a Real OpenAI API Smoke Test

Scenario S1:

```text
A(short) -> B(short) -> B(short) -> A(short)
```

All requests had to return 200 with non-empty model/content, produce exactly two switches,
and leave A active.

Scenario S2:

```text
t=0: A(max_tokens=160, stream=true)
t=first token: B(max_tokens=24, stream=true)
```

B had to wait until A completed, both had to succeed, and active-request counts had to
return to zero. Fake tests would prove the script used only standard OpenAI inference
requests and never `/admin/switch`.

Planned commit: `test: add openai model switch smoke client`.

### Task 5: Add a Frozen Open-Loop Runner to `llm-switch-bench`

Each manifest row would freeze request ID, absolute scheduled offset, model, endpoint,
prompt name, token limit, temperature, streaming mode, and seed. The runner would schedule
independent tasks against absolute `time.monotonic()` deadlines and share one
`httpx.AsyncClient`.

Each record would include dispatch lag, status/error, transport first byte, semantic TTFT,
completion latency/tokens, and TPOT when token counting was valid. Semantic TTFT meant the
first non-empty generated content/text, excluding role-only events, metadata, heartbeats,
and `[DONE]`. Failures and timeouts remained as rows.

Fake SSE tests covered multiple events in one network chunk, role-only events before
content, overlapping requests finishing out of order, broken streams, timeouts, duplicate
IDs, non-monotonic manifests, and deterministic replay.

Planned commit: `feat: add request-driven model switch benchmark`.

### Task 6: Run Core Mechanism Ablations

Fixed controls included GPU, model revision/path, dtype, max model length, sampling,
prompt manifest, temperature zero, and seed. Warmup and measurement were separate. Each
mechanism required at least three independent runs, extended to five when variance was
material. Baseline order rotated; Linux page cache was not dropped and host RAM was not
exhausted.

Workloads:

- **W0 steady:** A x 20 at low concurrency, comparing direct backend with controller.
- **W1 alternating:** A, B, A, B, ... for 20 low-load requests, exposing worst-case
  switch cost rather than claiming a realistic trace.
- **W2 burst locality:** A x 5, B x 5, A x 5, B x 5, showing amortization from locality.
- A short overlap trace could be added only after mechanism evidence passed, to show
  queue/drain rather than a throughput saturation curve.

Mechanisms:

| ID | Mechanism | Meaning |
|---|---|---|
| M0 | Dedicated direct/controller | Per-model steady upper bound; two resident models only if memory permitted |
| M1 | Cold reload | Explicit stop/start through the same adapter and frozen manifest |
| M2 | Upstream vLLM level-1 | Separate worktree at pre-research commit `0decac0d96c42b49572498019f0a0e3600f50398` |
| M3 | Proposed | Research vLLM, long-lived engines, request-driven controller, clean pinned-backup reuse |

M2 could not be simulated by modifying the proposed engine. If the pinned upstream commit
was incompatible with the current CUDA/PyTorch environment, the report would record a
structured blocker and use first-miss versus later clean reuse only as a within-process
ablation, explicitly not an upstream baseline.

Core metrics were switch/sleep/wake/drain latency, semantic TTFT, end-to-end latency,
switch and hit/miss counts, D2H/H2D and backup allocation/reuse bytes, effective PCIe
bandwidth, GPU memory, process-tree RSS, `VmLck`, and host `MemAvailable`.

The report had to separate controller `switch_latency` from request-visible stall between
scheduled arrival and semantic first token.

### Task 7: Pair No-Pressure and Controlled-Pressure Runs

Both used `A -> B -> A -> B -> A`.

- **P0 no pressure:** watermarks kept the monitor normal. Expected clean-backup retention,
  skipped D2H on later sleeps, H2D on wake, and zero release requests.
- **P1 controlled pressure:** watermarks were safely set above current `MemAvailable`,
  without allocating large amounts of RAM. Expected positive release requests and
  acknowledgement, preregistered RSS and `MemAvailable` recovery, and allocation/D2H
  returning after the reclaimed model was used again.

Restore-required backup is not evictable while an engine sleeps. Clean backup becomes
`cache_only` after wake, so P1 needed an observation window after wake. A logical release
without both RSS and `MemAvailable` evidence could not be reported as complete physical
reclamation. `VmLck` was auxiliary because PyTorch pinned allocation is not equivalent to
mlock accounting.

### Task 8: Produce an Auditable Stage Report

The report was limited to four main figures: W1/W2 semantic TTFT distributions, per-switch
sleep/wake/drain decomposition, P0/P1 copy/reuse/switch differences, and the P1 logical
release plus RSS and `MemAvailable` timeline.

The summary table had to include models, GPU, host memory, all repository commits and
dirty states, manifest checksum, success/failure/timeout counts, offered and achieved
request rates, switch count, TTFT/end-to-end median and p95, and logical/physical backup
counters. Only the latest summary, figures, manifest checksum, and metadata would be
curated; raw runs stayed ignored.

Planned commit: `bench: evaluate request-driven model switching`.

## 6. Comparison Guardrails

The plan treated Prism as a GPU-memory-centric multi-LLM co-serving system based on
SGLang, kvcached/CUDA VMM, spatial and temporal sharing, reusable engine pools, parallel
weight loading, global placement, and local request arbitration. Its reported environment
used up to 32 H100-80G GPUs with NVLink and large host memory. The local environment was a
single RTX 3080 10 GiB host with PCIe 4.0 x16, so absolute numbers and published speedups
were not directly comparable.

Comparison levels were:

1. **Required same-engine mechanisms:** cold reload, level-1 first backup miss, clean
   pinned-backup reuse, post-reclaim miss, and dedicated calibration.
2. **Best-effort external baselines:** ServerlessLLM and SwapServeLLM with identical
   models, dtype, GPU budget, storage tier, and manifest. Unsupported artifacts produced
   structured blockers, not custom simulators.
3. **Preferred Prism-like subset:** kvcached vLLM integration and its OpenAI-compatible
   controller at pinned commit `623dbf2642dce1f9d27a154b7367605d26221c3c`, separating
   lifecycle micro results from optional elastic-KV coexistence results.
4. **Prism artifact smoke:** pinned commit
   `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8`, one GPU, two small models, and adapted
   W1/W2 only if the historical container/dependencies worked. This could be called a
   local artifact micro-run, never an official reproduction.

The following claims were explicitly prohibited:

- local proposed results versus Prism paper figure values;
- single-GPU temporal switching versus Prism's 32-GPU cost reduction;
- CPU weight backup versus GPU KV-memory ballooning;
- a two-model synthetic trace versus 58-model production-derived traces;
- RTX 3080 PCIe copy versus H100/NVLink parallel loading.

Useful experimental ideas retained from related work were frozen-trace replay, SLOs based
on common dedicated calibrations, reporting TTFT and TPOT attainment with failures in the
denominator, and burst-shifting traces for locality.

## 7. Gates and Stop Conditions

### Gate 1: Correctness

Tasks 1-4, real `A -> B -> B -> A`, and concurrent drain had to pass before performance
work began.

### Gate 2: Mechanism Evidence

P0 needed clean reuse and D2H skip. P1 needed release acknowledgement, physical memory
evidence, and D2H returning after reclaim. Missing evidence stopped workload expansion.

### Gate 3: Repeatable Performance

W1/W2 required at least three reproducible repeats explained by switch counts and byte
profiles before any external baseline was attempted.

Stop or downgrade conditions:

- If 1.5B + 3B could not initialize and switch safely on 10 GiB, use 0.5B + 1.5B and
  relabel every result.
- If Prism could not install/import or run a minimal smoke on the available hardware,
  record the blocker and stop porting.
- Do not make system-wide CUDA, driver, or container changes for an external baseline on
  a shared machine.
- If proposed and first-miss behavior were indistinguishable, verify profile reuse before
  increasing model count or concurrency.

## 8. Completion Checklist

- [ ] A standard OpenAI client can request two aliases from one base URL.
- [ ] The `model` field drives lifecycle changes without `/admin/switch`.
- [ ] Both backends use the research vLLM checkout for CPU backup experiments.
- [ ] A long A stream cannot be slept early by a B request.
- [ ] Real `A -> B -> B -> A` smoke passes.
- [ ] The open-loop runner has fake SSE concurrency and error tests.
- [ ] No-pressure runs prove clean reuse and D2H skip.
- [ ] Controlled-pressure runs provide both logical and physical reclaim evidence.
- [ ] W1/W2 have at least three repeats and retain raw failures.
- [ ] Reports distinguish local, external-artifact, and paper-reported results.
- [ ] Controller and benchmark tests/lint pass; focused vLLM tests pass when dependencies
  are available.
- [ ] Each review unit receives an independent review and regression pass.
- [ ] Only the latest curated benchmark output is tracked; raw runs remain ignored.

## 9. Planned Review Order

1. Controller lifecycle contract
2. Controller switch observability
3. Research vLLM wiring and real smoke test
4. Open-loop benchmark runner
5. Mechanism and pressure experiments
6. kvcached/vLLM baseline, then ServerlessLLM/SwapServeLLM if time permitted, and full
   Prism smoke last
7. Analysis and report

Each review unit followed: failing test, minimal implementation, focused tests, full
tests/lint, diff and secret inspection, independent review, targeted regression, and full
verification before commit.
