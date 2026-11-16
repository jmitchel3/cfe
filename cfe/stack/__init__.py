"""cfe.stack: a module system for Django projects.

Install with `pip install "cfe[stack]"` (or `cfe[all]` for every first-party
module's SDK). See GOAL.md and docs/stack.md.
"""

from .exceptions import StackConfigError
from .loader import configure, current
from .module import KNOWN_SLOTS, Env, EnvVar, Module

__all__ = [
    "KNOWN_SLOTS",
    "Env",
    "EnvVar",
    "Module",
    "StackConfigError",
    "configure",
    "current",
]
