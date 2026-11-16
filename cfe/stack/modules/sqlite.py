"""SQLite database. The zero-dependency default for local development and tests.

Reference module: copy this shape when writing a new one.
"""

from pathlib import Path

from cfe.stack.module import EnvVar, Module


class SQLite(Module):
    name = "sqlite"
    label = "SQLite"
    description = "File-backed SQLite database for local development and tests."
    provides = ("database",)
    env = (EnvVar("SQLITE_PATH", default="db.sqlite3", help="Path to the database file."),)

    def settings(self, env):
        return {
            "DATABASES": {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(Path(env.SQLITE_PATH)),
                }
            }
        }

    def checks(self, env):
        parent = Path(env.SQLITE_PATH).resolve().parent
        return [] if parent.exists() else [f"SQLITE_PATH directory does not exist: {parent}"]


MODULES = [SQLite]
