"""composition errors."""


class CompositionCycleError(Exception):
    """Raised when the composition graph contains an import cycle."""
