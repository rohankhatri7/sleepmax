"""Abstract base class for context adapters."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class ContextAdapter(ABC):
    """Interface for adapters that provide daily context signals.

    Each adapter returns a partial dict of DailyContext fields.
    The orchestrator merges results from all adapters into one DailyContext row.
    """

    @abstractmethod
    async def fetch(self, target_date: date, **kwargs: Any) -> dict[str, Any]:
        """Fetch context data for a given date.

        Args:
            target_date: The date to fetch context for.
            **kwargs: Adapter-specific parameters (e.g., lat/lon for weather).

        Returns:
            Dict with keys matching DailyContext column names and their values.
            Only include fields this adapter is responsible for.
        """
        ...

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Identifier for this adapter (e.g. 'weather', 'calendar')."""
        ...
