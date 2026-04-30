"""Agent 3 — Pattern Discovery: correlations, lag, binning, FDR."""

from backend.agents.discovery.discover import (
    BINNED_VARS,
    CONTEXT_FIELDS,
    HIGHER_IS_BETTER,
    LAGS,
    SLEEP_METRICS,
    PatternResult,
    discover_patterns,
)
from backend.agents.discovery.persist import persist_patterns

__all__ = [
    "BINNED_VARS",
    "CONTEXT_FIELDS",
    "HIGHER_IS_BETTER",
    "LAGS",
    "SLEEP_METRICS",
    "PatternResult",
    "discover_patterns",
    "persist_patterns",
]
