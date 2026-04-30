"""Context adapters — enrich sleep data with environmental and behavioral signals."""

from backend.agents.context.base import ContextAdapter
from backend.agents.context.weather import WeatherAdapter

__all__ = ["ContextAdapter", "WeatherAdapter"]
