# backtest/calibration.py
# ─────────────────────────────────────────────────────────────
# Per-category calibration correction (S3-1).
# Computes how systematically biased Claude is per category
# and applies a correction to future probability estimates.
# ─────────────────────────────────────────────────────────────

import logging
import db
from signals.categorizer import detect_category

logger = logging.getLogger(__name__)

_bias_cache: dict[str, float] = {}
_cache_scan: int = 0
_MIN_SAMPLES = 10   # Minimum resolved predictions before applying correction


def compute_calibration_bias() -> dict[str, float]:
    """
    Compute mean(claude_yes_prob) - mean(resolved_yes) per category.
    Positive bias means Claude is systematically over-estimating YES.
    """
    try:
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute("""
            SELECT question, claude_yes_prob, resolved_yes
            FROM predictions
            WHERE resolved_yes IS NOT NULL
        """)
        rows = c.fetchall()
        conn.close()
    except Exception:
        return {}

    by_cat: dict[str, list] = {}
    for row in rows:
        cat = detect_category(row["question"] or "")
        by_cat.setdefault(cat, []).append(
            (float(row["claude_yes_prob"] or 0), int(row["resolved_yes"]))
        )

    bias = {}
    for cat, pairs in by_cat.items():
        if len(pairs) < _MIN_SAMPLES:
            continue
        avg_pred   = sum(p for p, _ in pairs) / len(pairs)
        avg_actual = sum(a for _, a in pairs) / len(pairs)
        bias[cat]  = round(avg_pred - avg_actual, 4)
        logger.info(f"Calibration [{cat}]: bias={bias[cat]:+.3f} (n={len(pairs)})")

    return bias


def get_correction(question: str, scan_count: int = 0) -> float:
    """
    Return the calibration correction to subtract from Claude's probability.
    Refreshes bias cache every 100 scans.
    """
    global _bias_cache, _cache_scan

    if not _bias_cache or (scan_count - _cache_scan) >= 100:
        _bias_cache = compute_calibration_bias()
        _cache_scan = scan_count

    cat = detect_category(question)
    correction = _bias_cache.get(cat, 0.0)
    if correction != 0.0:
        logger.debug(f"Calibration correction [{cat}]: {correction:+.3f}")
    return correction
