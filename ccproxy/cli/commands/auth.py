"""Authentication and credential management commands."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any


if TYPE_CHECKING:
    from ccproxy.auth.openai import OpenAIOAuthClient, OpenAITokenManager
    from ccproxy.plugins.protocol import ProviderPlugin
    from plugins.codex.config import CodexSettings

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from structlog import get_logger

from ccproxy.cli.helpers import get_rich_toolkit
from ccproxy.config.settings import get_settings
from ccproxy.core.async_utils import get_claude_docker_home_dir
from ccproxy.models.provider import ProviderConfig
from ccproxy.plugins.loader import PluginLoader
from ccproxy.services.credentials import CredentialsManager


app = typer.Typer(name="auth", help="Authentication and credential management")

console = Console()
logger = get_logger(__name__)


def get_credentials_manager(
    custom_paths: list[Path] | None = None,
) -> CredentialsManager:
    """Get a CredentialsManager instance with custom paths if provided."""
    if custom_paths:
        # Get base settings and update storage paths
        settings = get_settings()
        settings.auth.storage.storage_paths = custom_paths
        return CredentialsManager(config=settings.auth)
    else:
        # Use default settings
        settings = get_settings()
        return CredentialsManager(config=settings.auth)


def get_docker_credential_paths() -> list[Path]:
    """Get credential file paths for Docker environment."""
    docker_home = Path(get_claude_docker_home_dir())
    return [
        docker_home / ".claude" / ".credentials.json",
        docker_home / ".config" / "claude" / ".credentials.json",
        Path(".credentials.json"),
    ]


async def discover_oauth_providers() -> dict[str, tuple[str, str]]:
    """Discover available OAuth-enabled providers.

    Returns:
        Dictionary mapping provider names to (auth_type, description) tuples
    """
    oauth_providers = {}

    loader = PluginLoader()
    plugins = await loader.discover_plugins()

    for plugin in plugins:
        # Check if plugin supports OAuth
        try:
            # Try to get config class and instantiate it directly
            config_class = plugin.get_config_class()
            if config_class:
                config = config_class()
                # Check if config is a ProviderConfig with OAuth support
                if (
                    isinstance(config, ProviderConfig)
                    and hasattr(config, "requires_auth")
                    and config.requires_auth
                    and hasattr(config, "auth_type")
                    and config.auth_type == "oauth"
                ):
                    oauth_providers[plugin.name] = (
                        config.auth_type,
                        f"{plugin.name} OAuth provider",
                    )
        except Exception as e:
            logger.debug(f"Failed to check OAuth support for plugin {plugin.name}: {e}")
            continue

    return oauth_providers


def get_oauth_provider_choices() -> list[str]:
    """Get list of available OAuth provider names for CLI choices."""
    providers = asyncio.run(discover_oauth_providers())
    return list(providers.keys())


async def get_plugin_for_provider(provider: str) -> "ProviderPlugin":
    """Get the plugin instance for the specified provider.

    Args:
        provider: Provider name (e.g., 'claude_api', 'codex')

    Returns:
        Plugin instance for the provider

    Raises:
        ValueError: If provider not found or doesn't support OAuth
    """
    loader = PluginLoader()
    plugins = await loader.discover_plugins()

    for plugin in plugins:
        if plugin.name == provider:
            try:
                # Try to get config class and instantiate it directly
                config_class = plugin.get_config_class()
                if config_class:
                    config = config_class()
                    if (
                        hasattr(config, "requires_auth")
                        and config.requires_auth
                        and hasattr(config, "auth_type")
                        and config.auth_type == "oauth"
                    ):
                        return plugin
                    else:
                        raise ValueError(
                            f"Provider '{provider}' does not support OAuth authentication"
                        )
            except Exception as e:
                raise ValueError(
                    f"Failed to check OAuth support for provider '{provider}': {e}"
                ) from e

    raise ValueError(f"OAuth provider '{provider}' not found")


async def get_oauth_client_for_provider(provider: str) -> Any:
    """Get OAuth client for the specified provider.

    Args:
        provider: Provider name (e.g., 'claude_api', 'codex')

    Returns:
        OAuth client instance for the provider

    Raises:
        ValueError: If provider not found or doesn't support OAuth
    """
    plugin = await get_plugin_for_provider(provider)

    # Initialize plugin with minimal CoreServices for CLI context
    import httpx
    import structlog

    from ccproxy.core.services import CoreServices

    settings = get_settings()

    # Create minimal services for CLI usage
    async with httpx.AsyncClient() as client:
        services = CoreServices(
            settings=settings,
            http_client=client,
            logger=structlog.get_logger(),
        )

        # Initialize the plugin
        await plugin.initialize(services)

        # Now get the OAuth client
        oauth_client = await plugin.get_oauth_client()
        if not oauth_client:
            raise ValueError(f"Provider '{provider}' does not implement OAuth client")
        return oauth_client


async def check_provider_credentials(provider: str) -> dict[str, Any]:
    """Check if provider has valid stored credentials.

    Args:
        provider: Provider name

    Returns:
        Dictionary with credential status information
    """
    try:
        # Get plugin for provider and use it to check credentials
        plugin = await get_plugin_for_provider(provider)
        oauth_client = await plugin.get_oauth_client()

        if not oauth_client:
            return {
                "has_credentials": False,
                "expired": True,
                "path": None,
                "credentials": None,
            }

        # Try to get profile info to test if credentials are valid
        # This will use the plugin's internal credential checking logic
        profile_info = await plugin.get_profile_info()

        # Basic credential status based on whether we can get profile info
        has_credentials = profile_info is not None

        return {
            "has_credentials": has_credentials,
            "expired": not has_credentials,
            "path": None,  # Plugin-specific, would need to be added to protocol if needed
            "credentials": None,  # Plugin-specific, would need to be added to protocol if needed
        }

    except Exception:
        # If we can't check credentials, assume none exist
        return {
            "has_credentials": False,
            "expired": True,
            "path": None,
            "credentials": None,
        }


@app.command(name="providers")
def list_providers() -> None:
    """List all available OAuth providers."""
    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]Available OAuth Providers[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        providers = asyncio.run(discover_oauth_providers())

        if not providers:
            toolkit.print("No OAuth providers found", tag="warning")
            return

        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.ROUNDED,
            title="OAuth Providers",
            title_style="bold white",
        )
        table.add_column("Provider", style="cyan")
        table.add_column("Auth Type", style="white")
        table.add_column("Description", style="dim")

        for name, (auth_type, description) in providers.items():
            table.add_row(name, auth_type, description)

        console.print(table)

    except Exception as e:
        toolkit.print(f"Error listing providers: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="login")
def login_command(
    provider: Annotated[
        str | None,
        typer.Argument(
            help="Provider to authenticate with (claude-sdk, claude-api, codex, openai)"
        ),
    ] = None,
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help="Use Docker credential paths (Claude SDK only)",
        ),
    ] = False,
    credential_file: Annotated[
        str | None,
        typer.Option(
            "--credential-file",
            help="Path to specific credential file (Claude SDK only)",
        ),
    ] = None,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Don't automatically open browser for OAuth"),
    ] = False,
) -> None:
    """Login to a provider using appropriate authentication.

    Examples:
        ccproxy auth login                # Default Claude SDK login
        ccproxy auth login claude-sdk     # Explicit Claude SDK login
        ccproxy auth login claude-api     # Claude API OAuth login
        ccproxy auth login codex          # Codex/OpenAI OAuth login
        ccproxy auth login openai         # Alias for codex
    """
    toolkit = get_rich_toolkit()

    # Default to claude-sdk if no provider specified
    if provider is None:
        provider = "claude-sdk"

    # Normalize provider names
    provider = provider.lower()
    if provider == "openai":
        provider = "codex"  # OpenAI is an alias for codex
    elif provider == "claude":
        provider = "claude-sdk"  # Default 'claude' to SDK

    # Handle Claude SDK authentication (non-OAuth)
    if provider == "claude-sdk":
        toolkit.print("[bold cyan]Claude SDK Login[/bold cyan]", centered=True)
        toolkit.print_line()

        try:
            # Get credential paths based on options
            custom_paths = None
            if credential_file:
                custom_paths = [Path(credential_file)]
            elif docker:
                custom_paths = get_docker_credential_paths()

            # Check if already logged in
            manager = get_credentials_manager(custom_paths)
            validation_result = asyncio.run(manager.validate())
            if validation_result.valid and not validation_result.expired:
                console.print(
                    "[yellow]You are already logged in with valid credentials.[/yellow]"
                )
                console.print(
                    "Use [cyan]ccproxy auth info[/cyan] to view current credentials."
                )

                overwrite = typer.confirm(
                    "Do you want to login again and overwrite existing credentials?"
                )
                if not overwrite:
                    console.print("Login cancelled.")
                    return

            # Perform OAuth login
            console.print("Starting OAuth login process...")
            console.print("Your browser will open for authentication.")
            console.print(
                "A temporary server will start on port 54545 for the OAuth callback..."
            )

            try:
                asyncio.run(manager.login())
                toolkit.print("Successfully logged in to Claude SDK!", tag="success")

                # Show credential info
                console.print("\n[dim]Credential information:[/dim]")
                updated_validation = asyncio.run(manager.validate())
                if updated_validation.valid and updated_validation.credentials:
                    oauth_token = updated_validation.credentials.claude_ai_oauth
                    console.print(
                        f"  Subscription: {oauth_token.subscription_type or 'Unknown'}"
                    )
                    if oauth_token.scopes:
                        console.print(f"  Scopes: {', '.join(oauth_token.scopes)}")
                    exp_dt = oauth_token.expires_at_datetime
                    console.print(
                        f"  Expires: {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
            except Exception as e:
                logger.error(f"Login failed: {e}")
                toolkit.print("Login failed. Please try again.", tag="error")
                raise typer.Exit(1) from e

        except KeyboardInterrupt:
            console.print("\n[yellow]Login cancelled by user.[/yellow]")
            raise typer.Exit(1) from None
        except Exception as e:
            toolkit.print(f"Error during login: {e}", tag="error")
            raise typer.Exit(1) from e

        return

    # Handle OAuth providers (claude-api, codex)
    toolkit.print(
        f"[bold cyan]OAuth Login - {provider.replace('_', '-').title()}[/bold cyan]",
        centered=True,
    )
    toolkit.print_line()

    try:
        # Validate provider exists
        providers = asyncio.run(discover_oauth_providers())
        if provider not in providers:
            available = ", ".join(providers.keys()) if providers else "none"
            toolkit.print(
                f"Provider '{provider}' not found. Available OAuth providers: {available}",
                tag="error",
            )
            raise typer.Exit(1)

        # Special handling for codex/OpenAI
        if provider == "codex":
            from ccproxy.auth.openai import OpenAIOAuthClient, OpenAITokenManager
            from plugins.codex.config import CodexSettings

            settings = CodexSettings()
            token_manager = OpenAITokenManager()
            oauth_client = OpenAIOAuthClient(settings, token_manager)

            console.print("Starting OpenAI/Codex OAuth login process...")
            console.print(
                "A temporary server will start on port 1455 for the OAuth callback..."
            )
            if not no_browser:
                console.print("Your browser will open for authentication.")

            credentials = asyncio.run(
                oauth_client.authenticate(open_browser=not no_browser)
            )

            toolkit.print("Successfully logged in to OpenAI/Codex!", tag="success")
            console.print("\n[dim]Credential information:[/dim]")
            console.print(f"  Account ID: {credentials.account_id}")
            console.print(
                f"  Expires: {credentials.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        else:
            # Generic OAuth flow for other providers
            oauth_client = asyncio.run(get_oauth_client_for_provider(provider))

            console.print(f"Starting {provider} OAuth login process...")
            if not no_browser:
                console.print("Your browser will open for authentication.")

            credentials = asyncio.run(
                oauth_client.authenticate(open_browser=not no_browser)
            )

            toolkit.print(f"Successfully logged in to {provider}!", tag="success")
            console.print(f"\n[dim]Authenticated with {provider}[/dim]")

    except ValueError as e:
        toolkit.print(str(e), tag="error")
        raise typer.Exit(1) from e
    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled by user.[/yellow]")
        raise typer.Exit(1) from None
    except Exception as e:
        toolkit.print(f"Error during {provider} login: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="status")
def status_command(
    provider: Annotated[
        str | None,
        typer.Argument(
            help="Provider to check status (claude-sdk, claude-api, codex, openai)"
        ),
    ] = None,
    detailed: Annotated[
        bool,
        typer.Option("--detailed", "-d", help="Show detailed credential information"),
    ] = False,
) -> None:
    """Check authentication status and info for specified provider.

    Shows authentication status, credential validity, and account information.
    If no provider specified, checks Claude SDK status.

    Examples:
        ccproxy auth status              # Claude SDK status (default)
        ccproxy auth status codex        # Codex/OpenAI status
        ccproxy auth status -d           # Detailed info with tokens
    """
    toolkit = get_rich_toolkit()

    # Default to claude-sdk if no provider specified
    if provider is None:
        provider = "claude-sdk"

    # Normalize provider names
    provider = provider.lower()
    if provider == "openai":
        provider = "codex"
    elif provider == "claude":
        provider = "claude-sdk"

    toolkit.print(
        f"[bold cyan]{provider.replace('_', '-').title()} Authentication Status[/bold cyan]",
        centered=True,
    )
    toolkit.print_line()

    async def get_plugin_profile_info(provider_name: str) -> dict[str, Any] | None:
        """Get profile info using plugin's method."""
        try:
            plugin = await get_plugin_for_provider(provider_name)

            # Initialize plugin with minimal CoreServices
            import httpx
            import structlog

            from ccproxy.core.services import CoreServices

            settings = get_settings()

            async with httpx.AsyncClient() as client:
                services = CoreServices(
                    settings=settings,
                    http_client=client,
                    logger=structlog.get_logger(),
                )
                await plugin.initialize(services)

                # Get profile info
                return await plugin.get_profile_info()
        except ValueError:
            # Provider doesn't support OAuth or doesn't exist
            return None
        except Exception as e:
            logger.debug(f"Failed to get profile info for {provider_name}: {e}")
            return None

    try:
        # Try to get profile info from ANY plugin (OAuth or not)
        async def get_any_plugin_profile() -> dict[str, Any] | None:
            """Get profile info from any plugin, not just OAuth ones."""
            try:
                loader = PluginLoader()
                plugins = await loader.discover_plugins()

                for plugin in plugins:
                    if plugin.name == provider:
                        # Initialize plugin with minimal CoreServices
                        import httpx
                        import structlog

                        from ccproxy.core.services import CoreServices

                        settings = get_settings()

                        async with httpx.AsyncClient() as client:
                            services = CoreServices(
                                settings=settings,
                                http_client=client,
                                logger=structlog.get_logger(),
                            )
                            await plugin.initialize(services)

                            # Get profile info
                            return await plugin.get_profile_info()

                return None
            except Exception as e:
                logger.debug(f"Failed to get profile info for {provider}: {e}")
                return None

        # Get profile info from the plugin
        profile_info = asyncio.run(get_any_plugin_profile())

        if profile_info:
            console.print("[green]✓[/green] Authenticated with valid credentials")

            # Display profile information generically
            console.print("\n[bold]Account Information[/bold]")

            # Define field display names
            field_display_names = {
                "email": "Email",
                "organization_name": "Organization",
                "organization_type": "Organization Type",
                "subscription_type": "Subscription",
                "plan_type": "Plan",
                "user_id": "User ID",
                "account_id": "Account ID",
                "full_name": "Full Name",
                "display_name": "Display Name",
                "has_claude_pro": "Claude Pro",
                "has_claude_max": "Claude Max",
                "rate_limit_tier": "Rate Limit Tier",
                "billing_type": "Billing Type",
                "expires_at": "Expires At",
                "scopes": "Scopes",
                "email_verified": "Email Verified",
                "subscription_start": "Subscription Start",
                "subscription_until": "Subscription Until",
                "organization_role": "Organization Role",
                "organization_id": "Organization ID",
            }

            # Display fields in a sensible order
            priority_fields = [
                "email",
                "organization_name",
                "subscription_type",
                "plan_type",
                "expires_at",
            ]

            # Show priority fields first
            for field in priority_fields:
                if field in profile_info:
                    display_name = field_display_names.get(
                        field, field.replace("_", " ").title()
                    )
                    value = profile_info[field]

                    # Format special values
                    if field == "scopes" and isinstance(value, list):
                        value = ", ".join(value)
                    elif field == "user_id" and len(str(value)) > 20:
                        value = f"{str(value)[:12]}..."
                    elif isinstance(value, bool):
                        value = "Yes" if value else "No"

                    console.print(f"  {display_name}: {value}")

            # Show remaining fields
            for field, value in profile_info.items():
                if field not in priority_fields and field not in [
                    "provider",
                    "authenticated",
                ]:
                    display_name = field_display_names.get(
                        field, field.replace("_", " ").title()
                    )

                    # Format special values
                    if field == "scopes" and isinstance(value, list):
                        value = ", ".join(value)
                    elif field == "user_id" and len(str(value)) > 20:
                        value = f"{str(value)[:12]}..."
                    elif isinstance(value, bool):
                        value = "Yes" if value else "No"

                    console.print(f"  {display_name}: {value}")

            # For detailed mode, try to show token preview if available
            if detailed:
                # Special handling for different providers
                if provider == "claude-sdk":
                    manager = get_credentials_manager()
                    validation_result = asyncio.run(manager.validate())
                    if validation_result.valid and validation_result.credentials:
                        oauth = validation_result.credentials.claude_ai_oauth
                        if oauth.access_token:
                            token_preview = (
                                f"{oauth.access_token[:8]}...{oauth.access_token[-8:]}"
                            )
                            console.print(f"\n  Token: [dim]{token_preview}[/dim]")
                elif provider == "codex":
                    from ccproxy.auth.openai import OpenAITokenManager

                    token_manager = OpenAITokenManager()
                    credentials = asyncio.run(token_manager.load_credentials())
                    if credentials and credentials.access_token:
                        token_preview = f"{credentials.access_token[:12]}...{credentials.access_token[-8:]}"
                        console.print(f"\n  Token: [dim]{token_preview}[/dim]")
        else:
            # No profile info means not authenticated or provider doesn't exist
            console.print("[red]✗[/red] Not authenticated or provider not found")
            console.print(f"  Run 'ccproxy auth login {provider}' to authenticate")

    except Exception as e:
        console.print(f"[red]✗[/red] Error checking status: {e}")
        raise typer.Exit(1) from e


