# vLLM Fork Compatibility and Testing

## Supported baseline

The v0.1 fork is based on upstream vLLM `v0.22.1` at
`0decac0d96c42b49572498019f0a0e3600f50398`. It is not a promise that the allocator,
worker hooks, or management API apply cleanly to later upstream releases.

Each upstream upgrade must revalidate:

- CuMem allocator tag and sleep semantics;
- worker initialization order and CUDA graph readiness;
- all model mutation/reload/EPLB paths;
- management endpoint query parameters and post-conditions;
- coordinator wire schema;
- exact disk manifest and failure fencing;
- CPU RSS and GPU inference correctness after reclaim/restore.

## CPU-focused release tests

Run from the compatible vLLM environment:

```bash
.venv/bin/python -m pytest -q \
  tests/basic_correctness/test_cpu_backup_coordinator.py \
  tests/basic_correctness/test_cumem.py \
  tests/basic_correctness/test_exact_disk_backup.py \
  tests/v1/executor/test_sleep_cpu_backup.py \
  tests/v1/worker/test_weight_backup_lifecycle.py \
  tests/v1/worker/test_eplb_cpu_backup.py
```

The exact command may require the fork's documented vLLM test dependencies. Record Python,
PyTorch, CUDA, vLLM commit, and complete output.

## GPU release gates

A release artifact should additionally exercise:

1. default CUDA-graph or supported production engine initialization;
2. first level-1 sleep using eager prebackup with zero weight D2H;
3. wake followed by output-equality inference;
4. staged `weights` then `kv_cache` wake when that mode is claimed;
5. repeated same-process sleep/wake demonstrating clean backup reuse;
6. controlled reclaim with logical acknowledgement, RSS drop, and `MemAvailable` recovery;
7. post-reclaim sleep rebuilding CPU backup and returning to reuse;
8. exact disk spill/reclaim/restore with checksum verification;
9. corrupted or missing exact disk data failing closed;
10. two-model request-driven switching through this controller.

GPU checks belong on a dedicated runner or in a recorded release-validation workflow, not
in the controller's hardware-free unit CI.

## Compatibility failures

| Symptom | Likely mismatch |
|---|---|
| Coordinator `422` | Protocol version/capabilities absent or different. |
| Unknown exact-disk variable | Engine older than canonical `VLLM_EXACT_DISK_BACKUP_*` contract. |
| `/is_sleeping` missing/non-boolean | Wrong vLLM management API revision or development mode disabled. |
| Partial wake fails inference | Configured tag set does not restore all required allocations. |
| Usage accepted but no physical reclaim | Worker host-cache flush unavailable/failed or no reclaimable bytes. |
| Snapshot reused after out-of-tree mutation | Mutation path failed to invalidate the weights tag; engine integration bug. |
