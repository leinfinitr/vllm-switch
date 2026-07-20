import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx


async def send_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    max_tokens: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token_ms: float | None = None
    content = ""
    async with client.stream(
        "POST",
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Count upward briefly."}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
        },
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            choice = (event.get("choices") or [{}])[0]
            piece = (choice.get("delta") or {}).get("content") or choice.get("text") or ""
            if piece and first_token_ms is None:
                first_token_ms = (time.perf_counter() - started) * 1000
            content += piece
    return {
        "model": model,
        "status": 200,
        "first_token_ms": first_token_ms,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "content": content,
    }


async def run_smoke(base_url: str, models: list[str]) -> list[dict[str, Any]]:
    if len(models) != 2:
        raise ValueError("exactly two models are required")
    async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
        records = [
            await send_chat(client, base_url=base_url, model=model, max_tokens=16)
            for model in [models[0], models[1], models[1], models[0]]
        ]

        long_a = asyncio.create_task(
            send_chat(client, base_url=base_url, model=models[0], max_tokens=160)
        )
        await asyncio.sleep(0.1)
        short_b = asyncio.create_task(
            send_chat(client, base_url=base_url, model=models[1], max_tokens=16)
        )
        drain_a, drain_b = await asyncio.gather(long_a, short_b)
        records.extend([{**drain_a, "scenario": "drain-a"}, {**drain_b, "scenario": "drain-b"}])

        state = (await client.get(f"{base_url}/admin/state")).json()
        if state.get("active_requests"):
            raise RuntimeError(f"request reservation leaked: {state['active_requests']}")
    return records


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test request-driven OpenAI model switching")
    parser.add_argument("--base-url", default="http://127.0.0.1:9000")
    parser.add_argument("--models", nargs=2, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    records = await run_smoke(args.base_url.rstrip("/"), args.models)
    write_jsonl(args.output, records)
    print(json.dumps({"ok": True, "requests": len(records), "output": args.output}))


if __name__ == "__main__":
    asyncio.run(main_async())