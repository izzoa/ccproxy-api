"""MCP permission prompt tool for Claude Code SDK."""

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from structlog import get_logger

from ccproxy.config.settings import config_manager
from ccproxy.models.responses import (
    PermissionToolAllowResponse,
    PermissionToolDenyResponse,
)


app = typer.Typer(
    rich_markup_mode="rich",
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)

logger = get_logger(__name__)


@app.command()
def permission_tool(
    tool_name: Annotated[
        str, typer.Argument(help="Name of the tool to check permissions for")
    ],
    tool_input: Annotated[str, typer.Argument(help="JSON string of the tool input")],
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to configuration file (TOML, JSON, or YAML)",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """
    MCP permission prompt tool for Claude Code SDK.

    This tool is used by the Claude Code SDK to check permissions for tool calls.
    It returns a JSON response indicating whether the tool call should be allowed or denied.

    Response format:
    - Allow: {"behavior": "allow", "updatedInput": {...}}
    - Deny: {"behavior": "deny", "message": "reason"}

    Examples:
        ccproxy-perm "bash" '{"command": "ls -la"}'
        ccproxy-perm "edit_file" '{"path": "/etc/passwd", "content": "..."}'
    """

    try:
        # Parse the tool input JSON
        try:
            input_data = json.loads(tool_input)
        except json.JSONDecodeError as e:
            response = PermissionToolDenyResponse(message=f"Invalid JSON input: {e}")
            print(response.model_dump_json(by_alias=True))
            raise typer.Exit(1) from e

        # Load settings to get permission configuration
        settings = config_manager.load_settings(config_path=config)

        # Basic permission checking logic
        # This can be extended with more sophisticated rules

        # Check for potentially dangerous commands
        dangerous_patterns = [
            "rm -rf",
            "sudo",
            "passwd",
            "chmod 777",
            "/etc/passwd",
            "/etc/shadow",
            "format",
            "mkfs",
        ]

        # Convert input to string for pattern matching
        input_str = json.dumps(input_data).lower()

        # Check for dangerous patterns
        for pattern in dangerous_patterns:
            if pattern in input_str:
                response = PermissionToolDenyResponse(
                    message=f"Tool call contains potentially dangerous pattern: {pattern}"
                )
                print(response.model_dump_json(by_alias=True))
                return

        # Check for specific tool restrictions
        restricted_tools = {"exec", "system", "shell", "subprocess"}

        if tool_name.lower() in restricted_tools:
            response = PermissionToolDenyResponse(
                message=f"Tool {tool_name} is restricted for security reasons"
            )
            print(response.model_dump_json(by_alias=True))
            return

        # Allow the tool call with original input
        allow_response = PermissionToolAllowResponse(updated_input=input_data)
        print(allow_response.model_dump_json(by_alias=True))

    except Exception as e:
        error_response = PermissionToolDenyResponse(
            message=f"Error processing permission request: {e}"
        )
        print(error_response.model_dump_json(by_alias=True))
        raise typer.Exit(1) from e


def main() -> None:
    """Entry point for ccproxy-perm command."""
    app()


if __name__ == "__main__":
    main()
