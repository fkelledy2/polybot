#!/usr/bin/env python
# backtest/optimizer.py
# ─────────────────────────────────────────────────────────────
# Iterative parameter optimizer.
#
# Strategy: call Claude ONCE, cache estimates, then sweep the full
# parameter space in pure Python (free). Repeat until convergence.
#
# Usage:
#   python backtest/optimizer.py                   # dry-run, 100 markets
#   python backtest/optimizer.py --markets 150     # more data
#   python backtest/optimizer.py --apply           # write best config
#   python backtest/optimizer.py --force-refresh   # re-fetch + re-ask Claude
#
# Cost: ~$0.01-0.03 per run (Haiku, 100 markets). Cached = $0.00.
# ─────────────────────────────────────────────────────────────

import argparse
import ast
import copy
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.fetcher import fetch_resolved_markets, ResolvedMarket
from signals.categorizer import detect_category

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("optimizer")

# ── Cost table (USD per million tokens) ──────────────────────
COST_PER_MTOK = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":           {"input": 15.0, "output": 75.00},
}
COST_DEFAULT = {"input": 3.00, "output": 15.00}

# ── Optimization constants ────────────────────────────────────
MIN_TRADES_TO_REPORT = 5        # Segment needs ≥5 trades to be analysed
MIN_TRADES_TOTAL = 8            # Config needs ≥8 trades to be considered valid
CONVERGENCE_DELTA = 0.005       # Stop if improvement < 0.5¢/trade
PLATEAU_WINDOW = 3              # Stop after N consecutive non-improving iterations
DEFAULT_CACHE = "optimizer_cache.json"

# ── Parameter bounds ─────────────────────────────────────────
PARAM_BOUNDS = {
    "min_edge":           (0.05, 0.30),
    "min_edge_extreme":   (0.05, 0.40),
    "extreme_threshold":  (0.02, 0.15),
    "min_entry_prob":     (0.01, 0.15),
    "max_days":           (3,    90),
    "min_days":           (1,    14),
}

# ── Perturbation steps per parameter ─────────────────────────
PARAM_STEPS = {
    "min_edge":           [-0.04, -0.02, -0.01, 0.0, +0.01, +0.02, +0.04],
    "min_edge_extreme":   [-0.04, -0.02, -0.01, 0.0, +0.01, +0.02, +0.04],
    "extreme_threshold":  [-0.02, -0.01, 0.0, +0.01, +0.02],
    "min_entry_prob":     [-0.01, 0.0, +0.01],
    "max_days":           [-14, -7, 0, +7, +14],
    "min_days":           [-1, 0, +1],
}

# ── Segment → param relevance map ────────────────────────────
# Which params are most relevant when a given segment type is the worst
SEGMENT_PARAM_FOCUS = {
    "edge":     ["min_edge", "min_edge_extreme"],
    "extreme":  ["min_edge_extreme", "extreme_threshold"],
    "days":     ["max_days", "min_days"],
    "category": [],           # handled separately via disabled_categories
    "overall":  list(PARAM_STEPS.keys()),  # fallback: try everything
}


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────

@dataclass
class CachedEstimate:
    """One market + Claude's single-pass estimate."""
    market_id:          str
    question:           str
    resolved_yes:       bool
    last_price:         Optional[float]
    volume_usd:         float
    end_date:           Optional[str]
    category:           str
    days_to_resolve:    Optional[int]
    claude_probability: float
    confidence:         str
    reasoning:          str
    input_tokens:       int = 0
    output_tokens:      int = 0
    real_entry_price:   Optional[float] = None  # set when sourced from Dune


@dataclass
class OptimizerConfig:
    """Snapshot of tunable parameters."""
    min_edge:             float       = 0.10
    min_edge_extreme:     float       = 0.15
    extreme_threshold:    float       = 0.05
    min_entry_prob:       float       = 0.03
    max_days:             int         = 30
    min_days:             int         = 1
    disabled_categories:  list[str]   = field(default_factory=list)

    @classmethod
    def from_config_module(cls) -> "OptimizerConfig":
        """Load current values from config.py."""
        from config import (
            MIN_EDGE_TO_TRADE, MIN_EDGE_TO_TRADE_EXTREME,
            EXTREME_PRICE_THRESHOLD, MIN_ENTRY_PROBABILITY,
            MAX_DAYS_TO_RESOLVE, MIN_DAYS_TO_RESOLVE,
        )
        try:
            from config import DISABLED_CATEGORIES
        except ImportError:
            DISABLED_CATEGORIES = []

        return cls(
            min_edge=MIN_EDGE_TO_TRADE,
            min_edge_extreme=MIN_EDGE_TO_TRADE_EXTREME,
            extreme_threshold=EXTREME_PRICE_THRESHOLD,
            min_entry_prob=MIN_ENTRY_PROBABILITY,
            max_days=MAX_DAYS_TO_RESOLVE,
            min_days=MIN_DAYS_TO_RESOLVE,
            disabled_categories=list(DISABLED_CATEGORIES),
        )

    def as_dict(self) -> dict:
        return {
            "MIN_EDGE_TO_TRADE":          self.min_edge,
            "MIN_EDGE_TO_TRADE_EXTREME":  self.min_edge_extreme,
            "EXTREME_PRICE_THRESHOLD":    self.extreme_threshold,
            "MIN_ENTRY_PROBABILITY":      self.min_entry_prob,
            "MAX_DAYS_TO_RESOLVE":        self.max_days,
            "MIN_DAYS_TO_RESOLVE":        self.min_days,
            "DISABLED_CATEGORIES":        self.disabled_categories,
        }

    def summary(self) -> str:
        cats = f" disabled={self.disabled_categories}" if self.disabled_categories else ""
        return (
            f"edge={self.min_edge:.0%}  "
            f"extreme_edge={self.min_edge_extreme:.0%}@{self.extreme_threshold:.0%}  "
            f"days={self.min_days}-{self.max_days}"
            f"{cats}"
        )


