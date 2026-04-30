"""Agent 3 — Pattern Discovery.

Computes correlations between every numeric `DailyContext` field and every
`SleepRecord` metric, with lag-0/1/2 analysis, day-of-week control, optional
binned analysis for non-linear variables, and Benjamini-Hochberg FDR
correction.

Output is a ranked list of `PatternResult` records, each persisted as one row
in `discovered_patterns`. The LLM (Agent 4) consumes these directly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.stats import f_oneway, false_discovery_control, pearsonr, spearmanr

logger = logging.getLogger(__name__)


# Numeric metrics drawn from `SleepRecord`.
SLEEP_METRICS: tuple[str, ...] = (
    "total_duration_min",
    "time_in_bed_min",
    "deep_min",
    "light_min",
    "rem_min",
    "awake_min",
    "efficiency",
    "avg_hr",
)

# Numeric fields drawn from `DailyContext`.
CONTEXT_FIELDS: tuple[str, ...] = (
    "temp_high_c",
    "temp_low_c",
    "humidity_pct",
    "pressure_hpa",
    "precipitation_mm",
    "meeting_count",
    "meeting_hours",
    "back_to_back_count",
    "exercise_min",
)

# Variables that are likely non-linear (sweet spots / thresholds). For these
# we ALSO run a binned analysis on top of the linear correlation.
BINNED_VARS: frozenset[str] = frozenset({
    "temp_high_c",
    "temp_low_c",
    "pressure_hpa",
    "exercise_min",
})

# Sleep metrics where higher = better. The "optimal" bin maximizes these.
HIGHER_IS_BETTER: frozenset[str] = frozenset({
    "total_duration_min",
    "time_in_bed_min",
    "deep_min",
    "light_min",
    "rem_min",
    "efficiency",
})

LAGS: tuple[int, ...] = (0, 1, 2)
DEFAULT_MIN_N = 10
DEFAULT_MIN_BIN_N = 5
DEFAULT_NUM_BINS = 4
DEFAULT_P_THRESHOLD = 0.05
# Below this n, the day-of-week residualization eats too many degrees of
# freedom (7 DOW levels) so we skip it and use raw values.
DOW_RESIDUALIZE_MIN_N = 14


@dataclass
class PatternResult:
    """One discovered pattern, ready to persist or feed to Agent 4."""

    pattern_type: str  # "correlation" | "binned"
    context_field: str
    sleep_metric: str
    correlation: float  # signed Pearson/Spearman r, or normalized effect for binned
    p_value: float  # post-FDR
    p_value_raw: float  # pre-FDR
    lag_days: int
    threshold: str | None  # bin label for binned; None for correlation
    description: str
    n: int
    confidence_label: str  # emerging | strong | very_strong


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _dow_residualize(values: np.ndarray, dows: np.ndarray) -> np.ndarray:
    """Subtract the per-DOW mean from each value (controls for weekday effects)."""
    df = pd.DataFrame({"v": values, "dow": dows})
    means = df.groupby("dow")["v"].transform("mean").to_numpy()
    return values - means


def _confidence_label(n: int, p_corrected: float) -> str:
    if n >= 40 and p_corrected < 0.001:
        return "very_strong"
    if n >= 20 and p_corrected < 0.01:
        return "strong"
    return "emerging"


def _build_lagged_table(
    sleep_df: pd.DataFrame,
    context_df: pd.DataFrame,
) -> pd.DataFrame:
    """Join sleep + context by date, generate lag-1 and lag-2 context columns.

    For lag k, the context value comes from k days BEFORE the sleep date —
    i.e. context that could have caused tonight's sleep.

    Returns a DataFrame with one row per sleep night, plus a `dow` column
    (day of week of the sleep date) and lagged context columns named
    `<field>__lag{0,1,2}`.
    """
    sleep = sleep_df.copy()
    context = context_df.copy()

    sleep["date"] = pd.to_datetime(sleep["date"]).dt.normalize()
    context["date"] = pd.to_datetime(context["date"]).dt.normalize()

    merged = sleep.merge(context, on="date", how="left", suffixes=("", "_ctx"))

    # Lagged context: shift context by -lag days then merge again.
    for lag in LAGS:
        if lag == 0:
            for f in CONTEXT_FIELDS:
                if f in merged.columns:
                    merged[f"{f}__lag0"] = merged[f]
        else:
            shifted = context.copy()
            shifted["date"] = shifted["date"] + pd.Timedelta(days=lag)
            for f in CONTEXT_FIELDS:
                if f not in shifted.columns:
                    continue
                tmp = shifted[["date", f]].rename(columns={f: f"{f}__lag{lag}"})
                merged = merged.merge(tmp, on="date", how="left")

    merged["dow"] = pd.to_datetime(merged["date"]).dt.dayofweek
    return merged


def _correlation_for_pair(
    table: pd.DataFrame,
    sleep_metric: str,
    context_field: str,
    lag: int,
    min_n: int,
) -> tuple[float, float, int] | None:
    """Compute the stronger of Pearson/Spearman for one (sleep, context, lag) pair.

    Returns (signed_r, p_value_raw, n) or None if not enough data.
    """
    col = f"{context_field}__lag{lag}"
    if col not in table.columns or sleep_metric not in table.columns:
        return None

    sub = table[[sleep_metric, col, "dow"]].dropna()
    if len(sub) < min_n:
        return None
    if sub[sleep_metric].nunique() < 2 or sub[col].nunique() < 2:
        return None

    y = sub[sleep_metric].to_numpy(dtype=float)
    x = sub[col].to_numpy(dtype=float)
    dow = sub["dow"].to_numpy()

    if len(sub) >= DOW_RESIDUALIZE_MIN_N:
        y = _dow_residualize(y, dow)
        x = _dow_residualize(x, dow)
        if np.std(y) == 0 or np.std(x) == 0:
            return None

    pearson = pearsonr(x, y)
    spearman = spearmanr(x, y)
    # Pick whichever has the smaller p-value.
    if pearson.pvalue <= spearman.pvalue:
        return float(pearson.statistic), float(pearson.pvalue), len(sub)
    return float(spearman.statistic), float(spearman.pvalue), len(sub)


def _best_correlation(
    table: pd.DataFrame,
    sleep_metric: str,
    context_field: str,
    min_n: int,
) -> PatternResult | None:
    best: tuple[int, float, float, int] | None = None  # lag, r, p, n
    for lag in LAGS:
        out = _correlation_for_pair(table, sleep_metric, context_field, lag, min_n)
        if out is None:
            continue
        r, p, n = out
        if best is None or p < best[2]:
            best = (lag, r, p, n)

    if best is None:
        return None
    lag, r, p, n = best
    direction = "less" if r < 0 else "more"
    desc = (
        f"Higher {context_field} correlates with {direction} {sleep_metric} "
        f"(r={r:+.2f}, lag={lag}, n={n})."
    )
    return PatternResult(
        pattern_type="correlation",
        context_field=context_field,
        sleep_metric=sleep_metric,
        correlation=r,
        p_value=p,  # filled with corrected later
        p_value_raw=p,
        lag_days=lag,
        threshold=None,
        description=desc,
        n=n,
        confidence_label="emerging",  # filled after FDR
    )


def _quantile_bins(values: np.ndarray, num_bins: int) -> np.ndarray:
    """Return bin edges using quantiles; collapse to unique values."""
    qs = np.linspace(0, 1, num_bins + 1)
    edges = np.quantile(values, qs)
    edges = np.unique(edges)
    return edges


def _binned_for_pair(
    table: pd.DataFrame,
    sleep_metric: str,
    context_field: str,
    lag: int,
    min_n: int,
    min_bin_n: int,
    num_bins: int,
) -> tuple[float, float, int, str] | None:
    """One-way ANOVA across quantile bins. Returns (effect, p, n, threshold) or None.

    `effect` is (best_bin_mean - overall_mean) / overall_std — a signed,
    standardized "how much better is the optimal range" measure.
    """
    col = f"{context_field}__lag{lag}"
    if col not in table.columns or sleep_metric not in table.columns:
        return None

    sub = table[[sleep_metric, col, "dow"]].dropna()
    if len(sub) < min_n:
        return None

    y = sub[sleep_metric].to_numpy(dtype=float)
    x = sub[col].to_numpy(dtype=float)
    dow = sub["dow"].to_numpy()

    if len(sub) >= DOW_RESIDUALIZE_MIN_N:
        y = _dow_residualize(y, dow)

    edges = _quantile_bins(x, num_bins)
    if len(edges) < 3:  # need at least 2 bins
        return None

    # Use np.digitize: bin index 0 = below first edge, len(edges) = above last.
    # We want indices 1..len(edges)-1 inclusive — clip the edges into bins.
    bin_idx = np.clip(np.digitize(x, edges[1:-1], right=False), 0, len(edges) - 2)

    groups: list[np.ndarray] = []
    group_ranges: list[tuple[float, float]] = []
    for b in range(len(edges) - 1):
        mask = bin_idx == b
        if mask.sum() < min_bin_n:
            continue
        groups.append(y[mask])
        group_ranges.append((float(edges[b]), float(edges[b + 1])))

    if len(groups) < 2:
        return None

    # ANOVA across the surviving bins.
    f_stat, p_value = f_oneway(*groups)
    if not np.isfinite(p_value):
        return None

    means = np.array([g.mean() for g in groups])
    overall_mean = float(np.concatenate(groups).mean())
    overall_std = float(np.concatenate(groups).std(ddof=1))
    if overall_std == 0:
        return None

    # Best bin: max for higher-is-better metrics, min otherwise.
    if sleep_metric in HIGHER_IS_BETTER:
        best_idx = int(np.argmax(means))
    else:
        best_idx = int(np.argmin(means))

    best_mean = float(means[best_idx])
    effect = (best_mean - overall_mean) / overall_std
    lo, hi = group_ranges[best_idx]
    threshold = f"{lo:.1f}-{hi:.1f}"
    n_total = sum(len(g) for g in groups)
    return effect, float(p_value), n_total, threshold


def _best_binning(
    table: pd.DataFrame,
    sleep_metric: str,
    context_field: str,
    min_n: int,
    min_bin_n: int,
    num_bins: int,
) -> PatternResult | None:
    best: tuple[int, float, float, int, str] | None = None
    for lag in LAGS:
        out = _binned_for_pair(
            table, sleep_metric, context_field, lag,
            min_n=min_n, min_bin_n=min_bin_n, num_bins=num_bins,
        )
        if out is None:
            continue
        effect, p, n, threshold = out
        if best is None or p < best[2]:
            best = (lag, effect, p, n, threshold)

    if best is None:
        return None
    lag, effect, p, n, threshold = best
    direction = "highest" if sleep_metric in HIGHER_IS_BETTER else "lowest"
    desc = (
        f"{sleep_metric} is {direction} when {context_field} is in [{threshold}] "
        f"(effect={effect:+.2f}σ, lag={lag}, n={n})."
    )
    return PatternResult(
        pattern_type="binned",
        context_field=context_field,
        sleep_metric=sleep_metric,
        correlation=effect,
        p_value=p,
        p_value_raw=p,
        lag_days=lag,
        threshold=threshold,
        description=desc,
        n=n,
        confidence_label="emerging",
    )


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def discover_patterns(
    sleep_df: pd.DataFrame,
    context_df: pd.DataFrame,
    *,
    min_n: int = DEFAULT_MIN_N,
    min_bin_n: int = DEFAULT_MIN_BIN_N,
    num_bins: int = DEFAULT_NUM_BINS,
    p_threshold: float = DEFAULT_P_THRESHOLD,
    sleep_metrics: Iterable[str] = SLEEP_METRICS,
    context_fields: Iterable[str] = CONTEXT_FIELDS,
) -> list[PatternResult]:
    """Discover statistically significant patterns linking context → sleep.

    Args:
        sleep_df: One row per sleep night. Must have `date` + sleep metric cols.
        context_df: One row per day. Must have `date` + context cols.
        min_n: Minimum sample size per pair (default 10).
        min_bin_n: Minimum sample size per bin in binned analysis (default 5).
        num_bins: Target number of quantile bins for binned analysis (default 4).
        p_threshold: Post-FDR p-value cutoff (default 0.05).
        sleep_metrics, context_fields: Override the default lists for testing.

    Returns:
        Patterns with `p_value < p_threshold` post-FDR, ranked by
        `|effect| × -log10(p_corrected)` descending.
    """
    if len(sleep_df) == 0 or len(context_df) == 0:
        return []

    table = _build_lagged_table(sleep_df, context_df)

    candidates: list[PatternResult] = []
    for sleep_metric in sleep_metrics:
        for ctx in context_fields:
            corr = _best_correlation(table, sleep_metric, ctx, min_n=min_n)
            if corr is not None:
                candidates.append(corr)
            if ctx in BINNED_VARS:
                binned = _best_binning(
                    table, sleep_metric, ctx,
                    min_n=min_n, min_bin_n=min_bin_n, num_bins=num_bins,
                )
                if binned is not None:
                    candidates.append(binned)

    if not candidates:
        return []

    # Benjamini-Hochberg across all collected raw p-values.
    raw_ps = np.array([c.p_value_raw for c in candidates])
    corrected = false_discovery_control(raw_ps, method="bh")
    for c, p_corr in zip(candidates, corrected):
        c.p_value = float(p_corr)
        c.confidence_label = _confidence_label(c.n, c.p_value)

    significant = [c for c in candidates if c.p_value < p_threshold and c.n >= min_n]
    significant.sort(
        key=lambda c: abs(c.correlation) * -math.log10(max(c.p_value, 1e-12)),
        reverse=True,
    )
    return significant
