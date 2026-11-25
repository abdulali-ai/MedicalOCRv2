"""API routes. Auth stays in the package and is not mounted."""
from . import epdf, health, pdf

__all__ = ["epdf", "health", "pdf"]
