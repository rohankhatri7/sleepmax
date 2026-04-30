"""Gemini Flash implementation of `InsightGenerator` (Agent 4).

Uses Google's `google-genai` SDK and the `gemini-2.0-flash` model. Free-tier
limits at the time of writing: 15 requests/min, 1M tokens/min, 1500 requests/day.
For a single-user daily digest this is well within bounds. Transient 429 / 5xx
responses are retried with exponential backoff; other failures surface as
`InsightGeneratorError`.

Output is requested as structured JSON (`response_mime_type="application/json"`)
so we don't have to regex-parse prose. If the model returns malformed JSON we
raise rather than silently degrade.
"""

import json
import logging
import random
import time
from typing import Any

from backend.agents.insights.base import (
    InsightGenerator,
    InsightGeneratorError,
    InsightOutput,
    PatternInput,
    RateLimitedError,
)

try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover - import-time fallback
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "gemini-2.0-flash"

SYSTEM_INSTRUCTION = """You are a sleep insights assistant for a personal sleep analytics app.

You translate already-discovered statistical patterns into clear, personal,
actionable advice. You do NOT do statistical analysis yourself, you do NOT
invent correlations, and you do NOT cite numbers that aren't in the input.
Every recommendation you give must be grounded in one of the supplied patterns.

You will be given a JSON object with:
  - patterns: a ranked list of correlations between context variables (e.g.
    meeting_count, exercise_min, temp_high_c) and sleep metrics (e.g.
    deep_min, total_duration_min, efficiency).
  - recent_sleep_summary (optional): short summary of the user's recent sleep,
    for color only — never as the source of statistical claims.

Return JSON matching the requested schema:
  - insights: a list of short, specific, actionable insight strings (one per
    notable pattern, ordered by importance). Reference the user's own data,
    not generic advice. Prefer concrete actions ("schedule fewer than 4
    meetings on Mondays") over vague ones ("manage your schedule").
  - weekly_digest: a 2–4 sentence narrative summary of the past week,
    weaving in the most important patterns.

If the patterns list is sparse or low-confidence, say so honestly rather than
overstating findings. Never fabricate statistics."""


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {"type": "string"},
        },
        "weekly_digest": {"type": "string"},
    },
    "required": ["insights", "weekly_digest"],
}

# Status codes worth retrying. 429 = rate limit, 5xx = transient server error.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class GeminiInsightGenerator(InsightGenerator):
    """Generates insights via Gemini Flash, with retry-on-transient and JSON output."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_attempts: int = 3,
        base_backoff_s: float = 1.0,
        client: Any = None,
    ) -> None:
        if not api_key and client is None:
            raise InsightGeneratorError("GEMINI_API_KEY is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        self.model = model
        self.max_attempts = max_attempts
        self.base_backoff_s = base_backoff_s

        if client is not None:
            self._client = client
        else:
            if genai is None:
                raise InsightGeneratorError(
                    "google-genai is not installed. Run `pip install google-genai`."
                )
            self._client = genai.Client(api_key=api_key)

    def generate(
        self,
        patterns: list[PatternInput] | list[dict],
        recent_sleep_summary: dict | None = None,
    ) -> InsightOutput:
        coerced = self._coerce_patterns(patterns)

        # Empty patterns: don't burn quota, return a stock digest. The model
        # has nothing to ground recommendations in and would risk fabrication.
        if not coerced:
            return InsightOutput(
                insights=[],
                weekly_digest=(
                    "Not enough data yet to surface personal patterns. Keep logging "
                    "sleep — the system needs at least 14 nights with context before "
                    "patterns become reliable."
                ),
                model=self.model,
            )

        prompt = self._build_prompt(coerced, recent_sleep_summary)
        raw_json = self._call_with_retry(prompt)
        return self._parse_response(raw_json)

    def _build_prompt(
        self,
        patterns: list[PatternInput],
        recent_sleep_summary: dict | None,
    ) -> str:
        payload = {
            "patterns": [p.to_dict() for p in patterns],
            "recent_sleep_summary": recent_sleep_summary or {},
        }
        return json.dumps(payload, default=str, indent=2)

    def _call_with_retry(self, user_payload: str) -> str:
        config = self._build_config()
        last_status: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=user_payload,
                    config=config,
                )
            except Exception as exc:
                status = self._status_code(exc)
                last_status = status
                if status in RETRYABLE_STATUS and attempt < self.max_attempts:
                    delay = self.base_backoff_s * (2 ** (attempt - 1))
                    delay += random.uniform(0, self.base_backoff_s)
                    logger.warning(
                        "Gemini call failed with status %s (attempt %d/%d); retrying in %.2fs",
                        status, attempt, self.max_attempts, delay,
                    )
                    time.sleep(delay)
                    continue
                if status == 429:
                    raise RateLimitedError(
                        f"Gemini rate limit hit; exhausted {self.max_attempts} attempts"
                    ) from exc
                raise InsightGeneratorError(
                    f"Gemini call failed (status={status}): {exc}"
                ) from exc

            text = getattr(response, "text", None)
            if not text:
                raise InsightGeneratorError("Gemini returned an empty response")
            return text

        # Loop exhausted only via continue path; status was retryable each time.
        if last_status == 429:
            raise RateLimitedError(
                f"Gemini rate limit hit; exhausted {self.max_attempts} attempts"
            )
        raise InsightGeneratorError(
            f"Gemini call failed after {self.max_attempts} attempts (last status={last_status})"
        )

    def _build_config(self) -> Any:
        # genai_types may be None in environments without the SDK; in that case
        # we'd never reach here because __init__ would have raised.
        if genai_types is None:
            return {
                "system_instruction": SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "response_schema": RESPONSE_SCHEMA,
            }
        return genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        # google.genai.errors.APIError exposes .code; other errors may not.
        for attr in ("code", "status_code"):
            val = getattr(exc, attr, None)
            if isinstance(val, int):
                return val
        return None

    def _parse_response(self, raw_json: str) -> InsightOutput:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise InsightGeneratorError(
                f"Gemini returned non-JSON output: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise InsightGeneratorError(
                f"Gemini output is not a JSON object: {type(data).__name__}"
            )

        insights = data.get("insights", [])
        digest = data.get("weekly_digest", "")
        if not isinstance(insights, list) or not all(isinstance(s, str) for s in insights):
            raise InsightGeneratorError("Gemini output `insights` must be a list of strings")
        if not isinstance(digest, str):
            raise InsightGeneratorError("Gemini output `weekly_digest` must be a string")

        return InsightOutput(insights=insights, weekly_digest=digest, model=self.model)
