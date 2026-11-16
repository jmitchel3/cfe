"""Apply a stack to a settings dict exactly the way a project's settings.py does."""

from cfe.stack import loader


def apply_stack(modules, environ=None, ns=None, strict=True):
    ns = {} if ns is None else ns
    stack = loader.load(modules, environ=environ if environ is not None else {}, strict=strict)
    ns["INSTALLED_APPS"] = [*ns.get("INSTALLED_APPS", []), *stack.installed_apps]
    ns["MIDDLEWARE"] = stack.middleware(ns.get("MIDDLEWARE", []))
    if "DATABASES" in stack.settings():
        ns["DATABASES"] = stack.databases(ns.get("DATABASES"))
    if "CACHES" in stack.settings():
        ns["CACHES"] = stack.caches(ns.get("CACHES"))
    if "STORAGES" in stack.settings():
        ns["STORAGES"] = stack.storages(ns.get("STORAGES"))
    ns.update(stack.extra_settings())
    return ns
