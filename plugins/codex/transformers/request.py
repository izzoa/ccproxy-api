"""Codex request transformer - headers and auth only."""

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class CodexRequestTransformer:
    """Transform requests for Codex API.
    
    Handles:
    - Header transformation and auth injection
    - Session ID header addition
    - Codex CLI headers injection from detection service
    - Minimal instructions field injection
    """
    
    def __init__(self, detection_service: Any | None = None):
        """Initialize the request transformer.
        
        Args:
            detection_service: CodexDetectionService for header/instructions injection
        """
        self.detection_service = detection_service
    
    def transform_headers(
        self, 
        headers: dict[str, str],
        session_id: str,
        auth_token: str | None = None
    ) -> dict[str, str]:
        """Transform request headers for Codex API.
        
        Args:
            headers: Original request headers
            session_id: Codex session ID
            auth_token: Optional Bearer token for authorization
            
        Returns:
            Transformed headers with Codex-specific headers
        """
        transformed = headers.copy()
        
        # Remove hop-by-hop headers
        hop_by_hop = {
            "host", "connection", "keep-alive", "transfer-encoding",
            "content-length", "upgrade", "proxy-authenticate",
            "proxy-authorization", "te", "trailer"
        }
        transformed = {
            k: v for k, v in transformed.items() 
            if k.lower() not in hop_by_hop
        }
        
        # Add session ID
        transformed["session_id"] = session_id
        
        # Add authorization if provided
        if auth_token:
            # Remove any existing authorization headers and add the JWT token
            transformed.pop("authorization", None)  # Remove lowercase variant
            transformed.pop("Authorization", None)   # Remove capitalized variant  
            transformed["Authorization"] = f"Bearer {auth_token}"
            logger.info("codex_auth_token_added", token_preview=f"{auth_token[:20]}..." if len(auth_token) > 20 else auth_token)
        
        # Inject detected Codex CLI headers if available
        if self.detection_service:
            cached_data = self.detection_service.get_cached_data()
            if cached_data and cached_data.headers:
                detected_headers = cached_data.headers.to_headers_dict()
                logger.debug(
                    "injecting_detected_codex_headers",
                    version=cached_data.codex_version,
                    header_count=len(detected_headers)
                )
                # Override with detected headers (except session_id)
                for key, value in detected_headers.items():
                    if key.lower() != "session_id":
                        transformed[key] = value
        else:
            # Fallback headers
            transformed.update({
                "originator": "codex_cli_rs",
                "openai-beta": "responses=experimental",
                "version": "0.21.0",
            })
        
        # Set standard headers
        if "content-type" not in [k.lower() for k in transformed]:
            transformed["Content-Type"] = "application/json"
        if "accept" not in [k.lower() for k in transformed]:
            transformed["Accept"] = "application/json"
        
        logger.info("codex_headers_final", headers=dict(transformed), session_id=session_id)
        return transformed
    
    def transform_body(self, body: bytes | None) -> bytes | None:
        """Minimal body transformation - inject instructions if missing.
        
        Args:
            body: Original request body
            
        Returns:
            Body with instructions injected if needed
        """
        logger.debug("transform_body_called", body_length=len(body) if body else 0)
        if not body:
            return body
        
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("body_decode_failed", error=str(e))
            return body
        
        # Only inject instructions if missing or None
        if "instructions" not in data or data.get("instructions") is None:
            instructions = self._get_instructions()
            if instructions:
                data["instructions"] = instructions
                logger.debug("injected_codex_instructions", instructions_preview=f"{instructions[:50]}...")
            else:
                logger.warning("no_codex_instructions_available")
        
        return json.dumps(data).encode("utf-8")
    
    def _get_instructions(self) -> str | None:
        """Get Codex instructions from detection service or fallback."""
        if self.detection_service:
            cached_data = self.detection_service.get_cached_data()
            if cached_data and cached_data.instructions:
                return cached_data.instructions.instructions_field
        
        # Fallback instructions
        return (
            "You are a coding agent running in the Codex CLI, a terminal-based coding assistant. "
            "Codex CLI is an open source project led by OpenAI."
        )