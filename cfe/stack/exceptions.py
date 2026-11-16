try:
    from django.core.exceptions import ImproperlyConfigured as _Base
except ImportError:  # pragma: no cover - cfe.stack requires django, but keep the CLI importable
    _Base = Exception


class StackConfigError(_Base):
    """The CFE_MODULES selection or its env vars are invalid."""
