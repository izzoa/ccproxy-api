"""CLI commands for interacting with plugins."""

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

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
        loader = PluginLoader(auto_install=auto_install, require_user_consent=True)

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

        # Generate dependency report
        report = loader.get_dependency_report(plugin_dirs)

        # Display system checks
        system_checks = report["system_checks"]

        console.print(
            Panel.fit(
                f"🔧 System Requirements\n"
                f"Python: {system_checks['python_version']['version']} "
                f"{'✅' if system_checks['python_version']['meets_minimum'] else '❌'}\n"
                f"uv: {'✅' if system_checks['uv_available'] else '❌'} "
                f"{system_checks.get('uv_version', 'Not available')}",
                title="System Status",
            )
        )

        # Display summary
        console.print(
            Panel.fit(
                f"📦 Plugin Summary\n"
                f"Total plugins: {report['total_plugins']}\n"
                f"With dependencies: {report['plugins_with_dependencies']}\n"
                f"All satisfied: {report['plugins_satisfied']}\n"
                f"With issues: {report['plugins_with_issues']}",
                title="Dependencies Overview",
            )
        )

        if detailed or report["plugins_with_issues"] > 0:
            # Show detailed plugin information
            for plugin_detail in report["plugin_details"]:
                if not detailed and plugin_detail["all_satisfied"]:
                    continue

                tree = Tree(f"[bold]{plugin_detail['name']}[/bold]")

                if plugin_detail["error"]:
                    tree.add(f"[red]Error: {plugin_detail['error']}[/red]")
                elif not plugin_detail["has_pyproject"]:
                    tree.add("[dim]No pyproject.toml (no dependencies)[/dim]")
                elif plugin_detail["total_dependencies"] == 0:
                    tree.add("[dim]No dependencies specified[/dim]")
                else:
                    status = (
                        "✅ All satisfied"
                        if plugin_detail["all_satisfied"]
                        else f"❌ {plugin_detail['missing_count']} missing"
                    )
                    tree.add(f"Status: {status}")

                    if "dependencies" in plugin_detail:
                        deps_tree = tree.add("Dependencies")
                        for dep in plugin_detail["dependencies"]:
                            status_icon = "✅" if dep["satisfied"] else "❌"
                            version_info = (
                                f" ({dep['version']})" if dep["version"] else ""
                            )
                            error_info = f" - {dep['error']}" if dep["error"] else ""
                            deps_tree.add(
                                f"{status_icon} {dep['name']}{version_info}{error_info}"
                            )

                console.print(tree)
                console.print()

        # Offer to install missing dependencies if any
        if auto_install and report["plugins_with_issues"] > 0:
            console.print(
                "[yellow]Attempting to resolve missing dependencies...[/yellow]"
            )

            for plugin_detail in report["plugin_details"]:
                if (
                    not plugin_detail["all_satisfied"]
                    and plugin_detail["has_pyproject"]
                ):
                    plugin_dir = Path(plugin_detail["path"])
                    console.print(
                        f"Resolving dependencies for {plugin_detail['name']}..."
                    )

                    success = await loader.resolve_plugin_dependencies(plugin_dir)
                    if success:
                        console.print(
                            f"[green]✅ Resolved dependencies for {plugin_detail['name']}[/green]"
                        )
                    else:
                        console.print(
                            f"[red]❌ Failed to resolve dependencies for {plugin_detail['name']}[/red]"
                        )

    asyncio.run(_check_deps())
