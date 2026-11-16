"""Local filesystem media storage. The default for development."""

from cfe.stack.module import EnvVar, Module


class LocalStorage(Module):
    name = "local"
    label = "Local filesystem storage"
    description = "Media files on the local disk via FileSystemStorage."
    provides = ("storage",)
    env = (
        EnvVar("MEDIA_ROOT", default="media", help="Directory for uploaded files."),
        EnvVar("MEDIA_URL", default="/media/", help="URL prefix for uploaded files."),
    )

    def settings(self, env):
        return {
            "MEDIA_ROOT": env.MEDIA_ROOT,
            "MEDIA_URL": env.MEDIA_URL,
            "STORAGES": {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": env.MEDIA_ROOT, "base_url": env.MEDIA_URL},
                }
            },
        }


MODULES = [LocalStorage]
