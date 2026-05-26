import argparse
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from controller.config import ControllerConfig, load_config
from controller.metrics import MetricsRecorder
from controller.policies import make_policy
from controller.router import make_router
from controller.state import ControllerState
from controller.vllm_client import VLLMClient


def create_app(config: ControllerConfig) -> FastAPI:
    state = ControllerState.from_models(
        list(config.models), config.controller.startup_awake_model
    )
    policy = make_policy(config.controller.policy)
    vllm_client = VLLMClient(config.models, timeout_s=config.controller.request_timeout_s)
    metrics_recorder = MetricsRecorder(config.controller.metrics_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await vllm_client.aclose()

    app = FastAPI(title="vLLM Model Switch Controller", lifespan=lifespan)
    app.state.controller_config = config
    app.state.controller_state = state
    app.state.vllm_client = vllm_client
    app.include_router(make_router(config, state, policy, vllm_client, metrics_recorder))
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the vLLM model switch controller")
    parser.add_argument("--config", default="configs/models.example.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=config.controller.host, port=config.controller.port)


if __name__ == "__main__":
    main()
