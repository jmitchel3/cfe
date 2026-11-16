"""Utility functions for CFE CLI."""

import shutil
from pathlib import Path
from typing import Dict


def copy_template_file(source: Path, dest: Path, variables: Dict[str, str]):
    """
    Copy a file from source to dest, replacing template variables.

    Template variables are in the format {{VARIABLE_NAME}}.
    """
    if source.suffix in [".pyc", ".pyo", ".pyd", ".so", ".dll"]:
        # Binary files - just copy
        shutil.copy2(source, dest)
        return

    try:
        # Try to read as text and replace variables
        content = source.read_text(encoding="utf-8")

        # Replace template variables
        for key, value in variables.items():
            content = content.replace(f"{{{{{key}}}}}", value)

        dest.write_text(content, encoding="utf-8")
    except UnicodeDecodeError:
        # If not text, just copy
        shutil.copy2(source, dest)
