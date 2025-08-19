"""SDK adapter models for Claude Code SDK integration - DEPRECATED.

This module is deprecated. Import from plugins.claude_sdk.models instead.
The models have been moved to the claude_sdk plugin for better plugin self-containment.
"""

import warnings


warnings.warn(
    "Importing from ccproxy.adapters.sdk is deprecated. "
    "Use plugins.claude_sdk.models instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Temporary re-exports for backward compatibility
from plugins.claude_sdk.models import (  # noqa: E402
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
