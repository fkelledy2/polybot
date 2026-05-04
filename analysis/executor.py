"""
Execute improvements identified by the performance analyzer.
Makes actual code changes, commits to git, and deploys.

Only operates when PAPER_TRADING=True (safety gate).
"""

import logging
import sys
import subprocess
import re
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


class ImprovementsExecutor:
    """Execute and deploy improvements identified by analysis."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.changes_made = []
        self.config_path = Path("config.py")

    def execute_improvements(self, analysis_report: dict) -> dict:
        """
        Execute free improvements from the analysis report.
        Returns dict with {status, changes_made, commits, deployed}.
        """
        from config import PAPER_TRADING

        result = {
            "status": "skipped",
            "reason": "",
            "changes_made": [],
            "git_commits": [],
            "deployed": False,
        }

        # Safety gate 1: Only act in paper trading mode
        if not PAPER_TRADING:
            result["reason"] = "Not in paper trading mode"
            logger.warning("Executor disabled: PAPER_TRADING=False")
            return result

        # Safety gate 2: Check if critical issues are FIXABLE by improvements
        # vs unrecoverable system failures
        critical = analysis_report.get("analysis", {}).get("critical_issues", [])
        unrecoverable_keywords = ["database", "config error", "api", "connection"]
        has_unrecoverable = any(
            any(kw in str(issue).lower() for kw in unrecoverable_keywords)
            for issue in critical
        )

        if has_unrecoverable:
            result["reason"] = "Unrecoverable system issues prevent execution"
            logger.warning(f"Executor blocked: {result['reason']}")
            return result

        # Performance-related critical issues (bad win rate, extreme prices) are
        # EXACTLY what we want to fix, so they don't block execution
        logger.info(f"Found {len(critical)} critical issues - proceeding with improvements")

        logger.info("Starting improvements executor...")

        # Get recommendations
        improvements = (
            analysis_report.get("recommendations", {})
            .get("auto_implement", [])
        )

        if not improvements:
            result["reason"] = "No improvements to implement"
            return result

        # Execute each improvement
        for improvement in improvements:
            name = improvement.get("name", "")
            changes = improvement.get("changes", {})

            if not changes:
                continue

            logger.info(f"Executing: {name}")

            if name == "Extreme Price Filter":
                self._implement_extreme_price_filter(changes)

            elif name == "Category Confidence Ceiling":
                self._implement_category_ceiling(changes)

            elif name == "Elite Wallet Veto":
                self._implement_wallet_veto(changes)

            elif name == "Calibration Feedback Loop":
                self._implement_calibration_tracking(changes)

        # Commit all changes
        if self.changes_made:
            result["status"] = "success"
            result["changes_made"] = self.changes_made
            commit_hash = self._commit_changes(analysis_report)
            if commit_hash:
                result["git_commits"].append(commit_hash)
                result["deployed"] = self._push_to_github()
        else:
            result["status"] = "no_changes"
            result["reason"] = "Analysis recommended no changes"

        return result

    def _implement_extreme_price_filter(self, changes: dict):
        """Add extreme price edge multiplier to config."""
        min_edge_extreme = changes.get("MIN_EDGE_TO_TRADE_EXTREME", 0.15)
        threshold = changes.get("EXTREME_PRICE_THRESHOLD", 0.05)

        config_content = self.config_path.read_text()

        # Check if already exists
        if "MIN_EDGE_TO_TRADE_EXTREME" in config_content:
            logger.info("Extreme price filter already configured")
            return

        # Find where to insert (after MIN_EDGE_TO_TRADE)
        pattern = r'(MIN_EDGE_TO_TRADE\s*=\s*[\d.]+\s*# [^\n]*)'
        insertion = (
            f"\n# ── Extreme Price Markets ───────────────────────\n"
            f"# Markets at extreme prices (<5% or >95%) require higher edge.\n"
            f"MIN_EDGE_TO_TRADE_EXTREME = {min_edge_extreme}  # 15% edge for extreme prices\n"
            f"EXTREME_PRICE_THRESHOLD = {threshold}       # Markets below 5% or above 95%\n"
        )

        if re.search(pattern, config_content):
            config_content = re.sub(
                pattern,
                r"\1" + insertion,
                config_content,
                count=1
            )
            self._write_config(config_content)
            self.changes_made.append("extreme_price_filter")
            logger.info("✅ Extreme price filter added to config")
        else:
            logger.warning("Could not find MIN_EDGE_TO_TRADE to add extreme filter")

    def _implement_category_ceiling(self, changes: dict):
        """Disable or reduce confidence for weak categories."""
        disabled = changes.get("DISABLE_CATEGORIES", [])

        if not disabled:
            logger.info("No categories to disable")
            return

        config_content = self.config_path.read_text()

        # Check if already configured with the same categories
        if "DISABLED_CATEGORIES" in config_content:
            logger.info("Category ceiling already configured")
            return

        # Add category disabling config
        insertion = (
            f"\n# ── Category Performance Tuning ────────────────\n"
            f"# Disable trading in weak-performing categories.\n"
            f"DISABLED_CATEGORIES = {disabled}\n"
        )

        # Insert after market resolution window section
        if "MAX_DAYS_TO_RESOLVE" in config_content:
            idx = config_content.find("MAX_DAYS_TO_RESOLVE")
            idx = config_content.find("\n", idx) + 1
            config_content = config_content[:idx] + insertion + config_content[idx:]
        else:
            config_content += insertion

        self._write_config(config_content)
        self.changes_made.append("category_ceiling")
        logger.info(f"✅ Category filtering added: {disabled}")

    def _implement_wallet_veto(self, changes: dict):
        """Enable wallet veto signals."""
        config_content = self.config_path.read_text()

        if "ENABLE_WALLET_VETO" in config_content:
            logger.info("Wallet veto already configured")
            return

        insertion = (
            f"\n# ── Wallet Veto Signal ─────────────────────────\n"
            f"# Disable trades when elite wallets disagree with Claude.\n"
            f"ENABLE_WALLET_VETO = True\n"
            f"WALLET_VETO_ON_EXTREME = True  # Especially for extreme prices\n"
        )

        # Insert in wallet section
        if "ENABLE_WALLET_TRACKING" in config_content:
            idx = config_content.find("ENABLE_WALLET_TRACKING")
            idx = config_content.find("\n", idx) + 1
            config_content = config_content[:idx] + insertion + config_content[idx:]
        else:
            config_content += insertion

        self._write_config(config_content)
        self.changes_made.append("wallet_veto")
        logger.info("✅ Wallet veto enabled")

    def _implement_calibration_tracking(self, changes: dict):
        """Enable calibration feedback loop."""
        config_content = self.config_path.read_text()

        if "TRACK_CALIBRATION" in config_content:
            logger.info("Calibration tracking already configured")
            return

        insertion = (
            f"\n# ── Calibration Feedback ───────────────────────\n"
            f"# Track probability estimates vs actual resolutions.\n"
            f"TRACK_CALIBRATION = True\n"
        )

        config_content += insertion
        self._write_config(config_content)
        self.changes_made.append("calibration_tracking")
        logger.info("✅ Calibration tracking enabled")

    def _write_config(self, content: str):
        """Write updated config to file."""
        if self.dry_run:
            logger.info("[DRY RUN] Would write config changes")
        else:
            self.config_path.write_text(content)

    def _commit_changes(self, analysis_report: dict) -> Optional[str]:
        """Commit all changes to git."""
        if not self.changes_made:
            return None

        try:
            # Stage config.py
            subprocess.run(
                ["git", "add", "config.py"],
                capture_output=True,
                timeout=10,
                check=True,
            )

            # Build commit message
            overall = analysis_report.get("analysis", {}).get("overall", {})
            improvements_list = "\n".join(
                f"  • {name}" for name in self.changes_made
            )

            message = f"""Auto: Implement free improvements from daily analysis

