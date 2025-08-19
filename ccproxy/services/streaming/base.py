"""Base streaming handler abstractions."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

from ccproxy.services.handler_config import HandlerConfig


class BaseStreamingHandler(ABC):
    """Abstract base class for streaming handlers."""

    @abstractmethod
    async def handle_streaming_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        handler_config: HandlerConfig,
        request_context: Any | None = None,
        **kwargs: Any,
    ) -> StreamingResponse:
        """Handle a streaming request.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body
            handler_config: Handler configuration
            request_context: Optional request context
            **kwargs: Additional handler-specific arguments

        Returns:
            StreamingResponse
        """
        pass

    @abstractmethod
    async def stream_generator(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        handler_config: HandlerConfig,
        **kwargs: Any,
    ) -> AsyncGenerator[bytes, None]:
        """Generate streaming response chunks.

        Args:
            method: HTTP method
            url: Target URL
            headers: Request headers
            body: Request body
            handler_config: Handler configuration
            **kwargs: Additional generator arguments

        Yields:
            Response chunks as bytes
        """
        pass

    async def process_sse_events(
        self,
        raw_stream: AsyncIterator[bytes],
        adapter: Any | None = None,
    ) -> AsyncIterator[bytes]:
        """Process Server-Sent Events from a raw stream.

        Args:
            raw_stream: Raw bytes stream
            adapter: Optional adapter for format conversion

        Yields:
            Processed SSE events
        """
        async for chunk in raw_stream:
            yield chunk

    def should_stream_response(self, headers: dict[str, str]) -> bool:
        """Check if response should be streamed based on headers.

        Args:
            headers: Request headers

        Returns:
            True if streaming is requested
        """
        accept_header = headers.get("accept", "").lower()
        return "text/event-stream" in accept_header or "stream" in accept_header
