"""CFE CLI - Build your Python apps faster with battle-tested components."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .components import get_component, list_components
from .detectors.package_manager import detect_package_manager, install_packages
from .detectors.project_type import detect_project

app = typer.Typer(
    name="cfe",
    help="Build your Python apps faster with battle-tested components, templates, and more.",
    no_args_is_help=True,
)

console = Console()


@app.command()
def add(
    component: str = typer.Argument(
        ..., help="Component to add (e.g., tailwind, htmx)"
    ),
    project_path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to project directory (defaults to current directory)",
    ),
):
    """Add a component to your Python project with automatic configuration."""
    project_path = project_path or Path.cwd()

    console.print(f"\n[bold cyan]🚀 CFE - Adding {component}[/bold cyan]\n")

    # Detect project type
    console.print("[dim]→[/dim] Detecting project type...")
    project_info = detect_project(project_path)

    if not project_info["detected"]:
        console.print("[bold red]✗[/bold red] Could not detect project type")
        console.print("  Currently supported: Django")
        console.print(
            "\n[yellow]💬 Want FastAPI, Flask, or other framework support?[/yellow]"
        )
        console.print("[yellow]   Send me a message on X: @justinmitchel[/yellow]\n")
        raise typer.Exit(code=1)

    # Check if detected framework is Django
    if project_info["type"].lower() != "django":
        console.print(
            f"[bold yellow]⚠[/bold yellow]  Detected {project_info['type']} project"
        )
        console.print(
            "[yellow]  Currently, CFE only supports Django projects.[/yellow]"
        )
        console.print("\n[yellow]💬 Want support for your framework?[/yellow]")
        console.print("[yellow]   Send me a message on X: @justinmitchel[/yellow]\n")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] Detected {project_info['type']} project")

    # Detect package manager
    console.print("[dim]→[/dim] Detecting package manager...")
    pkg_manager = detect_package_manager(project_path)
    console.print(f"[bold green]✓[/bold green] Using {pkg_manager}")

    # Get component configuration
    try:
        component_config = get_component(component, project_info["type"])
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1)

    # Install packages
    if component_config.get("packages"):
        console.print("\n[dim]→[/dim] Installing packages...")
        for package in component_config["packages"]:
            console.print(f"  • {package}")

        success = install_packages(
            component_config["packages"], pkg_manager, project_path
        )

        if not success:
            console.print("[bold red]✗[/bold red] Failed to install packages")
            raise typer.Exit(code=1)

        console.print("[bold green]✓[/bold green] Packages installed")

        # Apply configurations
        if component_config.get("configure"):
            console.print("\n[dim]→[/dim] Applying configurations...")
            component_config["configure"](project_info, project_path)
            console.print("[bold green]✓[/bold green] Configuration applied")

    # Show next steps
    if component_config.get("next_steps"):
        console.print(
            Panel(
                "\n".join(f"• {step}" for step in component_config["next_steps"]),
                title="[bold]Next Steps[/bold]",
                border_style="cyan",
            )
        )

    console.print(f"\n[bold green]✨ Successfully added {component}![/bold green]\n")


@app.command()
def list():
    """List all available components."""
    console.print("\n[bold cyan]📦 Available Components[/bold cyan]\n")
    components = list_components()

    for name, info in components.items():
        console.print(f"[bold]{name}[/bold]")
        console.print(f"  {info['description']}")
        console.print(f"  Supports: {', '.join(info['frameworks'])}\n")


@app.command()
def init(
    project_path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to project directory (defaults to current directory)",
    ),
):
    """Detect and display project information."""
    project_path = project_path or Path.cwd()

    console.print("\n[bold cyan]🔍 Project Information[/bold cyan]\n")

    project_info = detect_project(project_path)
    pkg_manager = detect_package_manager(project_path)

    if project_info["detected"]:
        console.print(f"[bold]Type:[/bold] {project_info['type']}")
        console.print(f"[bold]Path:[/bold] {project_path}")
        console.print(f"[bold]Package Manager:[/bold] {pkg_manager}")

        if project_info.get("config_files"):
            console.print("\n[bold]Configuration Files:[/bold]")
            for file in project_info["config_files"]:
                console.print(f"  • {file}")

        # Show warning if not Django
        if project_info["type"].lower() != "django":
            console.print(
                "\n[yellow]⚠  Note: CFE currently only supports Django projects[/yellow]"
            )
            console.print(
                "[yellow]   Want support for your framework? DM me on X: @justinmitchel[/yellow]"
            )
    else:
        console.print("[yellow]⚠ No supported project detected[/yellow]")
        console.print("  Currently supported: Django")
        console.print(
            "\n[yellow]💬 Want FastAPI, Flask, or other framework support?[/yellow]"
        )
        console.print("[yellow]   Send me a message on X: @justinmitchel[/yellow]")

    console.print()


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
