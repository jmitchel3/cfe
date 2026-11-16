from importlib import import_module

from django.apps import AppConfig
from django.conf import settings


def import_string(path: str):
    module_path, _, attr = path.replace(":", ".").rpartition(".")
    return getattr(import_module(module_path), attr)


class StackConfig(AppConfig):
    name = "cfe.stack"
    label = "cfe_stack"
    verbose_name = "CFE Stack"

    def ready(self) -> None:
        from . import hooks, loader, registry

        stack = loader.current()
        enabled = getattr(settings, "CFE_STACK_MODULES", [])
        if [m.name for m in stack.modules] != list(enabled):
            # configure() ran in another process or was bypassed; rebuild from
            # settings without re-validating env (settings already loaded).
            stack = loader.Stack(modules=[registry.get(n)() for n in enabled])

        for module in stack.modules:
            for hook_name, target in module.hooks.items():
                hooks.register(hook_name, import_string(target))
            if module.nav:
                items = list(module.nav)
                hooks.add_filter(
                    "dashboard_nav", lambda value, _items=items, **kw: [*value, *_items]
                )
            module.ready()
