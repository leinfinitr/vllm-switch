import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from controller.config import ModelSpec


class VLLMClientError(RuntimeError):
    """Raised when a vLLM management or proxy call fails."""


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def filter_end_to_end_headers(
    headers: Mapping[str, str] | None,
    *,
    rebuilding_body: bool,
) -> dict[str, str]:
    """Drop RFC hop-by-hop headers and fields named by Connection."""
    if not headers:
        return {}
    connection_tokens: set[str] = set()
    for key, value in headers.items():
        if key.lower() == "connection":
            connection_tokens.update(token.strip().lower() for token in value.split(","))
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"host"}
    if rebuilding_body:
        # JSON is re-encoded and httpx response bytes are decoded, so original
        # representation metadata would be incorrect downstream.
        excluded |= {"content-length", "content-encoding"}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


class VLLMClient:
    """HTTP client for vLLM management endpoints and OpenAI-compatible proxying."""

    def __init__(self, models: Mapping[str, ModelSpec], timeout_s: float = 600) -> None:
        self.models = dict(models)
        self.timeout = httpx.Timeout(timeout_s, connect=30.0)
        # Backend control-plane URLs are explicit and commonly loopback/private.
        # Environment proxies can bypass test transports and misroute local vLLM
        # sleep/wake requests, so do not inherit them here.
        self._client = httpx.AsyncClient(timeout=self.timeout, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _spec(self, model: str) -> ModelSpec:
        try:
            return self.models[model]
        except KeyError as exc:
            raise VLLMClientError(f"unknown model: {model}") from exc

    async def health(self, model: str) -> bool:
        spec = self._spec(model)
        try:
            response = await self._client.get(f"{spec.backend_url}/health")
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False

    async def sleep(self, model: str, level: int) -> float:
        spec = self._spec(model)
        start = time.perf_counter()
        response = await self._request("POST", f"{spec.backend_url}/sleep", params={"level": level})
        self._raise_for_response(response, f"sleep {model}")
        return time.perf_counter() - start

    async def wake_up(self, model: str, tags: list[str] | None = None) -> float:
        spec = self._spec(model)
        start = time.perf_counter()
        params: list[tuple[str, str]] | None = None
        if tags:
            params = [("tags", tag) for tag in tags]
        response = await self._request("POST", f"{spec.backend_url}/wake_up", params=params)
        self._raise_for_response(response, f"wake_up {model}")
        return time.perf_counter() - start

    async def proxy_json(
        self,
        model: str,
        path: str,
        body: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        spec = self._spec(model)
        backend_body = {**body, "model": spec.served_model_name}
        response = await self._request(
            "POST",
            f"{spec.backend_url}{path}",
            json=backend_body,
            headers=filter_end_to_end_headers(headers, rebuilding_body=True),
        )
        return response.status_code, dict(response.headers), response.content

    @asynccontextmanager
    async def proxy_stream(
        self,
        model: str,
        path: str,
        body: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[httpx.Response]:
        spec = self._spec(model)
        backend_body = {**body, "model": spec.served_model_name}
        try:
            async with self._client.stream(
                "POST",
                f"{spec.backend_url}{path}",
                json=backend_body,
                headers=filter_end_to_end_headers(headers, rebuilding_body=True),
            ) as response:
                yield response
        except httpx.HTTPError as exc:
            raise VLLMClientError(f"vLLM proxy stream failed: {exc}") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise VLLMClientError(f"vLLM request failed: {exc}") from exc

    @staticmethod
    def _raise_for_response(response: httpx.Response, action: str) -> None:
        if not 200 <= response.status_code < 300:
            raise VLLMClientError(
                f"vLLM {action} failed with HTTP {response.status_code}: {response.text[:500]}"
            )
