"""HTTPX transport wrapper for logging raw HTTP data."""

import httpx
from typing import AsyncIterator, Optional
from httpx._transports.default import AsyncByteStream
from .logger import RawHTTPLogger
import structlog

logger = structlog.get_logger(__name__)


class LoggingResponseStream(AsyncByteStream):
    """Wraps response stream to log chunks without buffering."""
    
    def __init__(
        self,
        stream: AsyncByteStream,
        logger: RawHTTPLogger,
        request_id: str,
        status_code: int,
        headers: httpx.Headers
    ):
        self.stream = stream
        self.logger = logger
        self.request_id = request_id
        self.status_code = status_code
        self.headers = headers
        self._logged_headers = False
    
    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Iterate over response chunks with logging."""
        # Build and log response headers first
        if not self._logged_headers:
            header_list = [(k.encode(), v.encode()) for k, v in self.headers.items()]
            raw_headers = self.logger.build_raw_response(self.status_code, header_list)
            await self.logger.log_provider_response(self.request_id, raw_headers)
            self._logged_headers = True
        
        # Stream and log chunks
        async for chunk in self.stream:
            # Log each chunk as it arrives
            if chunk:
                await self.logger.log_provider_response(self.request_id, chunk)
            
            # Yield chunk unchanged (no buffering)
            yield chunk
    
    async def aclose(self) -> None:
        """Close the underlying stream."""
        if hasattr(self.stream, 'aclose'):
            await self.stream.aclose()


class LoggingHTTPTransport(httpx.AsyncHTTPTransport):
    """Wraps HTTPX transport to log raw HTTP data without buffering."""
    
    def __init__(self, wrapped_transport: Optional[httpx.AsyncHTTPTransport] = None, logger: Optional[RawHTTPLogger] = None):
        self.wrapped = wrapped_transport or httpx.AsyncHTTPTransport()
        self.logger = logger or RawHTTPLogger()
        # Delegate pool to wrapped transport for context manager support
        if hasattr(self.wrapped, '_pool'):
            self._pool = self.wrapped._pool
        logger_module = structlog.get_logger(__name__)
        logger_module.info("LoggingHTTPTransport initialized", enabled=self.logger.enabled)
    
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle async request with raw logging."""
        # Extract request ID from extensions if available
        request_id = self._get_request_id(request)
        
        # Only log if we have a request ID and logging is enabled
        if request_id and self.logger.should_log():
            raw_request = self._build_raw_request(request)
            await self.logger.log_provider_request(request_id, raw_request)
        
        # Forward request to wrapped transport
        response = await self.wrapped.handle_async_request(request)
        
        # Wrap response stream for logging only if we have request ID
        if request_id and self.logger.should_log():
            response.stream = LoggingResponseStream(
                response.stream, 
                self.logger,
                request_id,
                response.status_code,
                response.headers
            )
        
        return response
    
    def _get_request_id(self, request: httpx.Request) -> str | None:
        """Extract request ID from extensions if available."""
        # Try to get from extensions (passed by plugin_handler)
        if hasattr(request, 'extensions') and 'request_id' in request.extensions:
            return request.extensions['request_id']
        
        # Log warning and return None - we won't log this request
        logger.warning(
            "provider_request_missing_request_id",
            url=str(request.url),
            method=request.method,
            message="Skipping raw HTTP logging for this request"
        )
        return None
    
    def _build_raw_request(self, request: httpx.Request) -> bytes:
        """Build raw HTTP/1.1 request format."""
        # Convert httpx headers to list of tuples
        headers = [(k.encode(), v.encode()) for k, v in request.headers.items()]
        
        # Filter sensitive headers if configured
        if hasattr(self.logger, 'config') and self.logger.config:
            exclude_headers = [h.lower() for h in self.logger.config.exclude_headers]
            filtered_headers = []
            for name, value in headers:
                if name.decode().lower() in exclude_headers:
                    filtered_headers.append((name, b"[REDACTED]"))
                else:
                    filtered_headers.append((name, value))
            headers = filtered_headers
        
        # Get request body
        body = None
        if request.content:
            body = request.content
        elif hasattr(request, 'stream') and request.stream:
            # For streaming requests, we'd need to buffer which we want to avoid
            # Log a placeholder for now
            body = b"[STREAMING BODY - NOT CAPTURED]"
        
        return self.logger.build_raw_request(
            method=request.method,
            url=str(request.url),
            headers=headers,
            body=body
        )
    
    async def aclose(self) -> None:
        """Close the transport."""
        if hasattr(self.wrapped, 'aclose'):
            await self.wrapped.aclose()
    
    async def __aenter__(self):
        """Enter async context."""
        await self.wrapped.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context."""
        await self.wrapped.__aexit__(exc_type, exc_val, exc_tb)