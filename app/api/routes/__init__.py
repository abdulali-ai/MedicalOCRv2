"""API routes. Auth stays in the package and is not mounted."""
from . import health, pdf

__all__ = ["health", "pdf"]
