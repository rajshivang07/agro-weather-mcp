"""Small async Open-Meteo client with retry and TTL caching."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class OpenMeteoClient:
    def __init__(self, timeout: float = 20.0, ttl_seconds: int = 600):
        self.http = httpx.AsyncClient(timeout=timeout)
        self.ttl_seconds = ttl_seconds
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}

    async def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        key = (url, tuple(sorted((str(k), str(v)) for k, v in params.items())))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl_seconds:
            return cached[1]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.http.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                data = response.json()
                self._cache[key] = (time.monotonic(), data)
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
        raise RuntimeError(
            f"Open-Meteo request failed after 3 attempts: {last_error}"
        ) from last_error

    async def aclose(self) -> None:
        await self.http.aclose()