@dataclass
class SegmentStats:
    trades:  int   = 0
    wins:    int   = 0
    pnl_sum: float = 0.0

    @property
    def avg_pnl(self) -> float:
        return self.pnl_sum / self.trades if self.trades else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


@dataclass
class SimStats:
    """Aggregated result of one simulation run."""
    config:       OptimizerConfig
    total_markets: int
    trades:       int
    wins:         int
    total_pnl:    float
    # Segment breakdowns
    by_edge:      dict[str, SegmentStats]     = field(default_factory=dict)
    by_category:  dict[str, SegmentStats]     = field(default_factory=dict)
    by_extreme:   dict[str, SegmentStats]     = field(default_factory=dict)
    by_days:      dict[str, SegmentStats]     = field(default_factory=dict)

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.trades if self.trades else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def ev_per_market(self) -> float:
        return self.total_pnl / self.total_markets if self.total_markets else 0.0


@dataclass
class IterationResult:
    iteration:       int
    config:          OptimizerConfig
    stats:           SimStats
    delta_avg_pnl:   float
    params_changed:  list[str]
    worst_segment:   str
    reason:          str


# ─────────────────────────────────────────────────────────────
# Cost tracking
# ─────────────────────────────────────────────────────────────

def _cost_table(model: str) -> dict:
    for key in COST_PER_MTOK:
        if key in model:
            return COST_PER_MTOK[key]
    return COST_DEFAULT


def estimate_claude_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    t = _cost_table(model)
    return (input_tokens * t["input"] + output_tokens * t["output"]) / 1_000_000


def estimate_preflight_cost(n_markets: int, model: str) -> float:
    """Rough upper-bound estimate before fetching anything."""
    batches = (n_markets + 19) // 20
    est_input  = batches * 900   # ~900 tokens/batch input
    est_output = batches * 600   # ~600 tokens/batch output
    return estimate_claude_cost(est_input, est_output, model)


# ─────────────────────────────────────────────────────────────
# Cache I/O
# ─────────────────────────────────────────────────────────────

def _parse_days_to_resolve(end_date: Optional[str]) -> Optional[int]:
    """
    Returns days until resolution, or None if already resolved / unknown.
    Returns None for past dates so the days-to-resolve filter is skipped
    for historical markets in backtesting.
    """
    if not end_date:
        return None
    try:
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(end_date[:26], fmt[:len(fmt)])
                break
            except ValueError:
                continue
        else:
            return None
        now = datetime.now()
        delta = dt - now
        if delta.days < 0:
            return None  # Already resolved — skip filter in backtest
        return delta.days
    except Exception:
        return None


