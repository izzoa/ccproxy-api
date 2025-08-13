"""CLI commands for interacting with plugins."""

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from ccproxy.plugins.loader import PluginLoader


app = typer.Typer(name="plugins", help="Manage and inspect plugins.")


@app.command()
def settings() -> None:
    """List all available plugin settings."""
    console = Console()
    import asyncio

    loader = PluginLoader()
    plugins = asyncio.run(loader.load_plugins())

    if not plugins:
        console.print("No plugins found.")
        return

    for plugin in plugins:
        table = Table(
            title=f"Plugin: [bold]{plugin.name}[/bold] v{plugin.version}",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Setting", style="dim")
        table.add_column("Type")
        table.add_column("Default")

        config_class = plugin.get_config_class()
        if not config_class:
            console.print(f"Plugin: [bold]{plugin.name}[/bold] v{plugin.version}")
            console.print("  No configuration settings.")
            console.print()
            continue

        schema = config_class.model_json_schema()
        properties = schema.get("properties", {})

        for key, prop in properties.items():
            # Extract type, default, and description
            prop_type = prop.get("type", "any")
            default_value = prop.get("default", "(none)")

            # Format default value for display
            if isinstance(default_value, dict | list):
                import json

                default_str = json.dumps(default_value)
            else:
                default_str = str(default_value)

            table.add_row(key, prop_type, default_str)

        console.print(table)
        console.print()


@app.command()
def dependencies(
    auto_install: bool = typer.Option(
        False, "--auto-install", help="Automatically install missing dependencies"
    ),
    detailed: bool = typer.Option(
        False, "--detailed", help="Show detailed dependency information"
    ),
) -> None:
    """Check and manage plugin dependencies."""

    async def _check_deps() -> None:
        console = Console()
        loader = PluginLoader()

        # Get all plugin directories
        from pathlib import Path

        possible_locations = [
            Path(__file__).parent.parent.parent.parent / "plugins",
            Path(__file__).parent.parent.parent / "plugins",
        ]

        plugin_dirs = []
        for location in possible_locations:
            if location.exists() and location.is_dir():
                for subdir in location.iterdir():
                    if subdir.is_dir() and not subdir.name.startswith("_"):
                        plugin_dirs.append(subdir)
                break

        if not plugin_dirs:
            console.print("[red]No plugin directories found[/red]")
            return

        # Dependency report is deprecated
        console.print(
            "[yellow]Plugin dependency management is now handled at the package level via pyproject.toml[/yellow]"
        )

    asyncio.run(_check_deps())
