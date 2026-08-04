# Compatibility Matrix

v0.1 is a coordinated research release. Do not assume compatibility from repository names
or nearby dates; pin exact revisions in deployments and experiment metadata.

## Release contract

| Component | v0.1 contract |
|---|---|
| Controller package | `vllm-switch-controller==0.1.5` |
| Controller lifecycle-fix commit | `87c30a4d626670f8c2af780699fd5fb7182d2ddf` |
| Controller release tag | `v0.1.5` |
| vLLM upstream base | tag `v0.22.1`, commit `0decac0d96c42b49572498019f0a0e3600f50398` |
| vLLM Switch fork release tag | `aipc2-v0.1.0` |
| vLLM Switch fork release commit | `71071ce4d0bc65e38acf2da76eb8c6fb05b9454d` |
| vLLM evidence collection commit | `1b3919d8c210af05f6ea8b29fff33fb8d07e6c1d` |
| CPU backup protocol | version `1` |
| Exact disk manifest | schema version `1` in the compatible fork |
| Benchmark artifact-closure commit | `llm-switch-bench` at `1b0fb0d6673ac90028d19b10f297dbb1ec05852a` |
| Benchmark release tag | `v0.1.6` |

Hosted releases use the immutable commits above. The three repositories are tagged
independently because the vLLM fork shares upstream's existing tag namespace.

## Protocol capabilities

Protocol v1 defines these controller/worker capabilities:

```text
cumulative-release-v1
released-bytes-total-v1
process-incarnation-v1
exact-disk-accounting-v1
```

Registration and usage requests declare the capabilities they use. Unknown capabilities,
an unsupported `protocol_version`, or missing required metadata fail validation. A client
cannot change PID, protocol version, or capability set while retaining the same complete
process-incarnation ID.

The compatible vLLM commit above implements this explicit handshake. Older local forks
without these fields receive no valid coordinator usage; basic OpenAI routing and
sleep/wake can still work independently.

## vLLM management API contract

Every managed backend must provide:

| Endpoint | Required result |
|---|---|
| `GET /health` | 2xx when ready for lifecycle control. |
| `POST /sleep?level=1|2` | 2xx only when accepted. |
| `POST /wake_up` | No `tags` means wake all; repeated `tags` selects a non-empty subset. |
| `GET /is_sleeping` | JSON object containing boolean `is_sleeping`. |

The controller verifies sleep and wake post-conditions under one transition deadline.

## Exact disk configuration

Only the canonical vLLM variables are supported:

```text
VLLM_EXACT_DISK_BACKUP_ENABLED
VLLM_EXACT_DISK_BACKUP_DIR
VLLM_EXACT_DISK_BACKUP_CHUNK_BYTES
VLLM_EXACT_DISK_BACKUP_DIRECT_IO
```

`VLLM_CPU_BACKUP_DISK_DIR` is deliberately absent. The controller never invents or injects
a disk location.

## Supported Python versions

The controller CI covers Python 3.11 and 3.12. vLLM, CUDA, PyTorch, GPU architecture, and
model compatibility are governed by the pinned vLLM fork rather than the lightweight
controller package.

## Non-guarantees

v0.1 does not claim compatibility with:

- arbitrary upstream vLLM releases after `v0.22.1`;
- stock vLLM coordinator clients (stock vLLM has no such client);
- multiple active controller replicas;
- out-of-tree worker clients that omit process incarnation or monotonic release counters;
- Windows or macOS process management and `/proc` memory monitoring.
