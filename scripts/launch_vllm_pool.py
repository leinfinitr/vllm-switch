import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from controller.config import load_config


async def wait_health(url: str, timeout_s: float = 600) -> None:
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(timeout=10) as client:
        while time.time() < deadline:
            try:
                response = await client.get(f"{url}/health")
                if response.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise TimeoutError(f"backend did not become healthy: {url}")


async def post(url: str, path: str, params: dict | None = None) -> None:
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{url}{path}", params=params)
        response.raise_for_status()


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Launch configured vLLM pool sequentially")
    parser.add_argument("--config", default="configs/models.example.yaml")
    parser.add_argument("--pid-file", default="pids.json")
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Only sleep/wake already running servers",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    pids: dict[str, int] = {}

    for name, spec in config.models.items():
        if spec.launch_command and not args.skip_launch:
            env = os.environ.copy()
            env.update(spec.env)
            env.setdefault("VLLM_SERVER_DEV_MODE", "1")
            process = subprocess.Popen(spec.launch_command, env=env)
            pids[name] = process.pid
            print(f"launched {name} pid={process.pid}")
        else:
            print(f"using existing backend for {name}: {spec.backend_url}")
        await wait_health(spec.backend_url, timeout_s=config.controller.switch_timeout_s)
        if name != config.controller.startup_awake_model:
            print(f"sleeping {name}")
            await post(spec.backend_url, "/sleep", {"level": spec.sleep_level})

    startup = config.controller.startup_awake_model
    if startup:
        print(f"waking startup model {startup}")
        await post(config.models[startup].backend_url, "/wake_up")

    Path(args.pid_file).write_text(json.dumps(pids, indent=2), encoding="utf-8")
    print(f"wrote pid file {args.pid_file}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
