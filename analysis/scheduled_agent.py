#!/usr/bin/env python3
"""
Scheduled Trading System Analyzer Agent

This agent runs autonomously to:
1. Analyze trading performance
2. Identify issues and opportunities
3. Recommend improvements
4. Prompt for API key setup only when needed
5. Generate a summary report for human review

Can be invoked via:
  - Scheduled cron job
  - Remote managed agent
  - Manual CLI run
"""

import logging
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.performance import PerformanceAnalyzer
from analysis.improvements import SystemImprovementEngine
from analysis.executor import ImprovementsExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class ScheduledAnalysisAgent:
    """Autonomous agent for trading system analysis."""

    def __init__(self, output_file: str = ".claude/analysis_report.json"):
        self.analyzer = PerformanceAnalyzer()
        self.engine = SystemImprovementEngine()
        self.output_file = output_file
        self.report = {}

    def run(self) -> dict:
        """Execute full analysis and return results."""
        logger.info("Starting scheduled trading system analysis...")

        # Step 1: Analyze performance
        logger.info("Analyzing trading performance...")
        overall = self.analyzer.get_overall_metrics()
        categories = self.analyzer.get_category_metrics()
        issues = self.analyzer.identify_calibration_issues()
        recs = self.analyzer.generate_recommendations()

        # Step 2: Generate improvement plan
        logger.info("Generating improvement recommendations...")
        plan = self.engine.generate_implementation_plan(recs)

        # Step 3: Check for API keys needed
        logger.info("Checking API configuration...")
        pending_apis = self.engine.get_pending_api_keys()

        # Step 4: Build report
        self.report = {
            "timestamp": datetime.now().isoformat(),
            "analysis": {
                "overall": overall,
                "by_category": {
                    cat: {
                        "trades": m.total_trades,
                        "wins": m.wins,
                        "losses": m.losses,
                        "win_rate": f"{m.win_rate:.1%}",
                        "avg_pnl": f"${m.avg_pnl:.2f}",
                    }
                    for cat, m in categories.items()
                },
                "critical_issues": [
                    {
                        "severity": i.severity,
                        "issue": i.issue,
                        "impact": i.impact,
                        "recommendation": i.recommendation,
                    }
                    for i in issues
                ],
            },
            "recommendations": plan,
            "api_setup": {
                "pending": list(pending_apis.keys()),
                "details": pending_apis,
            },
            "summary": self._generate_summary(overall, issues, plan, pending_apis),
        }

        # Step 5: Save report
        self._save_report()

        # Step 6: Execute improvements (autonomous deployment)
        logger.info("Executing improvements...")
        execution_result = self._execute_improvements()
        self.report["execution"] = execution_result

        # Step 7: Log key findings
        self._log_findings()

        return self.report

    def _generate_summary(self, overall: dict, issues: list, plan: dict,
                         pending_apis: dict) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Trading System Health Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Performance: {overall['wins']}-{overall['losses']} "
            f"({overall['win_rate']:.0%} WR) across {overall['total_trades']} trades",
            f"P&L: ${overall['total_pnl']:.2f} total",
            "",
            f"Status: {plan['critical_status']}",
            "",
        ]

        if plan['immediate_actions']:
            lines.append("🔴 IMMEDIATE ACTIONS:")
            for action in plan['immediate_actions']:
                lines.append(f"  • {action['action']}: {action['reason']}")
            lines.append("")

        lines.append("✅ FREE IMPROVEMENTS (auto-implement):")
        for item in plan['auto_implement']:
            lines.append(f"  • {item['name']}")
            lines.append(f"    Impact: {item['estimated_impact']}")

        if plan['user_approval_needed']:
            lines.append("")
            lines.append("⚠️ API KEYS FOR HUMAN APPROVAL:")
            for item in plan['user_approval_needed']:
                cost_note = " (COSTS MONEY)" if item['cost_bearing'] else " (free)"
                lines.append(f"  • {item['api']}{cost_note}")
                lines.append(f"    Benefit: {item['benefit']}")

        if plan['future_enhancements']:
            lines.append("")
            lines.append("🚀 FUTURE ENHANCEMENTS:")
            for item in plan['future_enhancements']:
                lines.append(f"  • {item['name']}: {item['expected_benefit']}")

        lines.extend([
            "",
            "ESTIMATED IMPACT WITH FREE IMPROVEMENTS:",
            "  From extreme price filter: +5-10% win rate",
            "  From category tuning: +3-8% win rate",
            "  Target: 0% → ~35-40% win rate before requiring paid APIs",
        ])

        return "\n".join(lines)

    def _execute_improvements(self) -> dict:
        """Execute improvements identified by analysis."""
        executor = ImprovementsExecutor()
        result = executor.execute_improvements(self.report)

        if result["status"] == "success":
            logger.info(f"✅ Improvements executed: {result['changes_made']}")
            if result["deployed"]:
                logger.info("✅ Changes deployed to GitHub")
        elif result["status"] == "no_changes":
            logger.info("ℹ️ No improvements to execute")
        else:
            logger.info(f"⏭️ Executor skipped: {result['reason']}")

        return result

    def _save_report(self):
        """Save report to JSON file."""
        try:
            Path(self.output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.output_file, "w") as f:
                json.dump(self.report, f, indent=2, default=str)
            logger.info(f"Report saved to {self.output_file}")
        except Exception as e:
            logger.error(f"Failed to save report: {e}")

    def _log_findings(self):
        """Log key findings to console and logger."""
        print("\n" + "="*100)
        print(self.report["summary"])
        print("="*100 + "\n")

        # Show execution results
        exec_result = self.report.get("execution", {})
        if exec_result:
            print("AUTONOMOUS IMPROVEMENTS EXECUTION")
            print("-"*100)
            if exec_result["status"] == "success":
                print(f"✅ Status: SUCCESS")
                print(f"   Changes implemented: {', '.join(exec_result['changes_made'])}")
                if exec_result["deployed"]:
                    print(f"   Deployed to GitHub: {exec_result.get('git_commits', ['?'])[0][:8]}")
            else:
                print(f"ℹ️ Status: {exec_result['status'].upper()}")
                if exec_result.get("reason"):
                    print(f"   Reason: {exec_result['reason']}")
            print()

        # If critical issues, warn prominently
        if self.report["analysis"]["critical_issues"]:
            logger.warning(f"⚠️ Found {len(self.report['analysis']['critical_issues'])} critical issues!")
            logger.warning("Review report at: " + self.output_file)

        # If APIs available, suggest enabling them
        if self.report["api_setup"]["pending"]:
            logger.info(f"Optional APIs ready to enable: {', '.join(self.report['api_setup']['pending'])}")

    def should_prompt_for_api_setup(self) -> bool:
        """Check if human input is needed for API setup."""
        # Only prompt if there are COST-BEARING APIs available
        pending = self.engine.get_pending_api_keys()
        cost_bearing = [
            api for api, desc in pending.items()
            if api != "DISCORD_WEBHOOK_URL"  # Discord is free
        ]
        return len(cost_bearing) > 0 and len(self.report["analysis"]["critical_issues"]) == 0

    def prompt_for_api_setup(self):
        """Interactively prompt for optional API key setup."""
        pending = self.engine.get_pending_api_keys()
        print("\n" + "="*100)
        print("OPTIONAL: Set up cost-bearing APIs for enhanced functionality")
        print("="*100)

        for api, desc in pending.items():
            if api == "DISCORD_WEBHOOK_URL":
                continue  # Skip free APIs

            print(f"\n{api}")
            print(f"  {desc}")
            response = input(f"  Set up {api}? (y/n) [n]: ").strip().lower()

            if response == "y":
                print(f"  Instructions for {api}:")
                print(f"    1. Visit the API provider")
                print(f"    2. Generate an API key")
                print(f"    3. Add to .env or environment: {api}=YOUR_KEY")
                print(f"    4. Restart the bot to use")


