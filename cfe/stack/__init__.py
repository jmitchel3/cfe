"""cfe.stack: a module system for Django projects.

Install with `pip install "cfe[stack]"` (or `cfe[all]` for every first-party
module's SDK). See docs/stack.md.
"""

from .exceptions import StackConfigError
from .loader import Stack, load, runtime
from .module import KNOWN_SLOTS, Env, EnvVar, Module

__all__ = [
    "KNOWN_SLOTS",
    "Env",
    "EnvVar",
    "Module",
    "Stack",
    "StackConfigError",
    "load",
    "runtime",
]
