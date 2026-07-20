from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


def process_rss_bytes(pid: int) -> int:
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return 0


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable missing from /proc/meminfo")


def snapshot(stats: dict[str, Any]) -> dict[str, Any]:
    clients = stats["clients"]
    return {
        "captured_at": time.time(),
        "memavailable_bytes": mem_available_bytes(),
        "clients": {
            client_id: {
                "pid": client["pid"],
                "rss_bytes": process_rss_bytes(int(client["pid"])),
                "total_bytes": client["total_bytes"],
                "released_bytes_total": client["released_bytes_total"],
                "required_for_restore_bytes": client["required_for_restore_bytes"],
                "cache_only_bytes": client["cache_only_bytes"],
                "requested_release_bytes_total": client["requested_release_bytes_total"],
            }
            for client_id, client in clients.items()
        },
        "pool_stats": stats["stats"],
        "memory_pressure": stats.get("memory_pressure"),
    }


async def switch(client: httpx.AsyncClient, base_url: str, model: str) -> float:
    started = time.perf_counter()
    response = await client.post(
        f"{base_url}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Say ok"}],
            "max_tokens": 8,
            "temperature": 0,
        },
    )
    response.raise_for_status()
    return time.perf_counter() - started


async def stats(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    response = await client.get(f"{base_url}/admin/cpu-backup/stats")
    response.raise_for_status()
    return response.json()


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Validate controller CPU backup release")
    parser.add_argument("--base-url", default="http://127.0.0.1:9000")
    parser.add_argument("--mode", choices=("retain", "release"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-a", default="qwen-1.5b")
    parser.add_argument("--model-b", default="qwen-3b")
    parser.add_argument("--observe-s", type=float, default=3)
    args = parser.parse_args()
    async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
        latencies = []
        sequence = [args.model_a, args.model_b, args.model_a, args.model_b, args.model_a]
        for model in sequence:
            latencies.append(
                {"model": model, "latency_s": await switch(client, args.base_url, model)}
            )
        before_stats = await stats(client, args.base_url)
        before = snapshot(before_stats)
        release_response = None
        if args.mode == "release":
            candidate_id, candidate = next(
                (
                    item
                    for item in before_stats["clients"].items()
                    if item[1]["cache_only_bytes"] > 0
                ),
                (None, None),
            )
            if candidate_id is None or candidate is None:
                raise RuntimeError("no cache-only backup available for controlled release")
            response = await client.post(
                f"{args.base_url}/admin/cpu-backup/release",
                json={
                    "client_id": candidate_id,
                    "target_free_bytes": candidate["cache_only_bytes"],
                },
            )
            response.raise_for_status()
            release_response = response.json()
        await asyncio.sleep(args.observe_s)
        after = snapshot(await stats(client, args.base_url))
    result = {
        "mode": args.mode,
        "switches": latencies,
        "before": before,
        "release_response": release_response,
        "after": after,
        "memavailable_delta_bytes": after["memavailable_bytes"] - before["memavailable_bytes"],
        "client_rss_delta_bytes": {
            client_id: after["clients"].get(client_id, {}).get("rss_bytes", 0) - client["rss_bytes"]
            for client_id, client in before["clients"].items()
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main_async())
