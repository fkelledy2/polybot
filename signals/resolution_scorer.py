# signals/resolution_scorer.py
# ─────────────────────────────────────────────────────────────
# FEAT-05: Score resolution criteria text for ambiguity risk.
# Returns float 0.0 (clear/objective) to 1.0 (maximally ambiguous).
# Regex-based — no API call, fails open (returns 0.0 on error).
# ─────────────────────────────────────────────────────────────

import re
import logging

logger = logging.getLogger(__name__)

_AMBIGUITY_SIGNALS = [
    (r"\bat\s+(polymarket'?s?)?\s*discretion\b",                  0.35),
    (r"\bpolymarket\s+(will|may)\s+decide\b",                     0.35),
    (r"\bin\s+(polymarket'?s?)?\s*(sole|absolute)\s*(judgment|discretion)\b", 0.40),
    (r"\bas\s+(determined|decided)\s+by\b",                       0.20),
    (r"\bincluding\s+but\s+not\s+limited\s+to\b",                 0.20),
    (r"\bif\s+(circumstances|conditions)\s+(change|warrant)\b",   0.20),
    (r"\bsignificant(ly)?\b",                                     0.15),
    (r"\bsubstantial(ly)?\b",                                     0.15),
    (r"\bwidely\s+(regarded|considered|seen)\b",                  0.15),
    (r"\bgenerally\s+(regarded|considered|accepted)\b",           0.12),
    (r"\bor\s+(similar|equivalent|comparable)\b",                 0.15),
    (r"\bappropriate\b",                                          0.10),
    (r"\breasonable\b",                                           0.10),
    (r"\bmay\b.{0,30}\bor\s+may\s+not\b",                        0.15),
    (r"\bmajor\b",                                                0.08),
]

_CLARITY_SIGNALS = [
    r"\baccording\s+to\b.{0,50}\b(official|government|federal|cdc|who|bls|ecb|fed)\b",
    r"\bas\s+(reported|published|announced)\s+by\b",
    r"\bofficial\b.{0,30}\b(data|source|record|announcement|statement)\b",
    r"\b(cme|fedwatch|deribit|cftc|sec|fda)\b",
    r"\bif\s+and\s+only\s+if\b",
    r"\bexactly\s+\d+\b",
    r"\bclosing\s+price\b",
    r"\bmarket\s+cap(italisation|italization)?\b.{0,30}\bcoingecko\b",
]

_NO_CRITERIA_PENALTY = 0.20


def score_ambiguity(resolution_criteria: str) -> float:
    """Returns 0.0 (objective) to 1.0 (maximally ambiguous)."""
    if not resolution_criteria or len(resolution_criteria.strip()) < 20:
        return _NO_CRITERIA_PENALTY

    text = resolution_criteria.lower()
    score = 0.0

    for pattern, weight in _AMBIGUITY_SIGNALS:
        if re.search(pattern, text):
            score += weight

    for pattern in _CLARITY_SIGNALS:
        if re.search(pattern, text):
            score = max(0.0, score - 0.15)

    return round(min(score, 1.0), 3)


def ambiguity_label(score: float) -> str:
    if score < 0.15:  return "clear"
    if score < 0.35:  return "moderate"
    if score < 0.55:  return "ambiguous"
    return "highly_ambiguous"
