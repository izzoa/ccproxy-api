"""Codex response transformer for converting between API formats."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import json
import structlog

from ccproxy.adapters.openai.adapter import OpenAIAdapter


logger = structlog.get_logger(__name__)


class CodexResponseTransformer:
    """Transform responses between Codex and OpenAI formats.
    
    This transformer handles the conversion of responses from Codex Response API
    format to OpenAI Chat Completions format, including streaming responses.
    """

    def __init__(self) -> None:
        """Initialize the response transformer."""
        self._openai_adapter = OpenAIAdapter()
        
    def transform_codex_to_chat(
        self, 
        response_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Transform Codex Response API response to OpenAI Chat Completions format.
        
        Args:
            response_data: The Codex format response data
            
        Returns:
            Transformed response in OpenAI Chat Completions format
        """
        # Use OpenAI adapter to convert format
        return self._openai_adapter.adapt_response_to_chat(response_data)
    
    async def transform_codex_stream_to_chat(
        self,
        response_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[dict[str, Any]]:
        """Transform Codex SSE stream to OpenAI Chat Completions streaming format.
        
        Args:
            response_stream: The Codex SSE stream of bytes
            
        Yields:
            Transformed chunks in OpenAI Chat Completions format
        """
        # Use OpenAI adapter to convert streaming format
        async for chunk in self._openai_adapter.adapt_response_stream_to_chat(
            response_stream
        ):
            yield chunk
    
    def transform_error_to_chat(
        self,
        error_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Transform Codex error response to OpenAI Chat Completions error format.
        
        Args:
            error_data: The Codex error response
            
        Returns:
            Transformed error in OpenAI format
        """
        # Use OpenAI adapter's error transformation
        return self._openai_adapter.adapt_error(error_data)
    
    def validate_response(
        self,
        response_data: dict[str, Any],
    ) -> bool:
        """Validate a Codex response structure.
        
        Args:
            response_data: The response data to validate
            
        Returns:
            True if response is valid, False otherwise
        """
        # Check for required fields in Codex response
        if not isinstance(response_data, dict):
            return False
        
        # Check for error response
        if "error" in response_data:
            return True  # Error responses are valid
        
        # Check for standard response fields
        required_fields = {"id", "output"}
        if not all(field in response_data for field in required_fields):
            logger.warning(
                "codex_response_missing_fields",
                missing=required_fields - set(response_data.keys()),
                available=list(response_data.keys()),
            )
            return False
        
        return True
    
    async def process_streaming_response(
        self,
        response_stream: AsyncIterator[bytes],
        transform_to_openai: bool = False,
    ) -> AsyncIterator[bytes]:
        """Process and optionally transform a streaming response.
        
        Args:
            response_stream: The raw SSE stream
            transform_to_openai: Whether to transform to OpenAI format
            
        Yields:
            Processed/transformed stream chunks
        """
        if transform_to_openai:
            # Transform to OpenAI format
            async for chunk_dict in self.transform_codex_stream_to_chat(response_stream):
                # Convert dict to SSE format
                yield f"data: {json.dumps(chunk_dict)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        else:
            # Pass through raw SSE stream
            async for chunk in response_stream:
                yield chunk
    
    def extract_response_metadata(
        self,
        response_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract metadata from a Codex response.
        
        Args:
            response_data: The Codex response data
            
        Returns:
            Dictionary of extracted metadata
        """
        metadata = {}
        
        # Extract common fields
        if "id" in response_data:
            metadata["response_id"] = response_data["id"]
        
        if "created" in response_data:
            metadata["created_at"] = response_data["created"]
        
        if "model" in response_data:
            metadata["model"] = response_data["model"]
        
        if "output" in response_data:
            output = response_data["output"]
            if isinstance(output, str):
                metadata["output_length"] = len(output)
            elif isinstance(output, dict):
                metadata["output_type"] = "structured"
                metadata["output_keys"] = list(output.keys())
        
        if "usage" in response_data:
            metadata["usage"] = response_data["usage"]
        
        return metadata