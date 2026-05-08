# web/usage.py
# ─────────────────────────────────────────────────────────────
# Records actual API usage to the DB and computes real costs.
# Call record_usage() at every API call site.
# ─────────────────────────────────────────────────────────────

import logging
import threading
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)
_lock = threading.Lock()

# ── Pricing (USD per token / per call) ───────────────────────
# Anthropic Haiku 4.5
_HAIKU_IN       = 0.80  / 1_000_000   # $0.80 / MTok
_HAIKU_OUT      = 4.00  / 1_000_000   # $4.00 / MTok
_HAIKU_CACHE_W  = 1.00  / 1_000_000   # $1.00 / MTok (cache write)
_HAIKU_CACHE_R  = 0.08  / 1_000_000   # $0.08 / MTok (cache read, 10% of input)

# Anthropic Sonnet 4.6 (used for high-edge confirmation)
_SONNET_IN      = 3.00  / 1_000_000
_SONNET_OUT     = 15.00 / 1_000_000
_SONNET_CACHE_W = 3.75  / 1_000_000
_SONNET_CACHE_R = 0.30  / 1_000_000

# Brave Search — $5 / 2000 queries on Basic plan = $0.0025 per query
_BRAVE_PER_CALL = 0.0025

# Odds API — $10 / 500 requests on Starter = $0.02 per request
_ODDS_PER_CALL  = 0.02

_MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (_HAIKU_IN,   _HAIKU_OUT,   _HAIKU_CACHE_W,   _HAIKU_CACHE_R),
    "claude-sonnet-4-6":         (_SONNET_IN,  _SONNET_OUT,  _SONNET_CACHE_W,  _SONNET_CACHE_R),
    "claude-opus-4-7":           (15.00/1e6,   75.00/1e6,    18.75/1e6,        1.50/1e6),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    service      TEXT NOT NULL,
    model        TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read    INTEGER DEFAULT 0,
    cache_write   INTEGER DEFAULT 0,
    call_count    INTEGER DEFAULT 1,
    cost_usd      REAL DEFAULT 0.0
)
"""


def init_usage_table() -> None:
    try:
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute(db.adapt_schema(_SCHEMA))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Could not init api_usage table: {e}")


def _cost_for_anthropic(model: str, input_tokens: int, output_tokens: int,
                         cache_read: int, cache_write: int) -> float:
    pr = _MODEL_PRICING.get(model, (_HAIKU_IN, _HAIKU_OUT, _HAIKU_CACHE_W, _HAIKU_CACHE_R))
    return (
        input_tokens  * pr[0] +
        output_tokens * pr[1] +
        cache_write   * pr[2] +
        cache_read    * pr[3]
    )


def record_anthropic(model: str, input_tokens: int, output_tokens: int,
                     cache_read: int = 0, cache_write: int = 0) -> None:
    cost = _cost_for_anthropic(model, input_tokens, output_tokens, cache_read, cache_write)
    _insert("anthropic", model=model, input_tokens=input_tokens,
            output_tokens=output_tokens, cache_read=cache_read,
            cache_write=cache_write, cost_usd=cost)


def record_brave_search(call_count: int = 1) -> None:
    _insert("brave_search", call_count=call_count, cost_usd=_BRAVE_PER_CALL * call_count)


def record_odds_api(call_count: int = 1) -> None:
    _insert("odds_api", call_count=call_count, cost_usd=_ODDS_PER_CALL * call_count)


def _insert(service: str, model: str = None, input_tokens: int = 0,
            output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0,
            call_count: int = 1, cost_usd: float = 0.0) -> None:
    try:
        with _lock:
            conn = db.get_connection()
            c = db.get_cursor(conn)
            p = db.placeholder
            c.execute(
                f"""INSERT INTO api_usage
                    (service, model, input_tokens, output_tokens,
                     cache_read, cache_write, call_count, cost_usd)
                    VALUES ({p},{p},{p},{p},{p},{p},{p},{p})""",
                (service, model, input_tokens, output_tokens,
                 cache_read, cache_write, call_count, round(cost_usd, 6)),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"Could not record usage for {service}: {e}")


# ── Query helpers ─────────────────────────────────────────────

def get_costs_since(days: int) -> dict:
    """
    Returns per-service cost and call totals for the last N days.
    Also returns Heroku prorated for the same window.
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        conn = db.get_connection()
        c = db.get_cursor(conn)
        p = db.placeholder
        c.execute(f"""
            SELECT service,
                   SUM(cost_usd)      AS cost,
                   SUM(call_count)    AS calls,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cache_read)    AS cache_read,
                   COUNT(*)           AS records
            FROM api_usage
            WHERE recorded_at >= {p}
            GROUP BY service
        """, (since,))
        rows = {r["service"]: dict(r) for r in c.fetchall()}
        conn.close()
    except Exception as e:
        logger.warning(f"Could not query api_usage: {e}")
        rows = {}

    # Heroku: $7/month prorated
    heroku_cost = round(7.0 * days / 30.0, 4)

    result = {}
    for svc, row in rows.items():
        result[svc] = {
            "cost":          round(row["cost"] or 0, 4),
            "calls":         int(row["calls"] or 0),
            "input_tokens":  int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "cache_read":    int(row["cache_read"] or 0),
        }
    result["heroku"] = {"cost": heroku_cost, "calls": 0}

    total = sum(v["cost"] for v in result.values())
    return {"services": result, "total": round(total, 4), "days": days}
