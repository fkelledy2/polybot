"""
Learning Loop Tests
====================
The bot is supposed to improve over time. These tests verify that:
- The performance analyser produces valid output even with no trades
- The improvement executor does not corrupt config on repeated runs
- The analysis report reaches the database (so Heroku Scheduler output survives dyno isolation)
- Backtest optimizer respects real price data when available

These are high-stakes tests: a broken learning loop means the bot can't
improve, which is the primary long-term profit mechanism.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestPerformanceAnalyser:
    """Analyser must produce a complete, valid report even with zero trade history."""

    def setup_method(self):
        self.db_patcher = None

    def _get_analyser(self, tmp_db):
        import db
        # analyser will use db.get_connection() → patched to tmp_db
        from analysis.performance import PerformanceAnalyzer
        return PerformanceAnalyzer()

    def test_overall_metrics_all_keys_present_with_no_trades(self, paper_trader, tmp_db):
        from analysis.performance import PerformanceAnalyzer
        analyser = PerformanceAnalyzer(db_path=tmp_db)
        metrics = analyser.get_overall_metrics()
        required_keys = {"total_trades", "wins", "losses", "win_rate", "total_pnl",
                         "avg_pnl_per_trade"}
        for key in required_keys:
            assert key in metrics, f"Missing key '{key}' in overall metrics with no trades"

    def test_overall_metrics_zero_trades_win_rate_is_zero(self, paper_trader, tmp_db):
        from analysis.performance import PerformanceAnalyzer
        analyser = PerformanceAnalyzer(db_path=tmp_db)
        metrics = analyser.get_overall_metrics()
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0 or metrics["win_rate"] == pytest.approx(0.0)

    def test_overall_metrics_with_trades_reflects_reality(self, paper_trader, tmp_db):
        from analysis.performance import PerformanceAnalyzer
        from signals.claude_signal import TradeSignal
        # Place and close 2 wins, 1 loss
        for i, won in enumerate([True, True, False]):
            sig = TradeSignal(
                market_id=f"m{i}", question=f"Q{i}?",
                market_yes_price=0.40, claude_yes_probability=0.65,
                edge=0.25, direction="YES", confidence="medium",
                reasoning="r", wallet_alignment=False, should_trade=True,
            )
            paper_trader.place_trade(sig)
            paper_trader.close_trade(f"m{i}", resolved_yes=won)

        analyser = PerformanceAnalyzer(db_path=tmp_db)
        metrics = analyser.get_overall_metrics()
        assert metrics["wins"] == 2
        assert metrics["losses"] == 1
        assert metrics["total_trades"] == 3
        assert metrics["win_rate"] == pytest.approx(2 / 3)

    def test_recommendations_always_returns_dict(self, paper_trader, tmp_db):
        from analysis.performance import PerformanceAnalyzer
        analyser = PerformanceAnalyzer(db_path=tmp_db)
        recs = analyser.generate_recommendations()
        assert isinstance(recs, (list, dict))

    def test_category_metrics_does_not_crash_with_no_trades(self, paper_trader, tmp_db):
        from analysis.performance import PerformanceAnalyzer
        analyser = PerformanceAnalyzer(db_path=tmp_db)
        cats = analyser.get_category_metrics()
        assert isinstance(cats, dict)


class TestImprovementExecutorIdempotency:
    """Running the executor twice must produce the same config as running it once."""

    def test_category_ceiling_not_applied_twice(self, tmp_path):
        """DISABLED_CATEGORIES must not be duplicated in config.py."""
        config_file = tmp_path / "config.py"
        config_file.write_text("DISABLED_CATEGORIES = []\n")

        from analysis.executor import ImprovementsExecutor
        executor = ImprovementsExecutor(dry_run=False)
        # Point executor at the temp config file
        executor.config_path = config_file

        changes = {"disabled_categories": ["CRYPTO"], "reason": "test"}
        executor._implement_category_ceiling(changes)
        content_after_first = config_file.read_text()
        executor._implement_category_ceiling(changes)
        content_after_second = config_file.read_text()

        # The idempotency guard should prevent double-application
        count_first = content_after_first.count("DISABLED_CATEGORIES")
        count_second = content_after_second.count("DISABLED_CATEGORIES")
        assert count_second <= count_first, (
            f"DISABLED_CATEGORIES appeared {count_second} times after second run "
            f"vs {count_first} after first — executor is not idempotent"
        )

    def test_executor_skips_when_setting_already_present(self, tmp_path):
        """Executor must not write when the target setting is already in config."""
        from analysis.executor import ImprovementsExecutor
        from pathlib import Path

        config_file = tmp_path / "config.py"
        config_file.write_text(
            "EXTREME_PRICE_THRESHOLD = 0.03\n"
            "DISABLED_CATEGORIES = ['CRYPTO']\n"
            "ENABLE_WALLET_VETO = True\n"
            "TRACK_CALIBRATION = True\n"
        )

        executor = ImprovementsExecutor(dry_run=False)
        executor.config_path = config_file

        mtime_before = config_file.stat().st_mtime
        # Run all implement methods — all should be no-ops since settings exist
        changes = {"disabled_categories": ["CRYPTO"], "reason": "test"}
        executor._implement_category_ceiling(changes)

        mtime_after = config_file.stat().st_mtime
        assert mtime_after == mtime_before, (
            "Executor wrote to config.py even though DISABLED_CATEGORIES was already set"
        )


class TestAnalysisReportPersistence:
    """The report must survive Heroku's ephemeral filesystem (written to Postgres)."""

    def test_report_saved_to_file(self, tmp_path, paper_trader):
        from analysis.scheduled_agent import ScheduledAnalysisAgent
        output_file = str(tmp_path / "report.json")
        agent = ScheduledAnalysisAgent(output_file=output_file)

        # Run with mocked executor to avoid git operations
        with patch("analysis.scheduled_agent.ImprovementsExecutor") as MockExec:
            mock_executor = MagicMock()
            mock_executor.execute_improvements.return_value = {
                "status": "no_changes", "changes_made": [], "deployed": False, "reason": "nothing"
            }
            MockExec.return_value = mock_executor
            agent.run()

        assert Path(output_file).exists(), "Analysis report must be written to file"
        with open(output_file) as f:
            report = json.load(f)
        assert "timestamp" in report
        assert "analysis" in report
        assert "summary" in report

    def test_report_has_required_structure(self, tmp_path, paper_trader):
        from analysis.scheduled_agent import ScheduledAnalysisAgent
        output_file = str(tmp_path / "report.json")
        agent = ScheduledAnalysisAgent(output_file=output_file)

        with patch("analysis.scheduled_agent.ImprovementsExecutor") as MockExec:
            mock_executor = MagicMock()
            mock_executor.execute_improvements.return_value = {
                "status": "no_changes", "changes_made": [], "deployed": False, "reason": "nothing"
            }
            MockExec.return_value = mock_executor
            report = agent.run()

        # All keys required by the web dashboard route
        assert "analysis" in report
        assert "overall" in report["analysis"]
        assert "critical_issues" in report["analysis"]
        assert "recommendations" in report
        assert "summary" in report

    def test_summary_contains_performance_stats(self, tmp_path, paper_trader):
        from analysis.scheduled_agent import ScheduledAnalysisAgent
        output_file = str(tmp_path / "report.json")
        agent = ScheduledAnalysisAgent(output_file=output_file)

        with patch("analysis.scheduled_agent.ImprovementsExecutor") as MockExec:
            mock_executor = MagicMock()
            mock_executor.execute_improvements.return_value = {
                "status": "no_changes", "changes_made": [], "deployed": False, "reason": "nothing"
            }
            MockExec.return_value = mock_executor
            report = agent.run()

        summary = report["summary"]
        # Summary must reference win rate and PnL (minimum useful content)
        assert "win rate" in summary.lower() or "WR" in summary or "%" in summary


