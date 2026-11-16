"""Package manager detection and installation logic."""

import subprocess
from pathlib import Path
from typing import List


def detect_package_manager(project_path: Path) -> str:
    """
    Detect the package manager being used in the project.

    Priority: uv > poetry > pip
    """
    # Check for uv.lock or .venv managed by uv
    if (project_path / "uv.lock").exists():
        return "uv"

    # Check for pyproject.toml with poetry
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            if "[tool.poetry]" in content:
                return "poetry"
            # Check for uv in pyproject.toml
            if "[tool.uv]" in content:
                return "uv"
        except Exception:
            pass

    # Check for poetry.lock
    if (project_path / "poetry.lock").exists():
        return "poetry"

    # Check for requirements.txt or Pipfile
    if (project_path / "requirements.txt").exists():
        return "pip"

    if (project_path / "Pipfile").exists():
        return "pipenv"

    # Check if uv is available in PATH
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True, timeout=5)
        return "uv"
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        pass

    # Default to pip
    return "pip"


def install_packages(
    packages: List[str], package_manager: str, project_path: Path
) -> bool:
    """
    Install packages using the detected package manager.

    Returns True if installation was successful.
    """
    commands = {
        "uv": ["uv", "add"] + packages,
        "poetry": ["poetry", "add"] + packages,
        "pip": ["pip", "install"] + packages,
        "pipenv": ["pipenv", "install"] + packages,
    }

    cmd = commands.get(package_manager)
    if not cmd:
        return False

    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
