"""Inline tasks: `.delay()` runs the function immediately. For dev and tests."""

from cfe.stack.module import Module


class Sync(Module):
    name = "sync"
    label = "Sync tasks"
    description = "Runs background tasks inline, in the calling process."
    provides = ("tasks",)

    def settings(self, env):
        return {"CFE_TASKS_BACKEND": "sync"}


MODULES = [Sync]
