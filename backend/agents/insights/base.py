"""Abstract base class and shared types for insight generation (Agent 4).

Agent 4 is a translator, not an analyst: it turns structured statistical
findings from Agent 3 into readable, actionable prose. The provider (Gemini,
Claude, OpenAI, local) sits behind this interface so callers don't depend on
any specific SDK.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Required pattern fields. Any input dict missing one of these is rejected —
# this is what enforces the "patterns only, no raw sleep records" contract.
REQUIRED_PATTERN_FIELDS = (
    "pattern_type",
    "context_field",
    "sleep_metric",
    "correlation_strength",
    "confidence",
    "description",
    "sample_size",
)


class InsightGeneratorError(Exception):
    """Base error for insight generation failures (API errors, bad output, etc.)."""


class RateLimitedError(InsightGeneratorError):
    """Raised when the provider's rate limit was hit and retries were exhausted."""


@dataclass
class PatternInput:
    """A single ranked pattern from Agent 3, ready to be passed to the LLM.

    Mirrors the columns of `DiscoveredPattern` so a SQL row can be turned into
    one of these directly.
    """

    pattern_type: str
    context_field: str
    sleep_metric: str
    correlation_strength: float
    confidence: float
    description: str
    sample_size: int

    @classmethod
    def from_dict(cls, d: dict) -> "PatternInput":
        missing = [f for f in REQUIRED_PATTERN_FIELDS if f not in d]
        if missing:
            raise InsightGeneratorError(
                f"pattern dict missing required fields: {missing}. "
                "Agent 4 expects pattern objects from Agent 3, not raw sleep records."
            )
        return cls(
            pattern_type=str(d["pattern_type"]),
            context_field=str(d["context_field"]),
            sleep_metric=str(d["sleep_metric"]),
            correlation_strength=float(d["correlation_strength"]),
            confidence=float(d["confidence"]),
            description=str(d["description"]),
            sample_size=int(d["sample_size"]),
        )

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type,
            "context_field": self.context_field,
            "sleep_metric": self.sleep_metric,
            "correlation_strength": self.correlation_strength,
            "confidence": self.confidence,
            "description": self.description,
            "sample_size": self.sample_size,
        }


@dataclass
class InsightOutput:
    """Result of one insight-generation run."""

    insights: list[str] = field(default_factory=list)
    weekly_digest: str = ""
    model: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InsightGenerator(ABC):
    """Provider-agnostic interface for turning patterns into prose."""

    @abstractmethod
    def generate(
        self,
        patterns: list[PatternInput] | list[dict],
        recent_sleep_summary: dict | None = None,
    ) -> InsightOutput:
        """Translate ranked patterns into natural-language insights + a weekly digest.

        Args:
            patterns: Ranked pattern objects from Agent 3 (or dicts coercible to them).
            recent_sleep_summary: Optional short summary of the past week's sleep
                (averages, notable nights). Used as additional context, never as
                the source of statistical claims.

        Returns:
            InsightOutput with a list of insight strings and a weekly digest.

        Raises:
            InsightGeneratorError: API failure, bad output, contract violation.
            RateLimitedError: Rate limit hit and retries exhausted.
        """
        ...

    @staticmethod
    def _coerce_patterns(
        patterns: list[PatternInput] | list[dict],
    ) -> list[PatternInput]:
        """Validate and normalize the input list. Raises if the contract is broken."""
        if not isinstance(patterns, list):
            raise InsightGeneratorError(
                f"patterns must be a list, got {type(patterns).__name__}"
            )
        out: list[PatternInput] = []
        for i, p in enumerate(patterns):
            if isinstance(p, PatternInput):
                out.append(p)
            elif isinstance(p, dict):
                try:
                    out.append(PatternInput.from_dict(p))
                except InsightGeneratorError as e:
                    raise InsightGeneratorError(f"patterns[{i}]: {e}") from e
            else:
                raise InsightGeneratorError(
                    f"patterns[{i}] must be PatternInput or dict, got {type(p).__name__}"
                )
        return out
