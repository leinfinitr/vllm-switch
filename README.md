# vLLM Model Switch Controller

External model switching controller for evaluating vLLM Sleep Mode as a multi-model serving baseline.

## First-stage goal

Provide one OpenAI-compatible endpoint in front of multiple single-model vLLM server processes. The first policy is **Always sleep previous**:

1. Route request by the OpenAI `model` field.
2. If target model differs from the active model, sleep the previous active vLLM server.
3. Wake the target vLLM server.
4. Forward the request and record switch/TTFT/E2E metrics.

This project is intentionally outside vLLM for the first stage, so it can compare e2e behavior with cold reload, dedicated serving, SwapServeLLM, and ServerlessLLM.

## Quick start

```bash
cd /home/ljl/research-systems/vllm-model-switch-controller
uv sync --dev
uv run pytest
```

See `docs/implementation_plan.md` and `docs/first_stage_usage.md`.
