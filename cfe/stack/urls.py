"""Mount enabled modules' URLs.

In the project's urls.py:

    from cfe.stack.urls import module_urlpatterns
    urlpatterns = [..., *module_urlpatterns()]
"""

from django.conf import settings
from django.urls import include, path

from . import registry


def module_urlpatterns():
    patterns = []
    for name in getattr(settings, "CFE_STACK_MODULES", []):
        cls = registry.get(name)
        if not cls.urls:
            continue
        prefix = cls.url_prefix if cls.url_prefix is not None else f"{cls.name}/"
        patterns.append(path(prefix, include(cls.urls)))
    return patterns
