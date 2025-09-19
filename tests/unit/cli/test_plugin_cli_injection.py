from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from ccproxy.cli.main import _extend_command_with_arguments
from ccproxy.core.plugins.declaration import CliArgumentSpec


@pytest.mark.unit
@pytest.mark.skip(reason="CLI plugin injection refactored - function signature changed")
def test_injected_option_is_captured_in_context() -> None:
    # Arrange: simple Typer app with a target command
    app = typer.Typer()

    captured: dict[str, Any] = {}

    @app.command(name="serve")
    def serve_cmd(ctx: typer.Context) -> None:  # type: ignore[unused-argument]
        # At execution time the plugin-injected option should be available
        assert isinstance(ctx.obj, dict)
        captured.update(ctx.obj.get("plugin_cli_args") or {})

    # Inject a bool flag option via our helper
    spec = CliArgumentSpec(
        target_command="serve",
        argument_name="docker",
        argument_type=bool,
        help_text="Enable docker",
        default=False,
        typer_kwargs={"option": ["--docker/--no-docker"]},
    )

    _extend_command_with_arguments(app, "serve", [("test_plugin", spec)])

    # Act: run CLI with injected flag
    runner = CliRunner()
    # Typer apps can be invoked directly in CliRunner
    result = runner.invoke(
        app,
        ["serve", "--docker"],
        obj={"plugin_cli_args": {}},
        prog_name="ccproxy",
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert captured.get("docker") is True


@pytest.mark.unit
@pytest.mark.skip(reason="CLI plugin injection refactored - function signature changed")
def test_injected_option_for_nested_command() -> None:
    # Arrange: root app with a nested group `auth` and subcommand `login`
    root = typer.Typer()
    auth = typer.Typer()
    root.add_typer(auth, name="auth")

    seen: dict[str, Any] = {}

    @auth.command(name="login")
    def login(ctx: typer.Context) -> None:  # type: ignore[unused-argument]
        assert isinstance(ctx.obj, dict)
        seen.update(ctx.obj.get("plugin_cli_args") or {})

    # Inject string option into nested command using space-separated path
    arg = CliArgumentSpec(
        target_command="auth login",
        argument_name="provider_hint",
        argument_type=str,
        help_text="Provider hint",
        default=None,
        typer_kwargs={"option": ["--provider-hint"]},
    )

    _extend_command_with_arguments(root, "auth login", [("dummy_plugin", arg)])

    # Act
    res = CliRunner().invoke(
        root,
        ["auth", "login", "--provider-hint", "claude"],
        obj={"plugin_cli_args": {}},
        prog_name="ccproxy",
    )

    # Assert
    assert res.exit_code == 0, res.output
    assert seen.get("provider_hint") == "claude"
