import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from benchmarks.workload_schema import WorkloadConfig, generate_model_sequence, load_workload


async def run_workload(config: WorkloadConfig, output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    interval_s = 1.0 / config.request_rate
    models = generate_model_sequence(config)

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
        with output.open("w", encoding="utf-8") as f:
            for idx, model in enumerate(models):
                start = time.perf_counter()
                record = await send_one(client, config, model, idx)
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                elapsed = time.perf_counter() - start
                if idx != len(models) - 1 and elapsed < interval_s:
                    await asyncio.sleep(interval_s - elapsed)


async def send_one(
    client: httpx.AsyncClient, config: WorkloadConfig, model: str, idx: int
) -> dict[str, Any]:
    request_start = time.perf_counter()
    first_chunk_ts = None
    output_bytes = 0
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": config.prompt.text}],
        "max_tokens": config.output.max_tokens,
        "stream": config.stream,
    }
    record: dict[str, Any] = {
        "request_index": idx,
        "model": model,
        "start_unix_s": time.time(),
        "stream": config.stream,
    }
    try:
        if config.stream:
            async with client.stream(
                "POST", f"{config.base_url}/v1/chat/completions", json=payload
            ) as response:
                record["status_code"] = response.status_code
                async for chunk in response.aiter_bytes():
                    if chunk and first_chunk_ts is None:
                        first_chunk_ts = time.perf_counter()
                    output_bytes += len(chunk)
        else:
            response = await client.post(f"{config.base_url}/v1/chat/completions", json=payload)
            record["status_code"] = response.status_code
            output_bytes = len(response.content)
            first_chunk_ts = time.perf_counter()
        end = time.perf_counter()
        record["e2e_ttft_ms"] = (
            (first_chunk_ts - request_start) * 1000 if first_chunk_ts is not None else None
        )
        record["e2e_latency_ms"] = (end - request_start) * 1000
        record["output_bytes"] = output_bytes
        record["error"] = None
    except Exception as exc:  # benchmark should keep trace on individual failures
        record["status_code"] = None
        record["e2e_ttft_ms"] = None
        record["e2e_latency_ms"] = (time.perf_counter() - request_start) * 1000
        record["output_bytes"] = output_bytes
        record["error"] = repr(exc)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a multi-model workload against controller")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()
    config = load_workload(args.config)
    if args.base_url:
        config = config.model_copy(update={"base_url": args.base_url})
    asyncio.run(run_workload(config, args.output))


if __name__ == "__main__":
    main()
