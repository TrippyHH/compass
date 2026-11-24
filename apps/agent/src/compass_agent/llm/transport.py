"""Request-owned HTTP resources and bounded stream readers.

Only this module knows about HTTPX transports, decompression and SSE framing. The
rest of the kernel sees CallResources and the two stream wrappers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from .errors import RuntimeErrorCode, RuntimeFailure


class AsyncClosable(Protocol):
    async def aclose(self) -> None:
        """Release a request-owned response or client."""


class CallResources:
    """Everything one call owns. Never shared between calls."""

    __slots__ = (
        "sent",
        "has_partial",
        "saw_done",
        "clients",
        "total_bytes",
        "error_code",
        "wire_finish_reason",
        "wire_response_model",
        "wire_request_id",
    )

    def __init__(self) -> None:
        self.sent = False
        self.has_partial = False
        self.saw_done = False
        self.clients: list[AsyncClosable] = []
        self.total_bytes = 0
        self.error_code: RuntimeErrorCode | None = None
        self.wire_finish_reason: str | None = None
        self.wire_response_model: str | None = None
        self.wire_request_id: str | None = None

    def mark_sent(self) -> None:
        self.sent = True

    def mark_partial(self) -> None:
        self.sent = True
        self.has_partial = True

    def register(self, client: AsyncClosable) -> None:
        self.clients.append(client)

    def record_bytes(self, size: int, limit: int) -> None:
        self.total_bytes += size
        if self.total_bytes > limit:
            raise RuntimeFailure("response_too_large")

    async def close(self) -> None:
        # Runtime bounds the complete cleanup operation to two seconds.
        for client in reversed(self.clients):
            try:
                async with asyncio.timeout(1):
                    await client.aclose()
            except Exception:
                continue
        self.clients.clear()


class SingleAttemptTransport(httpx.AsyncBaseTransport):
    """Blocks a second physical request from one prepared call."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = transport or httpx.AsyncHTTPTransport(retries=0)
        self.used = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.used:
            raise RuntimeFailure("internal_error")
        self.used = True
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class DecodedResponseStream:
    """Byte-bounded view over a decoded HTTPX response stream."""

    def __init__(
        self,
        response: httpx.Response,
        resources: CallResources,
        limit: int,
    ) -> None:
        self._response = response
        self._resources = resources
        self._limit = limit

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_bytes():
                self._resources.record_bytes(len(chunk), self._limit)
                yield chunk
        except httpx.HTTPError as exc:
            raise RuntimeFailure("stream_interrupted") from exc

    async def aclose(self) -> None:
        await self._response.aclose()


class ObservedStream:
    """SSE frame reader. Only a standalone DONE frame ends the stream normally."""

    def __init__(self, lines: AsyncIterator[str], resources: CallResources, limit: int) -> None:
        self._lines = lines
        self._resources = resources
        self._limit = limit

    async def aiter_events(self) -> AsyncIterator[str]:
        data: list[str] = []
        try:
            async for line in self._lines:
                self._resources.record_bytes(len(line.encode("utf-8")), self._limit)
                if not line:
                    if not data:
                        continue
                    payload = "\n".join(data)
                    data.clear()
                    if payload == "[DONE]":
                        self._resources.saw_done = True
                        yield payload
                        return
                    yield payload
                    continue
                if line.startswith(":") or line.startswith("event:"):
                    continue
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                yield "\n".join(data)
        except httpx.HTTPError as exc:
            raise RuntimeFailure("stream_interrupted") from exc
