"""Codex-specific CLI commands."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ccproxy.cli.helpers import get_rich_toolkit
from ccproxy.config.settings import Settings, get_settings


app = typer.Typer(
    name="codex",
    help="Codex (OpenAI) management commands",
    rich_markup_mode="rich",
    add_completion=True,
    no_args_is_help=True,
)


@app.command(name="info")
def codex_info() -> None:
    """Show Codex configuration and detection cache information."""
    toolkit = get_rich_toolkit()
    console = Console()

    try:
        settings = get_settings()
        
        # Display Codex configuration
        console.print(
            Panel.fit(
                "[bold]Codex (OpenAI) Configuration[/bold]",
                border_style="blue",
            )
        )
        console.print()
        
        # Create configuration table
        config_table = Table(title="Current Settings", show_header=True, header_style="bold magenta")
        config_table.add_column("Setting", style="cyan", width=30)
        config_table.add_column("Value", style="green")
        config_table.add_column("Description", style="dim")
        
        # Add configuration rows
        config_table.add_row(
            "Enabled",
            str(settings.codex.enabled),
            "Whether Codex provider is enabled"
        )
        config_table.add_row(
            "Base URL",
            settings.codex.base_url,
            "ChatGPT backend API URL"
        )
        config_table.add_row(
            "Instruction Mode",
            settings.codex.system_prompt_injection_mode,
            "How system prompts are handled"
        )
        config_table.add_row(
            "Dynamic Model Info",
            str(settings.codex.enable_dynamic_model_info),
            "Fetch model capabilities dynamically"
        )
        config_table.add_row(
            "Max Tokens Fallback",
            str(settings.codex.max_output_tokens_fallback),
            "Default when dynamic info unavailable"
        )
        config_table.add_row(
            "Propagate Unsupported",
            str(settings.codex.propagate_unsupported_params),
            "Pass through unsupported OpenAI params"
        )
        config_table.add_row(
            "Header Override",
            str(settings.codex.header_override_enabled),
            "Allow custom header overrides"
        )
        config_table.add_row(
            "Verbose Logging",
            str(settings.codex.verbose_logging),
            "Enable detailed Codex logs"
        )
        
        console.print(config_table)
        console.print()
        
        # Display OAuth configuration
        oauth_table = Table(title="OAuth Configuration", show_header=True, header_style="bold magenta")
        oauth_table.add_column("Setting", style="cyan", width=20)
        oauth_table.add_column("Value", style="green")
        
        oauth_table.add_row("OAuth Base URL", settings.codex.oauth.base_url)
        oauth_table.add_row("Client ID", settings.codex.oauth.client_id)
        oauth_table.add_row("Scopes", ", ".join(settings.codex.oauth.scopes))
        oauth_table.add_row("Callback Port", str(settings.codex.callback_port))
        oauth_table.add_row("Redirect URI", settings.codex.redirect_uri)
        
        console.print(oauth_table)
        console.print()

    except Exception as e:
        toolkit.print(f"Error loading Codex configuration: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="cache")
def codex_cache(
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Clear the Codex detection cache",
    ),
    show_raw: bool = typer.Option(
        False,
        "--raw",
        help="Show raw cache data as JSON",
    ),
) -> None:
    """Inspect or manage the Codex detection cache.
    
    The detection cache stores headers and instructions discovered from the
    ChatGPT backend for use with the Codex CLI.
    """
    toolkit = get_rich_toolkit()
    console = Console()
    
    try:
        # Try to load cache data
        cache_file = Path.home() / ".cache" / "ccproxy" / "codex_detection.json"
        
        if clear:
            if cache_file.exists():
                cache_file.unlink()
                toolkit.print("Codex detection cache cleared", tag="success")
            else:
                toolkit.print("No cache file found", tag="info")
            return
        
        if not cache_file.exists():
            toolkit.print("No Codex detection cache found", tag="info")
            toolkit.print(f"Cache location: {cache_file}", tag="dim")
            toolkit.print("\nThe cache will be created when you first authenticate with OpenAI.", tag="dim")
            return
        
        # Load and display cache data
        with cache_file.open("r") as f:
            cache_data = json.load(f)
        
        if show_raw:
            # Display raw JSON
            console.print_json(data=cache_data)
            return
        
        # Display formatted cache information
        console.print(
            Panel.fit(
                "[bold]Codex Detection Cache[/bold]",
                border_style="blue",
            )
        )
        console.print()
        
        # Headers table
        if "headers" in cache_data:
            headers_table = Table(title="Detected Headers", show_header=True, header_style="bold magenta")
            headers_table.add_column("Header", style="cyan")
            headers_table.add_column("Value", style="green")
            
            headers = cache_data["headers"]
            for key, value in headers.items():
                if key.lower() not in ["authorization", "cookie"]:  # Don't show sensitive headers
                    headers_table.add_row(key, str(value))
            
            console.print(headers_table)
            console.print()
        
        # Instructions info
        if "instructions" in cache_data:
            instructions = cache_data["instructions"]
            inst_table = Table(title="Detected Instructions", show_header=True, header_style="bold magenta")
            inst_table.add_column("Field", style="cyan")
            inst_table.add_column("Value", style="green")
            
            if "instructions_field" in instructions:
                inst_text = instructions["instructions_field"]
                # Show truncated version
                if len(inst_text) > 200:
                    inst_text = inst_text[:200] + "..."
                inst_table.add_row("Instructions", inst_text)
            
            console.print(inst_table)
            console.print()
        
        # Metadata
        if "codex_version" in cache_data:
            meta_table = Table(title="Cache Metadata", show_header=True, header_style="bold magenta")
            meta_table.add_column("Field", style="cyan")
            meta_table.add_column("Value", style="green")
            
            meta_table.add_row("Codex Version", cache_data.get("codex_version", "Unknown"))
            meta_table.add_row("Account ID", cache_data.get("account_id", "Not set"))
            meta_table.add_row("Cache File", str(cache_file))
            
            console.print(meta_table)
            console.print()
        
        # Display tips
        console.print("[dim]Tip: Use --clear to remove the cache and re-detect on next use[/dim]")
        console.print("[dim]Tip: Use --raw to see the complete cache data as JSON[/dim]")
        
    except json.JSONDecodeError:
        toolkit.print("Cache file is corrupted", tag="error")
        toolkit.print("Consider clearing the cache with --clear", tag="info")
        raise typer.Exit(1)
    except Exception as e:
        toolkit.print(f"Error reading cache: {e}", tag="error")
        raise typer.Exit(1) from e


@app.command(name="test")
def codex_test(
    endpoint: str = typer.Option(
        "chat",
        "--endpoint",
        "-e",
        help="Endpoint to test: 'chat' for /codex/chat/completions or 'response' for /codex/responses",
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Test streaming response",
    ),
) -> None:
    """Test Codex API connectivity and configuration.
    
    This command sends a simple test request to verify that:
    - OpenAI credentials are configured
    - The Codex backend is accessible
    - Response conversion is working
    """
    toolkit = get_rich_toolkit()
    console = Console()
    
    try:
        import httpx
        from ccproxy.config.settings import get_settings
        
        settings = get_settings()
        
        if not settings.codex.enabled:
            toolkit.print("Codex provider is disabled in configuration", tag="error")
            raise typer.Exit(1)
        
        # Check for credentials
        token_file = Path.home() / ".codex" / "auth.json"
        if not token_file.exists():
            toolkit.print("No OpenAI credentials found", tag="error")
            toolkit.print("Run 'ccproxy auth login --provider openai' first", tag="info")
            raise typer.Exit(1)
        
        # Prepare test request
        base_url = f"http://{settings.server.host}:{settings.server.port}"
        
        if endpoint == "chat":
            url = f"{base_url}/codex/chat/completions"
            payload = {
                "model": "gpt-5",
                "messages": [
                    {"role": "user", "content": "Say 'Hello from Codex test!'"}
                ],
                "stream": stream,
                "max_tokens": 50,
            }
        else:
            url = f"{base_url}/codex/responses"
            payload = {
                "model": "gpt-5",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Say 'Hello from Codex test!'"}
                        ],
                    }
                ],
                "stream": stream,
            }
        
        console.print(f"[bold]Testing Codex API[/bold]")
        console.print(f"Endpoint: {url}")
        console.print(f"Streaming: {stream}")
        console.print()
        
        # Send request
        with httpx.Client(timeout=30.0) as client:
            if stream:
                with client.stream("POST", url, json=payload) as response:
                    if response.status_code == 200:
                        toolkit.print("✓ Connection successful", tag="success")
                        console.print("\n[bold]Response stream:[/bold]")
                        for line in response.iter_lines():
                            if line.startswith("data: "):
                                console.print(f"[dim]{line}[/dim]")
                    else:
                        toolkit.print(f"✗ Request failed: {response.status_code}", tag="error")
                        console.print(response.text)
            else:
                response = client.post(url, json=payload)
                if response.status_code == 200:
                    toolkit.print("✓ Connection successful", tag="success")
                    console.print("\n[bold]Response:[/bold]")
                    console.print_json(data=response.json())
                else:
                    toolkit.print(f"✗ Request failed: {response.status_code}", tag="error")
                    console.print(response.text)
        
    except httpx.ConnectError:
        toolkit.print("Could not connect to CCProxy server", tag="error")
        toolkit.print(f"Make sure the server is running on {settings.server.host}:{settings.server.port}", tag="info")
        raise typer.Exit(1)
    except Exception as e:
        toolkit.print(f"Test failed: {e}", tag="error")
        raise typer.Exit(1) from e