# Fork Delta from vLLM v0.22.1

Base: upstream tag `v0.22.1`, commit
`0decac0d96c42b49572498019f0a0e3600f50398`.

The release branch must contain only project-related changes. The public diff should be
audited against an approved file list before tagging; unrelated upstream cherry-picks are
not part of the vLLM Switch contribution.

## Implementation

| Path | Role |
|---|---|
| `vllm/device_allocator/cumem.py` | Backup versions/states, eager preparation, sleep transaction, reuse, reclaim, exact-disk restore, telemetry. |
| `vllm/device_allocator/cpu_backup_coordinator.py` | Process-local HTTP client for registration, usage, and cumulative release polling. |
| `vllm/device_allocator/exact_disk_backup.py` | Transactional process-incarnation disk bundles, manifests, checksums, and pipelined restore. |
| `vllm/v1/worker/gpu_worker.py` | Lifecycle lock, readiness-time prebackup, mutation fencing, reclaim poller, recovery state. |
| `vllm/envs.py` | Canonical exact-disk environment variables. |

The complete engine branch may also modify executor and EPLB integration files for
collective prepare/commit and mutation invalidation. Those paths must appear in the release
manifest whenever present; this summary is not permission to omit them from review.

## Documentation

| Path | Role |
|---|---|
| `docs/design/cpu_weight_backup.md` | Architecture index and invariants. |
| `docs/design/eager_cpu_weight_backup.md` | Prebackup lifecycle and mutation coverage. |
| `docs/design/pinned_cpu_backup_pool.md` | Local pool, coordinator, release semantics, telemetry. |
| `docs/design/exact_disk_backup.md` | Exact disk format and failure model. |
| `docs/features/sleep_mode.md` | User-facing sleep-mode extension. |
| `docs/fork_release.md` | Fork identity and release configuration. |

## Focused tests

| Path | Coverage |
|---|---|
| `tests/basic_correctness/test_cumem.py` | Allocator backup state, transaction, reuse, invalidation, reclaim, wake tags. |
| `tests/basic_correctness/test_cpu_backup_coordinator.py` | HTTP retry/order/idempotency contract. |
| `tests/basic_correctness/test_exact_disk_backup.py` | Disk store transaction, manifest, checksum, cleanup, pipeline failures. |
| `tests/basic_correctness/test_exact_disk_backup_gpu.py` | GPU-level disk restore integration. |
| `tests/v1/executor/test_sleep_cpu_backup.py` | Multi-worker prepare/commit/abort behavior. |
| `tests/v1/worker/test_weight_backup_lifecycle.py` | Worker lifecycle and mutation fencing. |
| `tests/v1/worker/test_eplb_cpu_backup.py` | EPLB invalidation coverage. |

## Release audit command

From the vLLM fork:

```bash
git diff --name-status \
  0decac0d96c42b49572498019f0a0e3600f50398..release/v0.1
```

Review every listed path. A clean public branch should not contain unrelated quantization,
TPU, model-registry, or other upstream feature changes merely because they were useful in
a development checkout.

## Behavioral delta

The fork changes level-1 sleep from “always allocate/copy CPU bytes on demand” to a
versioned exact-restore-source lifecycle:

```text
engine warmup
  -> eager clean CPU snapshot
  -> sleep prepare leases restore sources
  -> sleep commit unmaps GPU
  -> wake restores from current CPU or exact disk source
  -> clean CPU snapshot becomes cache-only and reusable
  -> pressure may reclaim cache-only/invalid/free-local bytes
```

Level-2 sleep remains checkpoint reconstruction rather than exact runtime-byte restore.
