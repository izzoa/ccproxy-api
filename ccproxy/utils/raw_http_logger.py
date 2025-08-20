"""Raw HTTP logger for direct transport-level logging."""

import os
import asyncio
from pathlib import Path
from typing import Optional
import aiofiles
from datetime import datetime


class RawHTTPLogger:
    """Direct logger for raw HTTP data without buffering."""
    
    def __init__(self):
        self.enabled = os.getenv("CCPROXY_LOG_RAW_HTTP", "").lower() == "true"
        self.log_dir = Path(os.getenv("CCPROXY_RAW_LOG_DIR", "/tmp/ccproxy/raw"))
        
        if self.enabled:
            # Create log directory if it doesn't exist
            self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def should_log(self) -> bool:
        """Check if logging is enabled."""
        return self.enabled
    
    async def log_client_request(self, request_id: str, raw_data: bytes):
        """Log raw client request data."""
        if not self.enabled:
            return
        
        file_path = self.log_dir / f"{request_id}_client_request.http"
        async with aiofiles.open(file_path, 'ab') as f:
            await f.write(raw_data)
    
    async def log_client_response(self, request_id: str, raw_data: bytes):
        """Log raw client response data."""
        if not self.enabled:
            return
        
        file_path = self.log_dir / f"{request_id}_client_response.http"
        async with aiofiles.open(file_path, 'ab') as f:
            await f.write(raw_data)
    
    async def log_provider_request(self, request_id: str, raw_data: bytes):
        """Log raw provider request data."""
        if not self.enabled:
            return
        
        file_path = self.log_dir / f"{request_id}_provider_request.http"
        async with aiofiles.open(file_path, 'ab') as f:
            await f.write(raw_data)
    
    async def log_provider_response(self, request_id: str, raw_data: bytes):
        """Log raw provider response data."""
        if not self.enabled:
            return
        
        file_path = self.log_dir / f"{request_id}_provider_response.http"
        async with aiofiles.open(file_path, 'ab') as f:
            await f.write(raw_data)
    
    def build_raw_request(self, method: str, url: str, headers: list, body: Optional[bytes] = None) -> bytes:
        """Build raw HTTP/1.1 request format."""
        # Parse URL to get path
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        
        # Build request line
        lines = [f"{method} {path} HTTP/1.1"]
        
        # Add Host header if not present
        has_host = any(h[0].lower() == b'host' for h in headers)
        if not has_host and parsed.netloc:
            lines.append(f"Host: {parsed.netloc}")
        
        # Add headers
        for name, value in headers:
            if isinstance(name, bytes):
                name = name.decode('ascii', errors='ignore')
            if isinstance(value, bytes):
                value = value.decode('ascii', errors='ignore')
            lines.append(f"{name}: {value}")
        
        # Build raw request
        raw = "\r\n".join(lines).encode('utf-8')
        raw += b"\r\n\r\n"
        
        # Add body if present
        if body:
            raw += body
        
        return raw
    
    def build_raw_response(self, status_code: int, headers: list, reason: str = "OK") -> bytes:
        """Build raw HTTP/1.1 response headers."""
        # Build status line
        lines = [f"HTTP/1.1 {status_code} {reason}"]
        
        # Add headers
        for name, value in headers:
            if isinstance(name, bytes):
                name = name.decode('ascii', errors='ignore')
            if isinstance(value, bytes):
                value = value.decode('ascii', errors='ignore')
            lines.append(f"{name}: {value}")
        
        # Build raw response headers
        raw = "\r\n".join(lines).encode('utf-8')
        raw += b"\r\n\r\n"
        
        return raw