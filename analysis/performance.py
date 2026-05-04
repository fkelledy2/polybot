"""
Comprehensive trading performance analysis and calibration feedback.
Analyzes trade history to identify systematic biases and generate improvement recommendations.
"""

import logging
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from signals.categorizer import detect_category

logger = logging.getLogger(__name__)


@dataclass
class CategoryMetrics:
    """Performance metrics for a single category."""
    category: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl: float
    avg_edge_claimed: float
    price_range: tuple[float, float]  # (min, max) entry prices

    def __repr__(self):
        return (
            f"{self.category:15} | "
            f"W/L: {self.wins:2}/{self.losses:2} ({self.win_rate:5.1%}) | "
            f"PnL: ${self.avg_pnl:7.2f} avg | "
            f"Edge: {self.avg_edge_claimed*100:+5.1f}% | "
            f"Prices: {self.price_range[0]:.1%}-{self.price_range[1]:.1%}"
        )


@dataclass
class CalibrationIssue:
    """A specific calibration problem identified."""
    severity: str  # "critical", "high", "medium", "low"
    category: Optional[str]
    issue: str
    impact: str
    recommendation: str
    affected_trades: int


class PerformanceAnalyzer:
    """Analyze trade history and generate improvement recommendations."""

    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self.trades = self._load_trades()

    def _load_trades(self) -> list[dict]:
        """Load all trades from database. Uses Postgres if DATABASE_URL is set, else SQLite."""
        import os
        database_url = os.getenv("DATABASE_URL", "")

        if database_url:
            return self._load_trades_postgres(database_url)
        return self._load_trades_sqlite()

    def _load_trades_postgres(self, database_url: str) -> list[dict]:
        """Load trades from Heroku Postgres."""
        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(database_url, sslmode="require")
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM trades ORDER BY timestamp")
            trades = [dict(row) for row in cur.fetchall()]
            conn.close()
            logger.info(f"Loaded {len(trades)} trades from Postgres")
            return trades
        except ImportError:
            logger.warning("psycopg2 not available, falling back to SQLite")
            return self._load_trades_sqlite()
        except Exception as e:
            logger.warning(f"Postgres load failed ({e}), falling back to SQLite")
            return self._load_trades_sqlite()

    def _load_trades_sqlite(self) -> list[dict]:
        """Load trades from local SQLite database."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY timestamp")
            trades = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.info(f"Loaded {len(trades)} trades from SQLite")
            return trades
        except Exception as e:
            logger.error(f"Failed to load trades: {e}")
            return []

    def get_overall_metrics(self) -> dict:
        """Get high-level portfolio metrics."""
        if not self.trades:
            return {
                "total_trades": 0,
                "closed_trades": 0,
                "open_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl_per_trade": 0,
                "win_loss_ratio": 0,
                "avg_entry_price": 0.5,
            }

        closed = [t for t in self.trades if t["status"] in ("won", "lost")]
        wins = [t for t in self.trades if t["status"] == "won"]
        losses = [t for t in self.trades if t["status"] == "lost"]

        total_pnl = sum(t["pnl"] or 0 for t in closed)
        avg_entry = sum(t["entry_price"] or 0.5 for t in self.trades) / len(self.trades)

        return {
            "total_trades": len(self.trades),
            "closed_trades": len(closed),
            "open_trades": len(self.trades) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) if closed else 0,
            "total_pnl": total_pnl,
            "avg_pnl_per_trade": total_pnl / len(closed) if closed else 0,
            "win_loss_ratio": len(wins) / max(1, len(losses)),
            "avg_entry_price": avg_entry,
        }

    def get_category_metrics(self) -> dict[str, CategoryMetrics]:
        """Get performance broken down by market category."""
        by_cat: dict[str, list] = {}

        for trade in self.trades:
            cat = detect_category(trade["question"] or "")
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(trade)

        metrics = {}
        for cat, trades in by_cat.items():
            closed = [t for t in trades if t["status"] in ("won", "lost")]
            wins = [t for t in trades if t["status"] == "won"]
            losses = [t for t in trades if t["status"] == "lost"]

            if closed:
                pnls = [t["pnl"] or 0 for t in closed]
                avg_pnl = sum(pnls) / len(pnls)
            else:
                avg_pnl = 0

            edges = [t["edge"] or 0 for t in trades]
            avg_edge = sum(edges) / len(edges) if edges else 0

            prices = [t["entry_price"] for t in trades if t["entry_price"] is not None]
            price_range = (min(prices), max(prices)) if prices else (0.5, 0.5)

            metrics[cat] = CategoryMetrics(
                category=cat,
                total_trades=len(trades),
                wins=len(wins),
                losses=len(losses),
                win_rate=len(wins) / len(closed) if closed else 0,
                avg_pnl=avg_pnl,
                avg_edge_claimed=avg_edge,
                price_range=price_range,
            )

        return metrics

    def get_extreme_price_trades(self, threshold: float = 0.05) -> list[dict]:
        """Identify trades at extreme market prices (<5% or >95%)."""
        return [
            t for t in self.trades
            if t["entry_price"] is not None and (
                t["entry_price"] < threshold or t["entry_price"] > (1 - threshold)
            )
        ]

    def identify_calibration_issues(self) -> list[CalibrationIssue]:
        """Identify systematic problems with Claude's estimates."""
        issues = []

        overall = self.get_overall_metrics()

        # Issue 1: Win rate critically low
        if overall["closed_trades"] >= 3 and overall["win_rate"] < 0.25:
            issues.append(CalibrationIssue(
                severity="critical",
                category=None,
                issue=f"Win rate only {overall['win_rate']:.0%} across {overall['closed_trades']} closed trades",
                impact="Portfolio is losing money faster than random chance",
                recommendation="Immediate: Disable auto-trading. Run full calibration analysis before proceeding.",
                affected_trades=overall["closed_trades"],
            ))

        # Issue 2: Extreme price overconfidence
        extreme_trades = self.get_extreme_price_trades(threshold=0.05)
        extreme_losses = [t for t in extreme_trades if t["status"] == "lost"]
        if len(extreme_losses) >= 2:
            loss_pct = len(extreme_losses) / len(extreme_trades) if extreme_trades else 0
            issues.append(CalibrationIssue(
                severity="critical",
                category=None,
                issue=f"{len(extreme_losses)}/{len(extreme_trades)} extreme-price trades lost ({loss_pct:.0%})",
                impact="Claude is finding false 'mispricings' at market extremes where pricing is most efficient",
                recommendation="Add confidence ceiling: require 15% edge (not 10%) for markets <5% or >95% probability",
                affected_trades=len(extreme_trades),
            ))

        # Issue 3: Category-specific poor performance
        cat_metrics = self.get_category_metrics()
        for cat, metrics in cat_metrics.items():
            if metrics.total_trades >= 2 and metrics.win_rate < 0.33:
                issues.append(CalibrationIssue(
                    severity="high",
                    category=cat,
                    issue=f"{cat}: {metrics.wins}/{metrics.total_trades} wins ({metrics.win_rate:.0%})",
                    impact=f"Consistent losses in {cat} category indicates systematic bias",
                    recommendation=f"Reduce confidence threshold for {cat} by 20%, or disable category entirely",
                    affected_trades=metrics.total_trades,
                ))

        # Issue 4: Edge claims vs actual performance
        all_closed = [t for t in self.trades if t["status"] in ("won", "lost")]
        if all_closed:
            avg_claimed_edge = sum(t["edge"] or 0 for t in all_closed) / len(all_closed)
            if avg_claimed_edge > 0.10 and overall["win_rate"] < 0.50:
                issues.append(CalibrationIssue(
                    severity="high",
                    category=None,
                    issue=f"Claims {avg_claimed_edge*100:.1f}% avg edge but only {overall['win_rate']:.0%} win rate",
                    impact="Claude's edge estimates are unreliable; market is less mispriced than Claude believes",
                    recommendation="Apply calibration correction: reduce all probability estimates by 5-10% toward market price",
                    affected_trades=len(all_closed),
                ))

        return issues

    def generate_recommendations(self) -> dict:
        """Generate actionable recommendations."""
        issues = self.identify_calibration_issues()
        overall = self.get_overall_metrics()
        cat_metrics = self.get_category_metrics()

        recommendations = {
            "timestamp": datetime.now().isoformat(),
            "critical_issues": [i for i in issues if i.severity == "critical"],
            "high_issues": [i for i in issues if i.severity == "high"],
            "overall_status": self._assess_status(overall),
            "category_breakdown": {
                cat: {
                    "trades": m.total_trades,
                    "win_rate": f"{m.win_rate:.0%}",
                    "avg_pnl": f"${m.avg_pnl:.2f}",
                }
                for cat, m in cat_metrics.items()
            },
            "suggested_config_changes": self._suggest_config_changes(issues, cat_metrics),
            "apis_available": self._check_available_apis(),
            "next_actions": self._prioritize_actions(issues),
        }

        return recommendations

    def _assess_status(self, overall: dict) -> str:
        """Assess overall system health."""
        if overall["closed_trades"] == 0:
            return "NO_DATA: Need at least 5 closed trades for reliable assessment"

        wr = overall["win_rate"]
        if wr < 0.33:
            return "CRITICAL: Win rate below 33% (worse than random). Stop trading."
        elif wr < 0.45:
            return "POOR: Win rate 33-45%. Require high-confidence improvements before resuming."
        elif wr < 0.55:
            return "WEAK: Win rate 45-55%. Implement recommended calibration fixes."
        else:
            return "ACCEPTABLE: Win rate >55%. Monitor for regression."

    def _suggest_config_changes(self, issues: list[CalibrationIssue],
                               cat_metrics: dict) -> list[str]:
        """Suggest specific config.py changes."""
        changes = []

        # Check for extreme price issue
        has_extreme_issue = any("extreme-price" in i.issue for i in issues)
        if has_extreme_issue:
            changes.append(
                "Add EXTREME_PRICE_EDGE_MULTIPLIER = 1.5 to config.py "
                "(require 15% edge instead of 10% for markets <5% or >95%)"
            )

        # Check for low confidence issue
        critical_cats = [i.category for i in issues if i.severity == "critical" and i.category]
        if critical_cats:
            changes.append(
                f"Disable auto-trading for categories: {', '.join(critical_cats)}"
            )

        # Check edge claim vs performance
        has_edge_issue = any("claims" in i.issue and "edge" in i.issue for i in issues)
        if has_edge_issue:
            changes.append(
                "Increase MIN_EDGE_TO_TRADE from 10% to 12% and add calibration correction layer"
            )

        return changes

    def _check_available_apis(self) -> dict[str, bool]:
        """Check which optional APIs are configured."""
        import os
        from config import (
            BRAVE_SEARCH_API_KEY, ODDS_API_KEY, DISCORD_WEBHOOK_URL,
            DATABASE_URL, ENABLE_WALLET_TRACKING
        )

        # Check both config AND environment variables (for production/Heroku)
        brave_search = bool(BRAVE_SEARCH_API_KEY or os.getenv("BRAVE_SEARCH_API_KEY"))
        odds_api = bool(ODDS_API_KEY or os.getenv("ODDS_API_KEY"))
        discord = bool(DISCORD_WEBHOOK_URL or os.getenv("DISCORD_WEBHOOK_URL"))

        return {
            "BRAVE_SEARCH": brave_search,
            "ODDS_API": odds_api,
            "DISCORD_ALERTS": discord,
            "POSTGRESQL": bool(DATABASE_URL),
            "WALLET_TRACKING": ENABLE_WALLET_TRACKING,
        }

    def _prioritize_actions(self, issues: list[CalibrationIssue]) -> list[str]:
        """Prioritize next actions."""
        actions = []

        critical_count = len([i for i in issues if i.severity == "critical"])
        if critical_count > 0:
            actions.append("🔴 STOP: Critical issues found. Disable auto-trading immediately.")
            actions.append("Run detailed root cause analysis before resuming trades.")

        actions.append("Implement extreme price filter (low cost, high impact)")
        actions.append("Enable ODDS_API for sports markets (improve calibration)")
        actions.append("Build live feedback loop: compare estimates vs actual resolutions")
        actions.append("Run extended-thinking confirmation on trades with edge >15%")

        return actions


def main():
    """Quick analysis CLI."""
    analyzer = PerformanceAnalyzer()

    print("\n" + "="*100)
    print("TRADING SYSTEM HEALTH REPORT")
    print("="*100)

    overall = analyzer.get_overall_metrics()
    print(f"\nOVERALL: {overall['total_trades']} trades | "
          f"{overall['wins']}-{overall['losses']} record | "
          f"{overall['win_rate']:.1%} WR | "
          f"${overall['total_pnl']:.2f} PnL")

    print("\nCATEGORY BREAKDOWN:")
    for cat, metrics in analyzer.get_category_metrics().items():
        print(f"  {metrics}")

    issues = analyzer.identify_calibration_issues()
    if issues:
        print("\nCALIBRATION ISSUES:")
        for issue in sorted(issues, key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x.severity)):
            print(f"\n  [{issue.severity.upper()}] {issue.issue}")
            print(f"    → {issue.recommendation}")

    recs = analyzer.generate_recommendations()
    print("\nSUGGESTED ACTIONS:")
    for action in recs["next_actions"]:
        print(f"  • {action}")


if __name__ == "__main__":
    main()
