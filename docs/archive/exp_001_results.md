# 实验 001：vLLM Sleep 控制器 A/B 交替

> 历史归档：本报告记录 2026-05-26 的第一阶段基线。环境、路径和命令不代表当前推荐配置；当前运行方式见 [`../operations.md`](../operations.md)。

日期：2026-05-26

## 目标

在真实 GPU 上使用 `Always sleep previous` 策略验证第一阶段外部 vLLM 模型切换控制器，并收集第一批端到端指标。

## 环境

- 主机 GPU：NVIDIA GeForce RTX 3080，10 GiB
- vLLM：0.21.0，安装在项目 `.venv` 中
- PyTorch：2.11.0+cu130
- torch 报告的 CUDA runtime：13.0
- `CUDA_HOME`：`/home/ljl/cuda-13.0`
- 模型 A：`/home/ljl/models/hf/Qwen2.5-0.5B-Instruct`，以 `qwen-a` 对外服务
- 模型 B：硬链接副本 `/home/ljl/models/hf/Qwen2.5-0.5B-Instruct-B`，以 `qwen-b` 对外服务

说明：尝试拉取第二个不同的 HF 模型时网络不可用，因此这个冒烟实验使用两个不同的 vLLM 进程，它们基于相同的 Qwen2.5-0.5B-Instruct 权重，但使用不同的本地路径和模型名称。这仍然可以验证进程级 sleep/wake 切换和控制器开销；后续对比应使用不同的模型家族或规模。

## 配置

- 控制器配置：`configs/archive/exp_001/models.yaml`
- 工作负载配置：`configs/archive/exp_001/ab_alternating.yaml`
- 模式：`qwen-a, qwen-b, ...` 交替
- 请求数：10
- 请求速率：0.2 req/s
- 最大输出 token 数：32
- 流式输出：启用
- vLLM `--gpu-memory-utilization`：每个后端 0.35
- vLLM `--max-model-len`：1024
- Sleep level：1

## 命令

```bash
PYTHONPATH=. uv run python scripts/launch_vllm_pool.py \
  --config configs/archive/exp_001/models.yaml \
  --pid-file pids.exp001.json \
  > results/exp_001/launch.log 2>&1

PYTHONPATH=. uv run python -m controller.main \
  --config configs/archive/exp_001/models.yaml \
  > results/exp_001/controller.log 2>&1

uv run python scripts/collect_gpu_metrics.py \
  --output results/exp_001/gpu.csv \
  --interval-s 1 \
  --duration-s 120 \
  > results/exp_001/gpu_metrics.log 2>&1

PYTHONPATH=. uv run python benchmarks/run_workload.py \
  --config configs/archive/exp_001/ab_alternating.yaml \
  --base-url http://127.0.0.1:9000 \
  --output results/exp_001/client.jsonl

PYTHONPATH=. uv run python benchmarks/analyze_results.py \
  --input results/exp_001/controller_events.jsonl \
  --output results/exp_001/controller_summary.json
```

## 结果

### 控制器侧指标

来自 `results/exp_001/controller_summary.json`：

| 指标 | 数量 | 平均 ms | P50 ms | P95 ms | 最小 ms | 最大 ms |
|---|---:|---:|---:|---:|---:|---:|
| E2E TTFT | 10 | 303.97 | 218.04 | 677.86 | 214.01 | 908.85 |
| E2E 延迟 | 10 | 378.12 | 294.97 | 748.65 | 286.31 | 977.58 |
| 切换延迟 | 9 | 243.54 | 207.60 | 407.83 | 203.90 | 540.88 |
| Sleep 延迟 | 9 | 123.82 | 87.74 | 282.62 | 87.42 | 412.18 |
| Wake 延迟 | 9 | 119.71 | 120.11 | 125.45 | 116.02 | 128.68 |
| Wake 后后端 TTFT | 10 | 84.78 | 10.71 | 383.12 | 10.01 | 395.52 |

共有 10/10 个请求成功，错误数为 0。

### 热切换稳态

第一个发生切换的请求较慢，因为它从启动时已唤醒的 `qwen-a` 切换到启动后已经睡眠的 `qwen-b`。之后切换延迟稳定在约 204-208 ms：

- 稳态 sleep：约 87-88 ms
- 稳态 wake：约 116-121 ms
- 稳态切换：约 204-208 ms
- 稳态 E2E TTFT：约 214-219 ms
- 稳态 E2E 延迟：约 286-296 ms

### 启动观察

来自 `results/exp_001/launch.log`：

- vLLM A 初始化引擎耗时 54.79 s，其中编译耗时 6.81 s。
- vLLM B 初始化引擎耗时 13.30 s，其中编译耗时 6.89 s。
- B 的初始 sleep 释放了 2.96 GiB GPU 显存，其中 0.98 GiB 备份到 CPU，其余被丢弃。
- B 的初始 sleep 耗时 0.417 s。

第一个服务的初始化明显慢于第二个服务，可能是由首次 CUDA graph、编译或缓存预热效应导致的。

### GPU 指标

来自 `results/exp_001/gpu.csv` 汇总：

- 采样数：80
- GPU 显存使用量：最小 4104 MB，平均 4493 MB，最大 4854 MB
- GPU 利用率：最小 0%，平均 1.2%，最大 73%

实验结束时，控制器状态为：

```json
{"active_model":"qwen-b","states":{"qwen-a":"sleeping","qwen-b":"awake"}}
```

`nvidia-smi` 显示：

```text
qwen-a EngineCore: ~898 MB
qwen-b EngineCore: ~3484 MB
```

这符合预期的 level-1 行为：睡眠中的进程仍然持有部分 GPU/runtime memory，但会释放大部分模型/KV memory。

## 解读

对于 RTX 3080 上这对 0.5B 模型，vLLM level-1 sleep/wake 切换的稳态切换成本约为 205 ms。由于 wake 后的后端 TTFT 在稳态下只有约 10 ms，交替工作负载中大部分用户可见 TTFT 来自控制器触发的 sleep/wake，而不是生成路径本身。

这验证了第一阶段控制器路径，并为后续对比提供了初始基线：

- 专用服务应能消除约 205 ms 的切换惩罚，但需要两个模型同时驻留。
- 冷重载应慢得多，可能达到数秒到数十秒，具体取决于编译和缓存状态。
- 更高级的策略应致力于减少切换次数，或重叠/隐藏 sleep/wake 成本。

## 产物

- `results/exp_001/launch.log`
- `results/exp_001/controller.log`
- `results/exp_001/controller_events.jsonl`
- `results/exp_001/controller_summary.json`
- `results/exp_001/client.jsonl`
- `results/exp_001/client_summary.json`
- `results/exp_001/gpu.csv`
