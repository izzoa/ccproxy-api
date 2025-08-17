"""CLI commands for interacting with plugins."""

import typer
from rich.console import Console
from rich.table import Table

from ccproxy.plugins.loader import PluginLoader


app = typer.Typer(name="plugins", help="Manage and inspect plugins.")


@app.command()
def settings() -> None:
    """List all available plugin settings."""
    console = Console()
    loader = PluginLoader()
    plugins_with_paths = loader.load_plugins_with_paths()

    if not plugins_with_paths:
        console.print("No plugins found.")
        return

    for plugin, _ in plugins_with_paths:
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
