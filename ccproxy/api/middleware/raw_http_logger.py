"""ASGI middleware for logging raw HTTP data from client requests."""

from typing import Callable, Awaitable
from ccproxy.utils.raw_http_logger import RawHTTPLogger


class RawHTTPLoggingMiddleware:
    """ASGI middleware to log raw HTTP data without buffering."""
    
    def __init__(self, app: Callable):
        self.app = app
        self.logger = RawHTTPLogger()
    
    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Process ASGI request with raw logging."""
        # Only handle HTTP requests
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        
        # Skip if logging is disabled
        if not self.logger.should_log():
            return await self.app(scope, receive, send)
        
        # Extract request ID from headers or generate one
        request_id = self._get_request_id(scope)
        
        # Log initial request headers
        await self._log_request_headers(scope, request_id)
        
        # Wrap receive to capture request body chunks
        wrapped_receive = self._wrap_receive(receive, request_id)
        
        # Wrap send to capture response chunks
        wrapped_send = self._wrap_send(send, request_id)
        
        # Forward to app
        await self.app(scope, wrapped_receive, wrapped_send)
    
    def _get_request_id(self, scope: dict) -> str:
        """Extract request ID from ASGI scope or headers."""
        # First check ASGI extensions (set by RequestIDMiddleware)
        if "extensions" in scope and "request_id" in scope["extensions"]:
            return scope["extensions"]["request_id"]
        
        # Fallback: Look for request ID in headers
        headers = dict(scope.get("headers", []))
        for header_name in [b'x-request-id', b'x-correlation-id']:
            if header_name in headers:
                return headers[header_name].decode('utf-8')
        
        # Last resort: Generate a UUID (consistent with RequestIDMiddleware)
        import uuid
        return str(uuid.uuid4())
    
    async def _log_request_headers(self, scope: dict, request_id: str) -> None:
        """Log the initial request headers."""
        # Build raw request line
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        query_string = scope.get("query_string", b"")
        
        if query_string:
            full_path = f"{path}?{query_string.decode('utf-8')}"
        else:
            full_path = path
        
        # Build raw request headers
        lines = [f"{method} {full_path} HTTP/1.1"]
        
        # Add headers
        for name, value in scope.get("headers", []):
            lines.append(f"{name.decode('ascii')}: {value.decode('ascii', errors='ignore')}")
        
        # Build raw request
        raw = "\r\n".join(lines).encode('utf-8')
        raw += b"\r\n\r\n"
        
        await self.logger.log_client_request(request_id, raw)
    
    def _wrap_receive(self, receive: Callable, request_id: str) -> Callable:
        """Wrap receive to capture request body chunks."""
        async def wrapped() -> dict:
            message = await receive()
            
            # Log request body chunks
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if body:
                    await self.logger.log_client_request(request_id, body)
            
            return message
        
        return wrapped
    
    def _wrap_send(self, send: Callable, request_id: str) -> Callable:
        """Wrap send to capture response chunks."""
        logged_headers = False
        
        async def wrapped(message: dict) -> None:
            nonlocal logged_headers
            
            if message["type"] == "http.response.start":
                # Log response headers
                status = message.get("status", 200)
                headers = message.get("headers", [])
                
                # Build raw response headers
                lines = [f"HTTP/1.1 {status} OK"]
                for name, value in headers:
                    lines.append(f"{name.decode('ascii')}: {value.decode('ascii', errors='ignore')}")
                
                raw = "\r\n".join(lines).encode('utf-8')
                raw += b"\r\n\r\n"
                
                await self.logger.log_client_response(request_id, raw)
                logged_headers = True
            
            elif message["type"] == "http.response.body":
                # Log response body chunks
                body = message.get("body", b"")
                if body:
                    await self.logger.log_client_response(request_id, body)
            
            # Forward message
            await send(message)
        
        return wrapped