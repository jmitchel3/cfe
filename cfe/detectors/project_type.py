"""Project type detection logic."""

from pathlib import Path
from typing import Any, Dict


def detect_django(project_path: Path) -> Dict[str, Any]:
    """Detect if the project is a Django project."""
    # Look for manage.py or settings.py
    manage_py = project_path / "manage.py"

    if manage_py.exists():
        # Try to find settings.py
        settings_files = list(project_path.rglob("settings.py"))
        config_files = ["manage.py"]

        if settings_files:
            config_files.extend(
                [str(f.relative_to(project_path)) for f in settings_files[:3]]
            )

        return {
            "detected": True,
            "type": "Django",
            "config_files": config_files,
            "settings_path": settings_files[0] if settings_files else None,
            "project_root": project_path,
        }

    return {"detected": False}


def detect_fastapi(project_path: Path) -> Dict[str, Any]:
    """Detect if the project is a FastAPI project."""
    # Look for common FastAPI patterns
    main_files = ["main.py", "app.py", "app/main.py", "src/main.py"]

    for main_file in main_files:
        file_path = project_path / main_file
        if file_path.exists():
            try:
                content = file_path.read_text()
                if "fastapi" in content.lower() and (
                    "FastAPI" in content or "from fastapi" in content
                ):
                    return {
                        "detected": True,
                        "type": "FastAPI",
                        "config_files": [main_file],
                        "main_file": file_path,
                        "project_root": project_path,
                    }
            except Exception:
                continue

    return {"detected": False}


def detect_flask(project_path: Path) -> Dict[str, Any]:
    """Detect if the project is a Flask project."""
    # Look for common Flask patterns
    app_files = ["app.py", "application.py", "main.py", "app/__init__.py"]

    for app_file in app_files:
        file_path = project_path / app_file
        if file_path.exists():
            try:
                content = file_path.read_text()
                if "flask" in content.lower() and (
                    "Flask" in content or "from flask" in content
                ):
                    return {
                        "detected": True,
                        "type": "Flask",
                        "config_files": [app_file],
                        "app_file": file_path,
                        "project_root": project_path,
                    }
            except Exception:
                continue

    return {"detected": False}


def detect_project(project_path: Path) -> Dict[str, Any]:
    """
    Detect the type of Python project.

    Returns a dictionary with project information:
    - detected: bool - Whether a project was detected
    - type: str - Project type (Django, FastAPI, Flask, etc.)
    - config_files: List[str] - Important configuration files
    - *_path: Path - Framework-specific file paths
    """
    detectors = [
        detect_django,
        detect_fastapi,
        detect_flask,
    ]

    for detector in detectors:
        result = detector(project_path)
        if result["detected"]:
            return result

    return {"detected": False, "type": None}