def auto_commit_recommendations(report: dict, paper_trading: bool = True) -> bool:
    """
    Auto-commit analysis and recommendations to git if conditions are met.
    Returns True if commit was made, False otherwise.
    """
    import subprocess
    import os

    if not paper_trading:
        logger.warning("Auto-commit disabled: not in paper trading mode")
        return False

    try:
        # Check git status
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.warning("Git not available or not a repo")
            return False

        # Only commit the analysis report file
        report_file = ".claude/analysis_report.json"
        if report_file not in result.stdout:
            logger.info("No new analysis report to commit")
            return False

        # Stage the report
        subprocess.run(["git", "add", report_file], timeout=10, check=True)

        # Build commit message
        overall = report["analysis"]["overall"]
        critical = len(report["analysis"]["critical_issues"])

        lines = [
            f"Auto: Trading system analysis - {overall['wins']}-{overall['losses']} record",
            f"",
            f"Win rate: {overall['win_rate']:.0%} | PnL: ${overall['total_pnl']:.2f}",
            f"Critical issues: {critical}",
            f"",
        ]

        # List free improvements
        improvements = report["recommendations"]["auto_implement"]
        if improvements:
            lines.append("Free improvements to implement:")
            for imp in improvements:
                lines.append(f"  • {imp['name']}")
            lines.append("")

        # Flag paid opportunities
        pending_apis = report["api_setup"]["pending"]
        if pending_apis:
            lines.extend([
                "Optional paid APIs available for review:",
                f"  {', '.join(pending_apis)}",
                "(Review .claude/analysis_report.json for details)",
                ""
            ])

        lines.append("Generated by automated trading analyzer")

        message = "\n".join(lines)

        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            logger.info("✅ Analysis committed to git")
            return True
        else:
            logger.warning(f"Git commit failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out")
        return False
    except Exception as e:
        logger.error(f"Failed to auto-commit: {e}")
        return False


def main():
    """Run the agent."""
    from config import PAPER_TRADING

    agent = ScheduledAnalysisAgent()

    try:
        report = agent.run()

        # Auto-commit if in paper trading mode and no critical issues
        critical = report["analysis"]["critical_issues"]
        if not critical and PAPER_TRADING:
            auto_commit_recommendations(report, paper_trading=PAPER_TRADING)

        # Check if user input is needed for APIs (only in interactive mode)
        if agent.should_prompt_for_api_setup():
            try:
                agent.prompt_for_api_setup()
            except (EOFError, KeyboardInterrupt):
                logger.info("Skipped API setup prompt (non-interactive/remote mode)")

        return report

    except Exception as e:
        logger.error(f"Agent failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    import sys
    report = main()

    # Exit with code 0 if no critical issues, 1 if critical issues found
    critical = report["analysis"]["critical_issues"]
    sys.exit(1 if critical else 0)
