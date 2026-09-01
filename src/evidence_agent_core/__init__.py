"""Evidence Agent Core public API."""

from .core import AgentCore, CoreError
from .coordination import CoordinationPlane

__all__ = ["AgentCore", "CoordinationPlane", "CoreError"]
__version__ = "0.4.0"
