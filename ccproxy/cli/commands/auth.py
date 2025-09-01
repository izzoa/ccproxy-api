"""Authentication and credential management commands."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated


if TYPE_CHECKING:
    from ccproxy.auth.openai import OpenAIOAuthClient, OpenAITokenManager
    from ccproxy.config.codex import CodexSettings

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from structlog import get_logger

from ccproxy.auth.exceptions import CredentialsNotFoundError
from ccproxy.cli.helpers import get_rich_toolkit
from ccproxy.config.settings import get_settings
from ccproxy.core.async_utils import get_claude_docker_home_dir
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


@app.command(name="validate")
def validate_credentials(
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help="Use Docker credential paths (from get_claude_docker_home_dir())",
        ),
    ] = False,
    credential_file: Annotated[
        str | None,
        typer.Option(
            "--credential-file",
            help="Path to specific credential file to validate",
        ),
    ] = None,
) -> None:
    """Validate Claude CLI credentials.

    Checks for valid Claude credentials in standard locations:
    - ~/.claude/credentials.json
    - ~/.config/claude/credentials.json

    With --docker flag, checks Docker credential paths:
    - {docker_home}/.claude/credentials.json
    - {docker_home}/.config/claude/credentials.json

    With --credential-file, validates the specified file directly.

    Examples:
        ccproxy auth validate
        ccproxy auth validate --docker
        ccproxy auth validate --credential-file /path/to/credentials.json
    """
    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]Claude Credentials Validation[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        # Get credential paths based on options
        custom_paths = None
        if credential_file:
            custom_paths = [Path(credential_file)]
        elif docker:
            custom_paths = get_docker_credential_paths()

        # Validate credentials
        manager = get_credentials_manager(custom_paths)
        validation_result = asyncio.run(manager.validate())

        if validation_result.valid:
            # Create a status table
            table = Table(
                show_header=True,
                header_style="bold cyan",
                box=box.ROUNDED,
                title="Credential Status",
                title_style="bold white",
            )
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="white")

            # Status
            status = "Valid" if not validation_result.expired else "Expired"
            status_style = "green" if not validation_result.expired else "red"
            table.add_row("Status", f"[{status_style}]{status}[/{status_style}]")

            # Path
            if validation_result.path:
                table.add_row("Location", f"[dim]{validation_result.path}[/dim]")

            # Subscription type
            if validation_result.credentials:
                sub_type = (
                    validation_result.credentials.claude_ai_oauth.subscription_type
                    or "Unknown"
                )
                table.add_row("Subscription", f"[bold]{sub_type}[/bold]")

                # Expiration
                oauth_token = validation_result.credentials.claude_ai_oauth
                exp_dt = oauth_token.expires_at_datetime
                now = datetime.now(UTC)
                time_diff = exp_dt - now

                if time_diff.total_seconds() > 0:
                    days = time_diff.days
                    hours = time_diff.seconds // 3600
                    exp_str = f"{exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({days}d {hours}h remaining)"
                else:
                    exp_str = f"{exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} [red](Expired)[/red]"

                table.add_row("Expires", exp_str)

                # Scopes
                scopes = oauth_token.scopes
                if scopes:
                    table.add_row("Scopes", ", ".join(str(s) for s in scopes))

            console.print(table)

            # Success message
            if not validation_result.expired:
                toolkit.print(
                    "[green]✓[/green] Valid Claude credentials found", tag="success"
                )
            else:
                toolkit.print(
                    "[yellow]![/yellow] Claude credentials found but expired",
                    tag="warning",
                )
                toolkit.print(
                    "\nPlease refresh your credentials by logging into Claude CLI",
                    tag="info",
                )

        else:
            # No valid credentials
            toolkit.print("[red]✗[/red] No credentials file found", tag="error")

            console.print("\n[dim]To authenticate with Claude CLI, run:[/dim]")
            console.print("[cyan]claude login[/cyan]")

    except Exception as e:
        toolkit.print(f"Error validating credentials: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="info")
def credential_info(
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help="Use Docker credential paths (from get_claude_docker_home_dir())",
        ),
    ] = False,
    credential_file: Annotated[
        str | None,
        typer.Option(
            "--credential-file",
            help="Path to specific credential file to display info for",
        ),
    ] = None,
) -> None:
    """Display detailed credential information.

    Shows all available information about Claude credentials including
    file location, token details, and subscription information.

    Examples:
        ccproxy auth info
        ccproxy auth info --docker
        ccproxy auth info --credential-file /path/to/credentials.json
    """
    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]Claude Credential Information[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        # Get credential paths based on options
        custom_paths = None
        if credential_file:
            custom_paths = [Path(credential_file)]
        elif docker:
            custom_paths = get_docker_credential_paths()

        # Get credentials manager and try to load credentials
        manager = get_credentials_manager(custom_paths)
        credentials = asyncio.run(manager.load())

        if not credentials:
            toolkit.print("No credential file found", tag="error")
            console.print("\n[dim]Expected locations:[/dim]")
            for path in manager.config.storage.storage_paths:
                console.print(f"  - {path}")
            raise typer.Exit(1)

        # Display account section
        console.print("\n[bold]Account[/bold]")
        oauth = credentials.claude_ai_oauth

        # Login method based on subscription type
        login_method = "Claude Account"
        if oauth.subscription_type:
            login_method = f"Claude {oauth.subscription_type.title()} Account"
        console.print(f"  L Login Method: {login_method}")

        # Try to load saved account profile first
        profile = asyncio.run(manager.get_account_profile())

        if profile:
            # Display saved account data
            if profile.organization:
                console.print(f"  L Organization: {profile.organization.name}")
                if profile.organization.organization_type:
                    console.print(
                        f"  L Organization Type: {profile.organization.organization_type}"
                    )
                if profile.organization.billing_type:
                    console.print(
                        f"  L Billing Type: {profile.organization.billing_type}"
                    )
                if profile.organization.rate_limit_tier:
                    console.print(
                        f"  L Rate Limit Tier: {profile.organization.rate_limit_tier}"
                    )
            else:
                console.print("  L Organization: [dim]Not available[/dim]")

            if profile.account:
                console.print(f"  L Email: {profile.account.email}")
                if profile.account.full_name:
                    console.print(f"  L Full Name: {profile.account.full_name}")
                if profile.account.display_name:
                    console.print(f"  L Display Name: {profile.account.display_name}")
                console.print(
                    f"  L Has Claude Pro: {'Yes' if profile.account.has_claude_pro else 'No'}"
                )
                console.print(
                    f"  L Has Claude Max: {'Yes' if profile.account.has_claude_max else 'No'}"
                )
            else:
                console.print("  L Email: [dim]Not available[/dim]")
        else:
            # No saved profile, try to fetch fresh data
            try:
                # First try to get a valid access token (with refresh if needed)
                valid_token = asyncio.run(manager.get_access_token())
                if valid_token:
                    profile = asyncio.run(manager.fetch_user_profile())
                    if profile:
                        # Save the profile for future use
                        asyncio.run(manager._save_account_profile(profile))

                        if profile.organization:
                            console.print(
                                f"  L Organization: {profile.organization.name}"
                            )
                        else:
                            console.print(
                                "  L Organization: [dim]Unable to fetch[/dim]"
                            )

                        if profile.account:
                            console.print(f"  L Email: {profile.account.email}")
                        else:
                            console.print("  L Email: [dim]Unable to fetch[/dim]")
                    else:
                        console.print("  L Organization: [dim]Unable to fetch[/dim]")
                        console.print("  L Email: [dim]Unable to fetch[/dim]")

                    # Reload credentials after potential refresh to show updated token info
                    credentials = asyncio.run(manager.load())
                    if credentials:
                        oauth = credentials.claude_ai_oauth
                else:
                    console.print("  L Organization: [dim]Token refresh failed[/dim]")
                    console.print("  L Email: [dim]Token refresh failed[/dim]")
            except Exception as e:
                logger.debug(f"Could not fetch user profile: {e}")
                console.print("  L Organization: [dim]Unable to fetch[/dim]")
                console.print("  L Email: [dim]Unable to fetch[/dim]")

        # Create details table
        console.print()
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.ROUNDED,
            title="Credential Details",
            title_style="bold white",
        )
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")

        # File location - check if there's a credentials file or if using keyring
        cred_file = asyncio.run(manager.find_credentials_file())
        if cred_file:
            table.add_row("File Location", str(cred_file))
        else:
            table.add_row("File Location", "Keyring storage")

        # Token info
        table.add_row("Subscription Type", oauth.subscription_type or "Unknown")
        table.add_row(
            "Token Expired",
            "[red]Yes[/red]" if oauth.is_expired else "[green]No[/green]",
        )

        # Expiration details
        exp_dt = oauth.expires_at_datetime
        table.add_row("Expires At", exp_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))

        # Time until expiration
        now = datetime.now(UTC)
        time_diff = exp_dt - now
        if time_diff.total_seconds() > 0:
            days = time_diff.days
            hours = (time_diff.seconds % 86400) // 3600
            minutes = (time_diff.seconds % 3600) // 60
            table.add_row(
                "Time Remaining", f"{days} days, {hours} hours, {minutes} minutes"
            )
        else:
            table.add_row("Time Remaining", "[red]Expired[/red]")

        # Scopes
        if oauth.scopes:
            table.add_row("OAuth Scopes", ", ".join(oauth.scopes))

        # Token preview (first and last 8 chars)
        if oauth.access_token:
            token_preview = f"{oauth.access_token[:8]}...{oauth.access_token[-8:]}"
            table.add_row("Access Token", f"[dim]{token_preview}[/dim]")

        # Account profile status
        account_profile_exists = profile is not None
        table.add_row(
            "Account Profile",
            "[green]Available[/green]"
            if account_profile_exists
            else "[yellow]Not saved[/yellow]",
        )

        console.print(table)

    except Exception as e:
        toolkit.print(f"Error getting credential info: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="login")
def login_command(
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help="Use Docker credential paths (from get_claude_docker_home_dir())",
        ),
    ] = False,
    credential_file: Annotated[
        str | None,
        typer.Option(
            "--credential-file",
            help="Path to specific credential file to save to",
        ),
    ] = None,
) -> None:
    """Login to Claude using OAuth authentication.

    This command will open your web browser to authenticate with Claude
    and save the credentials locally.

    Examples:
        ccproxy auth login
        ccproxy auth login --docker
        ccproxy auth login --credential-file /path/to/credentials.json
    """
    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]Claude OAuth Login[/bold cyan]", centered=True)
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
        try:
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
        except CredentialsNotFoundError:
            # No credentials found, proceed with login
            pass

        # Perform OAuth login
        console.print("Starting OAuth login process...")
        console.print("Your browser will open for authentication.")
        console.print(
            "A temporary server will start on port 54545 for the OAuth callback..."
        )

        try:
            asyncio.run(manager.login())
            success = True
        except Exception as e:
            logger.error(f"Login failed: {e}")
            success = False

        if success:
            toolkit.print("Successfully logged in to Claude!", tag="success")

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
                console.print(f"  Expires: {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        else:
            toolkit.print("Login failed. Please try again.", tag="error")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled by user.[/yellow]")
        raise typer.Exit(1) from None
    except Exception as e:
        toolkit.print(f"Error during login: {e}", tag="error")
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


@app.command(name="login-openai")
def login_openai_command(
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Don't automatically open browser for authentication",
        ),
    ] = False,
) -> None:
    """Login to OpenAI using OAuth authentication.

    This command will start a local callback server and open your web browser
    to authenticate with OpenAI. The credentials will be saved to ~/.codex/auth.json.

    Examples:
        ccproxy auth login-openai
        ccproxy auth login-openai --no-browser
    """
    import asyncio

    from ccproxy.config.codex import CodexSettings

    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]OpenAI OAuth Login[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        # Get Codex settings
        settings = CodexSettings()

        # Check if already logged in
        token_manager = get_openai_token_manager()
        existing_creds = asyncio.run(token_manager.load_credentials())

        if existing_creds and not existing_creds.is_expired():
            console.print(
                "[yellow]You are already logged in with valid OpenAI credentials.[/yellow]"
            )
            console.print(
                "Use [cyan]ccproxy auth openai-info[/cyan] to view current credentials."
            )

            overwrite = typer.confirm(
                "Do you want to login again and overwrite existing credentials?"
            )
            if not overwrite:
                console.print("Login cancelled.")
                return

        # Create OAuth client and perform login
        oauth_client = get_openai_oauth_client(settings)

        console.print("Starting OpenAI OAuth login process...")
        console.print(
            "A temporary server will start on port 1455 for the OAuth callback..."
        )

        if no_browser:
            console.print("Browser will NOT be opened automatically.")
        else:
            console.print("Your browser will open for authentication.")

        try:
            credentials = asyncio.run(
                oauth_client.authenticate(open_browser=not no_browser)
            )

            toolkit.print("Successfully logged in to OpenAI!", tag="success")

            # Show credential info
            console.print("\n[dim]Credential information:[/dim]")
            console.print(f"  Account ID: {credentials.account_id}")
            console.print(
                f"  Expires: {credentials.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            console.print(f"  Active: {'Yes' if credentials.active else 'No'}")

        except Exception as e:
            logger.error(f"OpenAI login failed: {e}")
            toolkit.print(f"Login failed: {e}", tag="error")
            raise typer.Exit(1) from e

    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled by user.[/yellow]")
        raise typer.Exit(1) from None
    except Exception as e:
        toolkit.print(f"Error during OpenAI login: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="logout-openai")
def logout_openai_command() -> None:
    """Logout from OpenAI and remove saved credentials.

    This command will remove the OpenAI credentials file (~/.codex/auth.json)
    and invalidate the current session.

    Examples:
        ccproxy auth logout-openai
    """
    import asyncio

    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]OpenAI Logout[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        token_manager = get_openai_token_manager()

        # Check if credentials exist
        existing_creds = asyncio.run(token_manager.load_credentials())
        if not existing_creds:
            console.print(
                "[yellow]No OpenAI credentials found. Already logged out.[/yellow]"
            )
            return

        # Confirm logout
        confirm = typer.confirm(
            "Are you sure you want to logout and remove OpenAI credentials?"
        )
        if not confirm:
            console.print("Logout cancelled.")
            return

        # Delete credentials
        success = asyncio.run(token_manager.delete_credentials())

        if success:
            toolkit.print("Successfully logged out from OpenAI!", tag="success")
            console.print("OpenAI credentials have been removed.")
        else:
            toolkit.print("Failed to remove OpenAI credentials", tag="error")
            raise typer.Exit(1)

    except Exception as e:
        toolkit.print(f"Error during OpenAI logout: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="openai-info")
def openai_info_command() -> None:
    """Display OpenAI credential information.

    Shows detailed information about the current OpenAI credentials including
    account ID, token expiration, and storage location.

    Examples:
        ccproxy auth openai-info
    """
    import asyncio
    import base64
    import json
    from datetime import UTC, datetime

    from rich import box
    from rich.table import Table

    toolkit = get_rich_toolkit()
    toolkit.print("[bold cyan]OpenAI Credential Information[/bold cyan]", centered=True)
    toolkit.print_line()

    try:
        token_manager = get_openai_token_manager()
        credentials = asyncio.run(token_manager.load_credentials())

        if not credentials:
            toolkit.print("No OpenAI credentials found", tag="error")
            console.print("\n[dim]Expected location:[/dim]")
            storage_location = token_manager.storage.get_location()
            console.print(f"  - {storage_location}")
            console.print("\n[dim]To login:[/dim]")
            console.print("  ccproxy auth login-openai")
            raise typer.Exit(1)

        # Decode JWT token to extract additional information
        jwt_payload = {}
        jwt_header = {}
        if credentials.access_token:
            try:
                # Split JWT into parts
                parts = credentials.access_token.split(".")
                if len(parts) == 3:
                    # Decode header and payload (add padding if needed)
                    header_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
                    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)

                    jwt_header = json.loads(base64.urlsafe_b64decode(header_b64))
                    jwt_payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            except Exception as decode_error:
                logger.debug(f"Failed to decode JWT token: {decode_error}")

        # Display account section
        console.print("\n[bold]OpenAI Account[/bold]")
        console.print(f"  L Account ID: {credentials.account_id}")
        console.print(f"  L Status: {'Active' if credentials.active else 'Inactive'}")

        # Extract additional info from JWT payload
        if jwt_payload:
            # Get OpenAI auth info from the JWT
            openai_auth = jwt_payload.get("https://api.openai.com/auth", {})
            if openai_auth:
                if "email" in jwt_payload:
                    console.print(f"  L Email: {jwt_payload['email']}")
                    if jwt_payload.get("email_verified"):
                        console.print("  L Email Verified: Yes")

                if openai_auth.get("chatgpt_plan_type"):
                    console.print(
                        f"  L Plan Type: {openai_auth['chatgpt_plan_type'].upper()}"
                    )

                if openai_auth.get("chatgpt_user_id"):
                    console.print(f"  L User ID: {openai_auth['chatgpt_user_id']}")

                # Subscription info
                if openai_auth.get("chatgpt_subscription_active_start"):
                    console.print(
                        f"  L Subscription Start: {openai_auth['chatgpt_subscription_active_start']}"
                    )
                if openai_auth.get("chatgpt_subscription_active_until"):
                    console.print(
                        f"  L Subscription Until: {openai_auth['chatgpt_subscription_active_until']}"
                    )

                # Organizations
                orgs = openai_auth.get("organizations", [])
                if orgs:
                    for org in orgs:
                        if org.get("is_default"):
                            console.print(
                                f"  L Organization: {org.get('title', 'Unknown')} ({org.get('role', 'member')})"
                            )
                            console.print(f"  L Org ID: {org.get('id', 'Unknown')}")

        # Create details table
        console.print()
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.ROUNDED,
            title="Token Details",
            title_style="bold white",
        )
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")

        # File location
        storage_location = token_manager.storage.get_location()
        table.add_row("Storage Location", storage_location)

        # Token algorithm and type from JWT header
        if jwt_header:
            table.add_row("Algorithm", jwt_header.get("alg", "Unknown"))
            table.add_row("Token Type", jwt_header.get("typ", "Unknown"))
            if jwt_header.get("kid"):
                table.add_row("Key ID", jwt_header["kid"])

        # Token status
        table.add_row(
            "Token Expired",
            "[red]Yes[/red]" if credentials.is_expired() else "[green]No[/green]",
        )

        # Expiration details
        exp_dt = credentials.expires_at
        table.add_row("Expires At", exp_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))

        # Time until expiration
        now = datetime.now(UTC)
        time_diff = exp_dt - now
        if time_diff.total_seconds() > 0:
            days = time_diff.days
            hours = (time_diff.seconds % 86400) // 3600
            minutes = (time_diff.seconds % 3600) // 60
            table.add_row(
                "Time Remaining", f"{days} days, {hours} hours, {minutes} minutes"
            )
        else:
            table.add_row("Time Remaining", "[red]Expired[/red]")

        # JWT timestamps if available
        if jwt_payload:
            if "iat" in jwt_payload:
                iat_dt = datetime.fromtimestamp(jwt_payload["iat"], tz=UTC)
                table.add_row("Issued At", iat_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))

            if "auth_time" in jwt_payload:
                auth_dt = datetime.fromtimestamp(jwt_payload["auth_time"], tz=UTC)
                table.add_row("Auth Time", auth_dt.strftime("%Y-%m-%d %H:%M:%S UTC"))

        # JWT issuer and audience
        if jwt_payload:
            if "iss" in jwt_payload:
                table.add_row("Issuer", jwt_payload["iss"])
            if "aud" in jwt_payload:
                audience = jwt_payload["aud"]
                if isinstance(audience, list):
                    audience = ", ".join(audience)
                table.add_row("Audience", audience)
            if "jti" in jwt_payload:
                table.add_row("JWT ID", jwt_payload["jti"])
            if "sid" in jwt_payload:
                table.add_row("Session ID", jwt_payload["sid"])

        # Token preview (first and last 8 chars)
        if credentials.access_token:
            token_preview = (
                f"{credentials.access_token[:12]}...{credentials.access_token[-8:]}"
            )
            table.add_row("Access Token", f"[dim]{token_preview}[/dim]")

        # Refresh token status
        has_refresh = bool(credentials.refresh_token)
        table.add_row(
            "Refresh Token",
            "[green]Available[/green]"
            if has_refresh
            else "[yellow]Not available[/yellow]",
        )

        console.print(table)

        # Show usage instructions
        console.print("\n[dim]Commands:[/dim]")
        console.print("  ccproxy auth login-openai    - Re-authenticate")
        console.print("  ccproxy auth logout-openai   - Remove credentials")

    except Exception as e:
        toolkit.print(f"Error getting OpenAI credential info: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="openai-status")
def openai_status_command() -> None:
    """Check OpenAI authentication status.

    Quick status check for OpenAI credentials without detailed information.
    Useful for scripts and automation.

    Examples:
        ccproxy auth openai-status
    """
    import asyncio

    try:
        token_manager = get_openai_token_manager()
        credentials = asyncio.run(token_manager.load_credentials())

        if not credentials:
            console.print("[red]✗[/red] Not logged in to OpenAI")
            raise typer.Exit(1)

        if credentials.is_expired():
            console.print("[yellow]⚠[/yellow] OpenAI credentials expired")
            console.print(
                f"  Expired: {credentials.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            raise typer.Exit(1)

        console.print("[green]✓[/green] OpenAI credentials valid")
        console.print(f"  Account: {credentials.account_id}")
        console.print(
            f"  Expires: {credentials.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]✗[/red] Error checking OpenAI status: {e}")
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
