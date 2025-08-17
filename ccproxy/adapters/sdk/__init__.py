"""SDK adapter models for Claude Code SDK integration.

This module provides shared SDK models that can be used by multiple plugins
without creating circular dependencies. Similar to the OpenAI adapter pattern,
these models define common data structures for SDK communication.
"""

from .models import (
    AssistantMessage,
    ContentBlock,
    ExtendedContentBlock,
    ResultMessage,
    ResultMessageBlock,
    SDKContentBlock,
    SDKMessage,
    SDKMessageContent,
    SDKMessageMode,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolResultSDKBlock,
    ToolUseBlock,
    ToolUseSDKBlock,
    UserMessage,
    convert_sdk_result_message,
    convert_sdk_system_message,
    convert_sdk_text_block,
    convert_sdk_tool_result_block,
    convert_sdk_tool_use_block,
    create_sdk_message,
    to_sdk_variant,
)


__all__ = [
    # Generic conversion
    "to_sdk_variant",
    # Content blocks
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    "ContentBlock",
    # Messages
    "UserMessage",
    "AssistantMessage",
    "SystemMessage",
    "ResultMessage",
    # SDK Query Messages
    "SDKMessageContent",
    "SDKMessage",
    "create_sdk_message",
    # Custom content blocks
    "SDKMessageMode",
    "ToolUseSDKBlock",
    "ToolResultSDKBlock",
    "ResultMessageBlock",
    "SDKContentBlock",
    "ExtendedContentBlock",
    # Conversion functions
    "convert_sdk_text_block",
    "convert_sdk_tool_use_block",
    "convert_sdk_tool_result_block",
    "convert_sdk_system_message",
    "convert_sdk_result_message",
]
