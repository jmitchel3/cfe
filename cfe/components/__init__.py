"""Component registry and file-based component system."""

import json
from pathlib import Path
from typing import Any, Dict

from ..utils import copy_template_file


def get_components_dir() -> Path:
    """Get the components directory path."""
    return Path(__file__).parent


def load_component_config(component_name: str) -> Dict[str, Any]:
    """Load component configuration from component.json."""
    components_dir = get_components_dir()
    config_file = components_dir / component_name / "component.json"

    if not config_file.exists():
        raise ValueError(f"Component '{component_name}' not found")

    with open(config_file, "r") as f:
        return json.load(f)


def get_component(name: str, framework: str) -> Dict[str, Any]:
    """
    Get component configuration and prepare for installation.

    Raises ValueError if component not found or not supported for framework.
    """
    try:
        config = load_component_config(name)
    except ValueError:
        available = list_components()
        raise ValueError(
            f"Component '{name}' not found. Available: {', '.join(available.keys())}"
        )

    framework_lower = framework.lower()
    if framework_lower not in config["frameworks"]:
        raise ValueError(
            f"Component '{name}' is not supported for {framework}. "
            f"Supported: {', '.join(config['frameworks'])}"
        )

    # Add installation function
    config["configure"] = lambda project_info, project_path: install_component(
        name, framework_lower, project_info, project_path
    )

    return config


def install_component(
    component_name: str,
    framework: str,
    project_info: Dict[str, Any],
    project_path: Path,
):
    """Install component files into the project."""
    components_dir = get_components_dir()
    component_dir = components_dir / component_name / framework

    if not component_dir.exists():
        raise ValueError(
            f"Component '{component_name}' does not have {framework} support"
        )

    # Copy all files from component directory to project
    for item in component_dir.rglob("*"):
        if item.is_file():
            # Calculate relative path from component framework dir
            rel_path = item.relative_to(component_dir)
            dest_path = project_path / rel_path

            # Create parent directories if needed
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file with template variable substitution
            copy_template_file(
                item,
                dest_path,
                {
                    "PROJECT_NAME": project_path.name,
                    "FRAMEWORK": framework,
                },
            )


def list_components() -> Dict[str, Dict[str, Any]]:
    """List all available components by scanning the components directory."""
    components_dir = get_components_dir()
    components = {}

    for component_dir in components_dir.iterdir():
        if component_dir.is_dir() and not component_dir.name.startswith("_"):
            config_file = component_dir / "component.json"
            if config_file.exists():
                try:
                    with open(config_file, "r") as f:
                        config = json.load(f)
                        components[config["name"]] = {
                            "description": config["description"],
                            "frameworks": config["frameworks"],
                            "version": config.get("version", "unknown"),
                        }
                except Exception:
                    continue

    return components
