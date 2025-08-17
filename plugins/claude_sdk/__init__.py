"""Claude SDK integration module."""

from ccproxy.adapters.sdk import (
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

from .client import ClaudeSDKClient
from .converter import MessageConverter
from .exceptions import ClaudeSDKError, StreamTimeoutError
from .options import OptionsHandler
from .parser import parse_formatted_sdk_content


__all__ = [
    # Session Context will be imported here once created
    "ClaudeSDKClient",
    "ClaudeSDKError",
    "StreamTimeoutError",
    "MessageConverter",
    "OptionsHandler",
    "parse_formatted_sdk_content",
    # Re-export SDK models from core adapter
    "AssistantMessage",
    "ContentBlock",
    "ExtendedContentBlock",
    "ResultMessage",
    "ResultMessageBlock",
    "SDKContentBlock",
    "SDKMessage",
    "SDKMessageContent",
    "SDKMessageMode",
    "SystemMessage",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolResultSDKBlock",
    "ToolUseBlock",
    "ToolUseSDKBlock",
    "UserMessage",
    "convert_sdk_result_message",
    "convert_sdk_system_message",
    "convert_sdk_text_block",
    "convert_sdk_tool_result_block",
    "convert_sdk_tool_use_block",
    "create_sdk_message",
    "to_sdk_variant",
]