Performance: {overall.get('wins', 0)}-{overall.get('losses', 0)}
  (WR: {overall.get('win_rate', 0):.0%})

Implemented improvements:
{improvements_list}

Safety gate: Paper trading mode + no critical blockers
Generated by autonomous trading analyzer"""

            result = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                # Extract commit hash
                hash_match = re.search(
                    r"\[main ([a-f0-9]+)\]", result.stdout
                )
                commit_hash = hash_match.group(1) if hash_match else "unknown"
                logger.info(f"✅ Committed: {commit_hash}")
                return commit_hash
            else:
                logger.warning(f"Commit failed: {result.stderr}")
                return None

        except Exception as e:
            logger.error(f"Failed to commit: {e}")
            return None

    def _push_to_github(self) -> bool:
        """Push changes to GitHub."""
        try:
            result = subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info("✅ Pushed to GitHub")
                return True
            else:
                logger.warning(f"Push failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Failed to push: {e}")
            return False


def main():
    """Demo the executor."""
    from analysis.performance import PerformanceAnalyzer

    analyzer = PerformanceAnalyzer()
    recs = analyzer.generate_recommendations()

    executor = ImprovementsExecutor(dry_run=True)
    result = executor.execute_improvements(recs)

    print("\n" + "="*100)
    print("IMPROVEMENTS EXECUTOR DEMO (DRY RUN)")
    print("="*100)
    print(f"\nStatus: {result['status']}")
    print(f"Changes: {result['changes_made']}")
    print(f"Deployed: {result['deployed']}")

    if result["changes_made"]:
        print("\nWould implement:")
        for change in result["changes_made"]:
            print(f"  • {change}")


if __name__ == "__main__":
    main()