def save_cache(estimates: list[CachedEstimate], path: str) -> None:
    data = {
        "saved_at":   datetime.now().isoformat(),
        "n_markets":  len(estimates),
        "estimates":  [asdict(e) for e in estimates],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Cache saved: {path} ({len(estimates)} markets)")


def load_cache(path: str) -> Optional[list[CachedEstimate]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        estimates = [CachedEstimate(**e) for e in data["estimates"]]
        age_hours = (
            datetime.now() - datetime.fromisoformat(data["saved_at"])
        ).total_seconds() / 3600
        logger.info(
            f"Loaded cache: {len(estimates)} markets, "
            f"{age_hours:.1f}h old ({path})"
        )
        return estimates
    except Exception as e:
        logger.warning(f"Cache load failed: {e}")
        return None


def fetch_and_estimate(
    n_markets: int,
    cost_limit: float,
    model: str,
    dune_key: str = "",
    lookback_days: int = 180,
) -> tuple[list[CachedEstimate], float]:
    """Fetch markets + call Claude once. Returns (estimates, actual_cost).

    If dune_key is provided, fetches real historical entry prices from Dune
    Analytics (polymarket_polygon schema) instead of Polymarket's gamma API.
    Dune markets use actual avg YES price 3-14 days before resolution, giving
    a single realistic entry price per market instead of 7 synthetic ones.
    """
    import anthropic
    from config import ANTHROPIC_API_KEY

    preflight = estimate_preflight_cost(n_markets, model)
    if preflight > cost_limit:
        raise RuntimeError(
            f"Estimated cost ${preflight:.2f} exceeds limit ${cost_limit:.2f}. "
            f"Reduce --markets or raise --cost-limit."
        )

    # ── Market sourcing: Dune (real prices) vs gamma (synthetic) ──
    if dune_key:
        from backtest.dune_fetcher import DuneFetcher
        dune = DuneFetcher(dune_key)
        markets = dune.fetch_resolved_markets(
            lookback_days=lookback_days, limit=n_markets
        )
        use_real_prices = True
        if not markets:
            raise RuntimeError("Dune returned 0 markets. Check API key or lookback window.")
    else:
        logger.info(f"Fetching {n_markets} resolved markets from Polymarket gamma API…")
        raw_markets = fetch_resolved_markets(limit=n_markets * 4)
        if not raw_markets:
            raise RuntimeError("No resolved markets returned from Polymarket API.")
        markets = raw_markets[:n_markets]
        use_real_prices = False

    logger.info(
        f"Fetched {len(markets)} markets "
        f"({'Dune — real prices' if use_real_prices else 'gamma — synthetic prices'})."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    estimates: list[CachedEstimate] = []
    total_input  = 0
    total_output = 0
    batch_size   = 20

    for batch_start in range(0, len(markets), batch_size):
        batch = markets[batch_start: batch_start + batch_size]
        batch_n = batch_start // batch_size + 1
        total_batches = (len(markets) + batch_size - 1) // batch_size
        logger.info(f"Claude batch {batch_n}/{total_batches} ({len(batch)} markets)…")

        lines = [
            f"{i+1}. [{m.market_id[:8]}] {m.question}"
            for i, m in enumerate(batch)
        ]
        prompt = (
            "You are an expert prediction market analyst performing a calibration exercise.\n\n"
            "Analyse each market question below and estimate the TRUE probability of YES.\n"
            "Do NOT try to guess the actual outcome — reason from first principles about "
            "what a well-calibrated probability should be.\n\n"
            "MARKETS:\n" + "\n".join(lines) + "\n\n"
            "Respond ONLY with a JSON array, same order, no markdown:\n"
            '[{"market_id":"<first 8 chars>","yes_probability":0.XX,'
            '"confidence":"low"|"medium"|"high","reasoning":"one sentence"}]'
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            clean = raw.replace("```json", "").replace("```", "").strip()
            results = json.loads(clean)
            if not isinstance(results, list):
                results = []

            in_tok  = response.usage.input_tokens
            out_tok = response.usage.output_tokens
            total_input  += in_tok
            total_output += out_tok

            running_cost = estimate_claude_cost(total_input, total_output, model)
            if running_cost > cost_limit:
                logger.error(
                    f"Cost limit ${cost_limit:.2f} exceeded at ${running_cost:.2f}. "
                    f"Stopping early with {len(estimates)} estimates."
                )
                break

            per_batch_tokens = in_tok // max(len(batch), 1)

            for i, market in enumerate(batch):
                r = results[i] if i < len(results) else None
                if not r:
                    continue
                try:
                    prob = float(r["yes_probability"])
                    conf = r.get("confidence", "medium")
                    rsn  = r.get("reasoning", "")
                except (KeyError, ValueError, TypeError):
                    continue

                estimates.append(CachedEstimate(
                    market_id=market.market_id,
                    question=market.question,
                    resolved_yes=market.resolved_yes,
                    last_price=market.last_price,
                    volume_usd=market.volume_usd,
                    end_date=market.end_date,
                    category=detect_category(market.question),
                    days_to_resolve=_parse_days_to_resolve(market.end_date),
                    claude_probability=round(prob, 4),
                    confidence=conf,
                    reasoning=rsn,
                    input_tokens=per_batch_tokens,
                    output_tokens=out_tok // max(len(batch), 1),
                    real_entry_price=market.last_price if use_real_prices else None,
                ))

        except json.JSONDecodeError as e:
            logger.warning(f"Batch {batch_n}: JSON parse error — {e}")
        except Exception as e:
            logger.error(f"Batch {batch_n}: API error — {e}")

    actual_cost = estimate_claude_cost(total_input, total_output, model)
    logger.info(
        f"Claude done: {len(estimates)} estimates, "
        f"{total_input:,} input + {total_output:,} output tokens, "
        f"cost=${actual_cost:.4f}"
    )
    return estimates, actual_cost


def load_or_fetch(
    n_markets: int,
    cost_limit: float,
    model: str,
    force_refresh: bool,
    cache_path: str,
    dune_key: str = "",
    lookback_days: int = 180,
) -> tuple[list[CachedEstimate], float]:
    if not force_refresh:
        cached = load_cache(cache_path)
        if cached:
            return cached, 0.0

    estimates, cost = fetch_and_estimate(
        n_markets, cost_limit, model,
        dune_key=dune_key, lookback_days=lookback_days,
    )
    if estimates:
        save_cache(estimates, cache_path)
    return estimates, cost


# ─────────────────────────────────────────────────────────────
# Pure-Python simulator (no Claude, no I/O)
# ─────────────────────────────────────────────────────────────

# Synthetic entry prices to test. We can't recover the actual pre-resolution
# market price for closed markets. Testing a spread of prices is more realistic
# than a fixed 0.50, and each market contributes at most one trade per price
# point. We avoid extremes (0.10/0.90) that rarely appear in liquid markets.
SYNTHETIC_PRICES = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]

# Confidence filter is intentionally RELAXED for backtesting.
# The backtest prompt gives Claude only a question (no price context, no
# resolution criteria, no news). That sparse context causes Claude to assign
# "low" confidence to ~70% of markets even when its directional estimate is
# correct. In live trading the full-context prompt produces far more
# medium/high signals. Filtering by confidence here would discard most of the
# dataset and make the optimizer blind.
BACKTEST_SKIP_LOW_CONFIDENCE = False


def _edge_bucket(abs_edge: float) -> str:
    if abs_edge < 0.15:  return "05-15%"
    if abs_edge < 0.25:  return "15-25%"
    if abs_edge < 0.35:  return "25-35%"
    return "35%+"


def _days_bucket(days: Optional[int]) -> str:
    if days is None:    return "unknown"
    if days <= 7:       return "1-7d"
    if days <= 14:      return "7-14d"
    if days <= 30:      return "14-30d"
    return "30d+"


def _seg_add(d: dict, key: str, won: bool, pnl: float) -> None:
    if key not in d:
        d[key] = SegmentStats()
    d[key].trades += 1
    d[key].wins   += int(won)
    d[key].pnl_sum += pnl


def simulate_config(estimates: list[CachedEstimate], cfg: OptimizerConfig) -> SimStats:
    """
    Pure Python. No I/O. Fast inner loop.

    Tests each market at multiple synthetic entry prices (see SYNTHETIC_PRICES).
    Uses last_price as a proxy to detect extreme-priced markets (those priced
    very close to 0 or 1 before resolution tend to have last_price near 0/1).
    """
    stats = SimStats(
        config=cfg,
        total_markets=len(estimates),
        trades=0, wins=0, total_pnl=0.0,
    )

    for est in estimates:
        # Days-to-resolve filter
        dtr = est.days_to_resolve
        if dtr is not None:
            if dtr > cfg.max_days or dtr < cfg.min_days:
                continue

        # Category filter
        if est.category in cfg.disabled_categories:
            continue

        # Confidence filter (relaxed for backtest — see BACKTEST_SKIP_LOW_CONFIDENCE)
        if BACKTEST_SKIP_LOW_CONFIDENCE and est.confidence == "low":
            continue

        # Extreme price detection via last_price proxy
        lp = est.last_price
        is_extreme = (
            lp is not None
            and (lp < cfg.extreme_threshold or lp > (1.0 - cfg.extreme_threshold))
        )

        effective_min = cfg.min_edge_extreme if is_extreme else cfg.min_edge

        # Use real entry price from Dune if available; otherwise test synthetics.
        # Real prices: one trade per market (accurate).
        # Synthetic prices: seven trades per market (approximate, gamma-sourced).
        prices_to_test = (
            [est.real_entry_price]
            if est.real_entry_price is not None
            else SYNTHETIC_PRICES
        )

        for entry_price in prices_to_test:
            edge      = est.claude_probability - entry_price
            direction = "YES" if edge >= 0 else "NO"
            abs_edge  = abs(edge)

            if abs_edge < effective_min:
                continue

            # Outcome
            correct = (direction == "YES") == est.resolved_yes
            if direction == "YES":
                pnl = (1.0 - entry_price) if est.resolved_yes else -entry_price
            else:
                no_price = 1.0 - entry_price
                pnl = (1.0 - no_price) if not est.resolved_yes else -no_price

            stats.trades    += 1
            stats.wins      += int(correct)
            stats.total_pnl += pnl

            # Segment tracking
            _seg_add(stats.by_edge,     _edge_bucket(abs_edge),                    correct, pnl)
            _seg_add(stats.by_category, est.category,                              correct, pnl)
            _seg_add(stats.by_extreme,  "extreme" if is_extreme else "normal",     correct, pnl)
            _seg_add(stats.by_days,     _days_bucket(dtr),                         correct, pnl)

    return stats


# ─────────────────────────────────────────────────────────────
# Segment analysis
# ─────────────────────────────────────────────────────────────

def find_worst_segment(stats: SimStats) -> tuple[str, str, float]:
    """
    Returns (segment_type, segment_label, avg_pnl_of_worst).
    Only considers segments with >= MIN_TRADES_TO_REPORT trades.
    """
    worst_type  = "overall"
    worst_label = "all"
    worst_pnl   = stats.avg_pnl

    for seg_type, seg_dict in [
        ("edge",     stats.by_edge),
        ("category", stats.by_category),
        ("extreme",  stats.by_extreme),
        ("days",     stats.by_days),
    ]:
        for label, seg in seg_dict.items():
            if seg.trades < MIN_TRADES_TO_REPORT:
                continue
            if seg.avg_pnl < worst_pnl:
                worst_pnl   = seg.avg_pnl
                worst_type  = seg_type
                worst_label = label

    return worst_type, worst_label, worst_pnl


def _build_reason(
    seg_type: str, seg_label: str, seg_pnl: float,
    stats: SimStats, delta: float,
) -> str:
    if seg_type == "overall":
        return f"Global avg PnL {stats.avg_pnl:+.3f}; broad parameter search."
    seg_dict = {
        "edge": stats.by_edge, "category": stats.by_category,
        "extreme": stats.by_extreme, "days": stats.by_days,
    }.get(seg_type, {})
    seg = seg_dict.get(seg_label)
    n = seg.trades if seg else 0
    return (
        f"{seg_type.title()} '{seg_label}' had avg_pnl={seg_pnl:+.3f} "
        f"on {n} trades (worst segment). "
        f"Adjustment gained {delta:+.3f} avg PnL."
    )


# ─────────────────────────────────────────────────────────────
# Neighbourhood search
# ─────────────────────────────────────────────────────────────

def _clamp(cfg: OptimizerConfig) -> OptimizerConfig:
    """Enforce bounds and logical constraints."""
    cfg = copy.copy(cfg)
    lo, hi = PARAM_BOUNDS["min_edge"]
    cfg.min_edge = round(max(lo, min(hi, cfg.min_edge)), 4)

    lo, hi = PARAM_BOUNDS["min_edge_extreme"]
    cfg.min_edge_extreme = round(max(lo, min(hi, cfg.min_edge_extreme)), 4)
    cfg.min_edge_extreme = max(cfg.min_edge_extreme, cfg.min_edge)

    lo, hi = PARAM_BOUNDS["extreme_threshold"]
    cfg.extreme_threshold = round(max(lo, min(hi, cfg.extreme_threshold)), 4)

    lo, hi = PARAM_BOUNDS["min_entry_prob"]
    cfg.min_entry_prob = round(max(lo, min(hi, cfg.min_entry_prob)), 4)

    lo, hi = PARAM_BOUNDS["max_days"]
    cfg.max_days = max(lo, min(int(hi), int(cfg.max_days)))

    lo, hi = PARAM_BOUNDS["min_days"]
    cfg.min_days = max(lo, min(int(hi), int(cfg.min_days)))
    cfg.min_days = min(cfg.min_days, cfg.max_days - 1)

    return cfg


def _diff_configs(a: OptimizerConfig, b: OptimizerConfig) -> list[str]:
    changed = []
    for attr in ["min_edge", "min_edge_extreme", "extreme_threshold",
                 "min_entry_prob", "max_days", "min_days", "disabled_categories"]:
        if getattr(a, attr) != getattr(b, attr):
            changed.append(attr)
    return changed


def generate_candidates(
    current: OptimizerConfig,
    worst_seg_type: str,
    estimates: list[CachedEstimate],
    iteration: int,
) -> list[OptimizerConfig]:
    """
    Generate neighbor configs to evaluate.
    In early iterations: perturb all params.
    In later iterations: focus on params most relevant to the worst segment.
    """
    focus_params = (
        list(PARAM_STEPS.keys())
        if iteration <= 4
        else SEGMENT_PARAM_FOCUS.get(worst_seg_type, list(PARAM_STEPS.keys()))
    )

    candidates: list[OptimizerConfig] = []

    # Perturb numerical params
    for param in focus_params:
        if param not in PARAM_STEPS:
            continue
        for delta in PARAM_STEPS[param]:
            if delta == 0.0:
                continue
            cfg = copy.copy(current)
            cfg.disabled_categories = list(current.disabled_categories)
            old_val = getattr(cfg, param)
            setattr(cfg, param, old_val + delta)
            cfg = _clamp(cfg)
            candidates.append(cfg)

    # Category disabling: try adding each losing category
    if worst_seg_type in ("category", "overall"):
        known_cats = ["CRYPTO", "SPORTS", "POLITICS", "MACRO", "TECH", "ENTERTAINMENT", "GEO"]
        for cat in known_cats:
            if cat not in current.disabled_categories:
                cfg = copy.copy(current)
                cfg.disabled_categories = list(current.disabled_categories) + [cat]
                candidates.append(cfg)

    # Also try re-enabling categories (if disabled but actually winning)
    for cat in list(current.disabled_categories):
        cfg = copy.copy(current)
        cfg.disabled_categories = [c for c in current.disabled_categories if c != cat]
        candidates.append(cfg)

    # Deduplicate
    seen: set[str] = set()
    unique: list[OptimizerConfig] = []
    for c in candidates:
        key = json.dumps(c.as_dict(), sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


# ─────────────────────────────────────────────────────────────
# Optimization loop
# ─────────────────────────────────────────────────────────────

def run_optimizer(
    estimates: list[CachedEstimate],
    baseline: OptimizerConfig,
    max_iterations: int = 15,
    delta_threshold: float = CONVERGENCE_DELTA,
) -> list[IterationResult]:

    history: list[IterationResult] = []
    best_cfg   = copy.deepcopy(baseline)
    best_stats = simulate_config(estimates, best_cfg)

    # Iteration 0: baseline
    history.append(IterationResult(
        iteration=0,
        config=best_cfg,
        stats=best_stats,
        delta_avg_pnl=0.0,
        params_changed=[],
        worst_segment="—",
        reason="Baseline (current config.py values).",
    ))

    no_improve_streak = 0

    for i in range(1, max_iterations + 1):
        seg_type, seg_label, seg_pnl = find_worst_segment(best_stats)

        candidates = generate_candidates(best_cfg, seg_type, estimates, i)
        if not candidates:
            logger.info("No candidates generated; stopping.")
            break

        # Evaluate all candidates — pure Python, very fast
        best_candidate_stats = best_stats
        best_candidate_cfg   = best_cfg

        for cfg in candidates:
            s = simulate_config(estimates, cfg)
            # Prefer higher avg_pnl; use trades as tiebreaker (prefer more trades)
            if (s.trades >= MIN_TRADES_TOTAL and
                    (s.avg_pnl > best_candidate_stats.avg_pnl or
                     (s.avg_pnl == best_candidate_stats.avg_pnl
                      and s.trades > best_candidate_stats.trades))):
                best_candidate_stats = s
                best_candidate_cfg   = cfg

        delta = best_candidate_stats.avg_pnl - best_stats.avg_pnl
        params_changed = _diff_configs(best_cfg, best_candidate_cfg)

        history.append(IterationResult(
            iteration=i,
            config=best_candidate_cfg,
            stats=best_candidate_stats,
            delta_avg_pnl=delta,
            params_changed=params_changed,
            worst_segment=f"{seg_type}:{seg_label}",
            reason=_build_reason(seg_type, seg_label, seg_pnl, best_stats, delta),
        ))

        if delta > 0:
            best_cfg   = best_candidate_cfg
            best_stats = best_candidate_stats
            no_improve_streak = 0
        else:
            no_improve_streak += 1

        if delta < delta_threshold:
            logger.info(f"Converged at iteration {i}: delta={delta:+.4f} < {delta_threshold}")
            break

        if no_improve_streak >= PLATEAU_WINDOW:
            logger.info(f"Plateau detected ({PLATEAU_WINDOW} non-improving iterations). Stopping.")
            break

    return history


# ─────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────

def _seg_table(seg_dict: dict[str, SegmentStats], label: str) -> str:
    if not seg_dict:
        return ""
    parts = []
    for k, v in sorted(seg_dict.items()):
        if v.trades >= MIN_TRADES_TO_REPORT:
            parts.append(f"{k}: {v.trades}t/{v.avg_pnl:+.2f}")
    return f"  {label:10s}  {('  '.join(parts)) or '—'}"


def print_iteration(result: IterationResult) -> None:
    s = result.stats
    changed_str = ", ".join(result.params_changed) if result.params_changed else "none"
    delta_arrow = "▲" if result.delta_avg_pnl > 0 else ("▼" if result.delta_avg_pnl < 0 else "—")

    print(f"\n{'═'*72}")
    if result.iteration == 0:
        print(f"  BASELINE")
    else:
        print(f"  ITERATION {result.iteration}")
    print(f"{'═'*72}")
    print(f"  Config:   {result.config.summary()}")
    print(
        f"  Trades:   {s.trades}  |  "
        f"Win: {s.win_rate:.1%}  |  "
        f"Avg PnL: {s.avg_pnl:+.4f}  |  "
        f"Total: {s.total_pnl:+.2f}"
    )
    if result.iteration > 0:
        print(f"  Changed:  {changed_str}")
        print(f"  Delta:    {result.delta_avg_pnl:+.4f} avg PnL/trade {delta_arrow}")
        print(f"  Reason:   {result.reason}")

    # Segment breakdown
    print()
    for line in [
        _seg_table(s.by_edge,     "By edge:"),
        _seg_table(s.by_category, "By cat:"),
        _seg_table(s.by_extreme,  "By type:"),
        _seg_table(s.by_days,     "By days:"),
    ]:
        if line:
            print(line)


def print_final_report(
    history: list[IterationResult],
    actual_cost: float,
    elapsed: float,
) -> None:
    baseline = history[0].stats
    best     = max(history, key=lambda r: r.stats.avg_pnl)
    best_s   = best.stats

    print(f"\n{'═'*72}")
    print(f"  OPTIMIZATION COMPLETE  ({len(history)-1} iterations)")
    print(f"{'═'*72}")
    print(f"  Baseline:  {baseline.trades}t  "
          f"win={baseline.win_rate:.1%}  avg_pnl={baseline.avg_pnl:+.4f}")
    print(f"  Optimal:   {best_s.trades}t  "
          f"win={best_s.win_rate:.1%}  avg_pnl={best_s.avg_pnl:+.4f}")

    improvement = best_s.avg_pnl - baseline.avg_pnl
    pct = (improvement / abs(baseline.avg_pnl) * 100) if baseline.avg_pnl != 0 else float("inf")
    print(f"  Gain:      {improvement:+.4f} avg PnL/trade ({pct:+.1f}%)")
    print()

    # Diff table
    baseline_cfg = history[0].config
    opt_cfg      = best.config
    print("  OPTIMAL PARAMETERS vs CURRENT:")
    print(f"  {'Parameter':<32}  {'Current':>10}  {'Optimal':>10}")
    print("  " + "─"*56)

    for attr, key in [
        ("min_edge",          "MIN_EDGE_TO_TRADE"),
        ("min_edge_extreme",  "MIN_EDGE_TO_TRADE_EXTREME"),
        ("extreme_threshold", "EXTREME_PRICE_THRESHOLD"),
        ("min_entry_prob",    "MIN_ENTRY_PROBABILITY"),
        ("max_days",          "MAX_DAYS_TO_RESOLVE"),
        ("min_days",          "MIN_DAYS_TO_RESOLVE"),
        ("disabled_categories", "DISABLED_CATEGORIES"),
    ]:
        cur_val = getattr(baseline_cfg, attr)
        opt_val = getattr(opt_cfg, attr)
        changed = " ←" if cur_val != opt_val else ""
        if isinstance(cur_val, float):
            cur_str = f"{cur_val:.2f}"
            opt_str = f"{opt_val:.2f}"
        else:
            cur_str = str(cur_val)
            opt_str = str(opt_val)
        print(f"  {key:<32}  {cur_str:>10}  {opt_str:>10}{changed}")

    print()
    print(f"  Claude cost:  ${actual_cost:.4f}")
    print(f"  Elapsed:      {elapsed:.1f}s  (simulation: ~{elapsed*0.05:.1f}s)")
    print(f"  Cache:        re-use with --no-refresh (cost $0.00)")


# ─────────────────────────────────────────────────────────────
# Config write-back
# ─────────────────────────────────────────────────────────────

_CONFIG_PATTERNS = [
    ("min_edge",          r"(MIN_EDGE_TO_TRADE\s*=\s*)[\d.]+",       lambda v: f"{v:.2f}"),
    ("min_edge_extreme",  r"(MIN_EDGE_TO_TRADE_EXTREME\s*=\s*)[\d.]+", lambda v: f"{v:.2f}"),
    ("extreme_threshold", r"(EXTREME_PRICE_THRESHOLD\s*=\s*)[\d.]+", lambda v: f"{v:.2f}"),
    ("min_entry_prob",    r"(MIN_ENTRY_PROBABILITY\s*=\s*)[\d.]+",   lambda v: f"{v:.2f}"),
    ("max_days",          r"(MAX_DAYS_TO_RESOLVE\s*=\s*)\d+",        lambda v: str(int(v))),
    ("min_days",          r"(MIN_DAYS_TO_RESOLVE\s*=\s*)\d+",        lambda v: str(int(v))),
    ("disabled_categories", r"(DISABLED_CATEGORIES\s*=\s*)\[.*?\]",  lambda v: repr(v)),
]


def apply_optimal_config(
    config_path: str,
    baseline_cfg: OptimizerConfig,
    optimal_cfg: OptimizerConfig,
    dry_run: bool = True,
) -> bool:
    """
    Write optimal parameters to config.py using safe regex substitution.
    Returns True if successful (or if dry_run with valid changes).
    """
    content = Path(config_path).read_text()
    modified = content

    changes: list[tuple[str, str, str]] = []

    for attr, pattern, formatter in _CONFIG_PATTERNS:
        cur_val = getattr(baseline_cfg, attr)
        opt_val = getattr(optimal_cfg, attr)
        if cur_val == opt_val:
            continue

        new_val_str = formatter(opt_val)
        new_content = re.sub(
            pattern,
            lambda m, s=new_val_str: m.group(1) + s,
            modified,
        )
        if new_content == modified:
            logger.warning(f"Pattern did not match for {attr} — skipping.")
            continue

        # Safety: verify pattern appears exactly once
        matches = re.findall(pattern, modified)
        if len(matches) != 1:
            logger.warning(f"Pattern for {attr} matched {len(matches)} times — skipping.")
            continue

        changes.append((attr, str(cur_val), new_val_str))
        modified = new_content

    if not changes:
        print("\n  No config changes needed (already optimal or no improvement).")
        return True

    # Verify the result is valid Python
    try:
        ast.parse(modified)
    except SyntaxError as e:
        logger.error(f"Generated config has syntax error: {e}. Aborting.")
        return False

    print(f"\n  Config changes ({'DRY RUN — not written' if dry_run else 'WRITING'}):")
    for attr, old, new in changes:
        print(f"    {attr}: {old} → {new}")

    if not dry_run:
        Path(config_path).write_text(modified)
        print(f"  Written to {config_path}")

    return True


# ─────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Iterative Polybot Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backtest/optimizer.py                     # run with defaults, dry-run
  python backtest/optimizer.py --markets 150       # more data, better signal
  python backtest/optimizer.py --apply             # write best config to config.py
  python backtest/optimizer.py --force-refresh     # re-fetch & re-ask Claude
  python backtest/optimizer.py --cost-limit 0.05  # tight budget
        """,
    )
    parser.add_argument("--markets",       type=int,   default=100,
                        help="Resolved markets to fetch (default: 100)")
    parser.add_argument("--cost-limit",    type=float, default=2.0,
                        help="Max USD spend on Claude calls (default: 2.00)")
    parser.add_argument("--iterations",    type=int,   default=15,
                        help="Max optimization iterations (default: 15)")
    parser.add_argument("--delta",         type=float, default=CONVERGENCE_DELTA,
                        help=f"Convergence threshold avg PnL/trade (default: {CONVERGENCE_DELTA})")
    parser.add_argument("--apply",         action="store_true",
                        help="Write optimal config to config.py (default: dry-run)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Re-fetch markets and re-call Claude even if cache exists")
    parser.add_argument("--cache",         type=str,   default=DEFAULT_CACHE,
                        help=f"Cache file path (default: {DEFAULT_CACHE})")
    parser.add_argument("--dune",          action="store_true",
                        help="Use Dune Analytics for real historical entry prices "
                             "(requires DUNE_API_KEY in .env)")
    parser.add_argument("--lookback",      type=int,   default=180,
                        help="Days of market history to include when using --dune (default: 180)")
    args = parser.parse_args()

    from config import CLAUDE_MODEL
    import os
    dune_key = os.getenv("DUNE_API_KEY", "") if args.dune else ""
    if args.dune and not dune_key:
        print("\n  ERROR: --dune requires DUNE_API_KEY in .env")
        sys.exit(1)

    t0 = time.time()

    print(f"\n{'═'*72}")
    print("  POLYBOT ITERATIVE PARAMETER OPTIMIZER")
    print(f"{'═'*72}")
    print(f"  Markets:      {args.markets}")
    print(f"  Cost limit:   ${args.cost_limit:.2f}")
    print(f"  Iterations:   {args.iterations}")
    print(f"  Delta:        {args.delta}")
    print(f"  Mode:         {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"  Data source:  {'Dune Analytics (real prices, ' + str(args.lookback) + 'd lookback)' if dune_key else 'Polymarket gamma API (synthetic prices)'}")
    print(f"  Cache:        {args.cache}")
    preflight = estimate_preflight_cost(args.markets, CLAUDE_MODEL)
    print(f"  Est. cost:    ${preflight:.3f} (Claude, if cache miss, model={CLAUDE_MODEL})")
    print()

    # Load current config as baseline
    baseline_cfg = OptimizerConfig.from_config_module()
    print(f"  Baseline config: {baseline_cfg.summary()}")

    # Fetch estimates (uses cache if available)
    try:
        estimates, actual_cost = load_or_fetch(
            n_markets=args.markets,
            cost_limit=args.cost_limit,
            model=CLAUDE_MODEL,
            force_refresh=args.force_refresh,
            cache_path=args.cache,
            dune_key=dune_key,
            lookback_days=args.lookback,
        )
    except RuntimeError as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)

    if not estimates:
        print("\n  ERROR: No estimates available. Check API keys and connectivity.")
        sys.exit(1)

    print(f"  Estimates loaded: {len(estimates)} markets")

    # Run optimization
    history = run_optimizer(
        estimates=estimates,
        baseline=baseline_cfg,
        max_iterations=args.iterations,
        delta_threshold=args.delta,
    )

    # Print each iteration
    for result in history:
        print_iteration(result)

    elapsed = time.time() - t0

    # Final summary
    print_final_report(history, actual_cost, elapsed)

    # Find best config across all iterations
    best_result = max(history, key=lambda r: r.stats.avg_pnl)
    optimal_cfg = best_result.config

    # Apply or preview
    config_path = str(Path(__file__).parent.parent / "config.py")
    apply_optimal_config(
        config_path=config_path,
        baseline_cfg=baseline_cfg,
        optimal_cfg=optimal_cfg,
        dry_run=not args.apply,
    )

    if not args.apply and _diff_configs(baseline_cfg, optimal_cfg):
        print(f"\n  To apply: python backtest/optimizer.py --apply")
        if not args.force_refresh:
            print(f"  (Cache exists — re-run will cost $0.00)")


if __name__ == "__main__":
    main()
