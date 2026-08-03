"""Allow-listed tool adapters used by domain agents."""

from .registry import ToolNotAllowedError, ToolRegistry

__all__ = ["ToolNotAllowedError", "ToolRegistry"]
