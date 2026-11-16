"""In-process memory cache for tests and local development. No env vars."""

from cfe.stack.module import Module


class LocMem(Module):
    name = "locmem"
    label = "Local memory cache"
    description = "Django's LocMemCache: per-process, for tests and development."
    provides = ("cache",)

    def settings(self, env):
        return {
            "CACHES": {
                "default": {
                    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                    "LOCATION": "cfe-stack",
                }
            }
        }


MODULES = [LocMem]
