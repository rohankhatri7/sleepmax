"""Persistence helper for Agent 3 pattern discovery results."""

import json

from sqlalchemy.orm import Session

from backend.agents.discovery.discover import PatternResult
from backend.db.models import DiscoveredPattern


def persist_patterns(patterns: list[PatternResult], session: Session) -> int:
    """Replace `discovered_patterns` with the latest run.

    Pattern discovery is rerun on the full history each time, so the new run
    supersedes the previous one. We delete-all then insert-all in the same
    transaction.

    Returns the number of rows inserted.
    """
    session.query(DiscoveredPattern).delete()

    rows = [
        DiscoveredPattern(
            pattern_type=p.pattern_type,
            context_field=p.context_field,
            sleep_metric=p.sleep_metric,
            correlation_strength=p.correlation,
            confidence=1.0 - p.p_value,
            description=p.description,
            sample_size=p.n,
            p_value=p.p_value,
            lag_days=p.lag_days,
            threshold=p.threshold,
            confidence_label=p.confidence_label,
            confound_flag=p.confound_flag,
            confounded_with=(
                json.dumps(list(p.confounded_with)) if p.confounded_with else None
            ),
        )
        for p in patterns
    ]
    session.add_all(rows)
    session.commit()
    return len(rows)