@app.command(name="logout")
def logout_command(
    provider: Annotated[
        str, typer.Argument(help="Provider to logout from (codex, openai)")
    ],
) -> None:
    """Logout and remove stored credentials for specified provider.

    Examples:
        ccproxy auth logout codex
        ccproxy auth logout openai
    """
    toolkit = get_rich_toolkit()

    # Normalize provider names
    provider = provider.lower()
    if provider == "openai":
        provider = "codex"

    toolkit.print(f"[bold cyan]{provider.title()} Logout[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        if provider == "codex":
            from ccproxy.auth.openai import OpenAITokenManager

            token_manager = OpenAITokenManager()
            existing_creds = asyncio.run(token_manager.load_credentials())

            if not existing_creds:
                console.print(
                    "[yellow]No credentials found. Already logged out.[/yellow]"
                )
                return

            # Confirm logout
            confirm = typer.confirm(
                "Are you sure you want to logout and remove credentials?"
            )
            if not confirm:
                console.print("Logout cancelled.")
                return

            # Delete credentials
            success = asyncio.run(token_manager.delete_credentials())

            if success:
                toolkit.print(
                    f"Successfully logged out from {provider}!", tag="success"
                )
                console.print("Credentials have been removed.")
            else:
                toolkit.print("Failed to remove credentials", tag="error")
                raise typer.Exit(1)
        else:
            toolkit.print(
                f"Logout not implemented for provider '{provider}'", tag="error"
            )
            raise typer.Exit(1)

    except Exception as e:
        toolkit.print(f"Error during logout: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command()
def renew(
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            "-d",
            help="Renew credentials for Docker environment",
        ),
    ] = False,
    credential_file: Annotated[
        Path | None,
        typer.Option(
            "--credential-file",
            "-f",
            help="Path to custom credential file",
        ),
    ] = None,
) -> None:
    """Force renew Claude credentials without checking expiration.

    This command will refresh your access token regardless of whether it's expired.
    Useful for testing or when you want to ensure you have the latest token.

    Examples:
        ccproxy auth renew
        ccproxy auth renew --docker
        ccproxy auth renew --credential-file /path/to/credentials.json
    """
    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]Claude Credentials Renewal[/bold cyan]", centered=True)
    toolkit.print_line()

    console = Console()

    try:
        # Get credential paths based on options
        custom_paths = None
        if credential_file:
            custom_paths = [Path(credential_file)]
        elif docker:
            custom_paths = get_docker_credential_paths()

        # Create credentials manager
        manager = get_credentials_manager(custom_paths)

        # Check if credentials exist
        validation_result = asyncio.run(manager.validate())
        if not validation_result.valid:
            toolkit.print("[red]✗[/red] No credentials found to renew", tag="error")
            console.print("\n[dim]Please login first:[/dim]")
            console.print("[cyan]ccproxy auth login[/cyan]")
            raise typer.Exit(1)

        # Force refresh the token
        console.print("[yellow]Refreshing access token...[/yellow]")
        refreshed_credentials = asyncio.run(manager.refresh_token())

        if refreshed_credentials:
            toolkit.print(
                "[green]✓[/green] Successfully renewed credentials!", tag="success"
            )

            # Show updated credential info
            oauth_token = refreshed_credentials.claude_ai_oauth
            console.print("\n[dim]Updated credential information:[/dim]")
            console.print(
                f"  Subscription: {oauth_token.subscription_type or 'Unknown'}"
            )
            if oauth_token.scopes:
                console.print(f"  Scopes: {', '.join(oauth_token.scopes)}")
            exp_dt = oauth_token.expires_at_datetime
            console.print(f"  Expires: {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        else:
            toolkit.print("[red]✗[/red] Failed to renew credentials", tag="error")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Renewal cancelled by user.[/yellow]")
        raise typer.Exit(1) from None
    except Exception as e:
        toolkit.print(f"Error during renewal: {e}", tag="error")
        raise typer.Exit(1) from e


# OpenAI Codex Authentication Commands


def get_openai_token_manager() -> "OpenAITokenManager":
    """Get OpenAI token manager dependency."""
    from ccproxy.auth.openai import OpenAITokenManager

    return OpenAITokenManager()


def get_openai_oauth_client(settings: "CodexSettings") -> "OpenAIOAuthClient":
    """Get OpenAI OAuth client dependency."""
    from ccproxy.auth.openai import OpenAIOAuthClient

    token_manager = get_openai_token_manager()
    return OpenAIOAuthClient(settings, token_manager)
