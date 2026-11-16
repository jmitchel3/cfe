"""Console email: prints messages to stdout. For development and tests."""

from cfe.stack.module import Module


class ConsoleEmail(Module):
    name = "console"
    label = "Console email"
    description = "Writes outgoing email to stdout instead of sending it."
    provides = ("email",)

    def settings(self, env):
        return {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}


MODULES = [ConsoleEmail]