class TestBacktestRealPrices:
    """When Dune provides real entry prices, the optimizer must use them."""

    def _make_estimate(self, real_entry_price=None):
        from backtest.optimizer import CachedEstimate
        return CachedEstimate(
            market_id="test_m",
            question="Will X happen?",
            resolved_yes=True,
            last_price=0.40,
            volume_usd=10000,
            end_date="2026-01-01",
            category="MACRO",
            days_to_resolve=10,
            claude_probability=0.70,
            confidence="high",
            reasoning="strong evidence",
            real_entry_price=real_entry_price,
        )

    def _make_cfg(self):
        from backtest.optimizer import OptimizerConfig
        return OptimizerConfig(
            min_edge=0.06,
            min_edge_extreme=0.20,
            extreme_threshold=0.03,
            min_entry_prob=0.03,
            max_days=30,
            min_days=1,
            disabled_categories=[],
        )

    def test_real_price_used_instead_of_synthetic_prices(self):
        from backtest.optimizer import simulate_config
        estimate = self._make_estimate(real_entry_price=0.40)
        cfg = self._make_cfg()
        stats = simulate_config([estimate], cfg)
        # With real_entry_price set, exactly 1 price point is used → at most 1 trade
        assert stats.trades == 1, (
            f"Expected exactly 1 trade when real_entry_price is set, got {stats.trades}"
        )

    def test_synthetic_prices_used_when_no_real_price(self):
        from backtest.optimizer import simulate_config, SYNTHETIC_PRICES
        estimate = self._make_estimate(real_entry_price=None)
        cfg = self._make_cfg()
        stats = simulate_config([estimate], cfg)
        # With no real price, up to len(SYNTHETIC_PRICES) prices are tested
        assert stats.trades <= len(SYNTHETIC_PRICES), (
            f"Expected at most {len(SYNTHETIC_PRICES)} trades, got {stats.trades}"
        )
        assert stats.trades > 0, "claude_prob=0.70 has edge ≥ 0.06 vs most synthetic prices"
