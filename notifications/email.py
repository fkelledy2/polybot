# notifications/email.py
# ─────────────────────────────────────────────────────────────
# Send email alerts for critical system events
# ─────────────────────────────────────────────────────────────

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration from environment
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "fkelledy@gmail.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ENABLE_EMAIL_ALERTS = bool(SMTP_USERNAME and SMTP_PASSWORD)


def send_alert_email(subject: str, message: str, event_type: str = "ALERT") -> bool:
	"""
	Send an alert email for critical events.

	Args:
		subject: Email subject line
		message: Email body (markdown-friendly)
		event_type: Type of alert (CRASH, HALT, CREDIT_ERROR, RESTART, DEPLOY)

	Returns:
		True if email sent successfully, False otherwise
	"""
	if not ENABLE_EMAIL_ALERTS:
		logger.debug(f"Email alerts disabled (SMTP not configured)")
		return False

	try:
		# Create email
		email = MIMEMultipart("alternative")
		email["Subject"] = f"[{event_type}] {subject}"
		email["From"] = SMTP_USERNAME
		email["To"] = ALERT_EMAIL

		# HTML body with styling
		html_body = f"""
		<html>
		  <body style="font-family: monospace; background: #000; color: #00ff41; padding: 20px;">
			<div style="max-width: 600px; margin: 0 auto; border: 1px solid #0d3320; padding: 20px; background: #050f07;">
			  <h2 style="color: #ff3b3b; margin-top: 0;">🚨 {event_type} ALERT</h2>
			  <p><strong>{subject}</strong></p>
			  <hr style="border: 1px solid #0d3320;">
			  <pre style="background: #000; padding: 10px; border-radius: 3px; overflow-x: auto;">
{message}
			  </pre>
			  <hr style="border: 1px solid #0d3320;">
			  <p style="font-size: 11px; color: #4a7a5a;">
				Timestamp: {datetime.now().isoformat()}<br>
				Bot: Polybot Trader<br>
				<a href="https://polybot-trader-89bba5ed2d0b.herokuapp.com/" style="color: #00ff41;">View Dashboard</a>
			  </p>
			</div>
		  </body>
		</html>
		"""

		# Plain text fallback
		text_body = f"{subject}\n\n{message}\n\nTimestamp: {datetime.now().isoformat()}"

		email.attach(MIMEText(text_body, "plain"))
		email.attach(MIMEText(html_body, "html"))

		# Send via SMTP
		with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
			server.starttls()
			server.login(SMTP_USERNAME, SMTP_PASSWORD)
			server.send_message(email)

		logger.info(f"Alert email sent: {subject}")
		return True

	except Exception as e:
		logger.error(f"Failed to send alert email: {e}")
		return False


# ── Specific Alert Functions ──────────────────────────────────

def alert_api_credit_exhausted(service: str) -> bool:
	"""Alert when API credits are exhausted."""
	return send_alert_email(
		subject=f"{service} API Credits Exhausted",
		message=f"""
The {service} API credit balance has been exhausted.

IMMEDIATE ACTION REQUIRED:
1. Go to console.anthropic.com/settings/billing
2. Add credits or upgrade your plan
3. Restart the bot: heroku restart -a polybot-trader

The bot cannot function without API credits. Trading has been halted.
		""",
		event_type="CRITICAL_ERROR"
	)


def alert_system_crashed(error_message: str, traceback_text: str = "") -> bool:
	"""Alert when the system crashes."""
	message = f"""
The Polybot trading system has crashed unexpectedly.

ERROR: {error_message}
"""
	if traceback_text:
		message += f"\nTRACEBACK:\n{traceback_text}"

	message += f"""

NEXT STEPS:
1. Check the full logs: heroku logs -a polybot-trader -n 200
2. Investigate the error above
3. Fix the issue and redeploy: git push heroku main
4. Verify: heroku logs -a polybot-trader --tail
	"""

	return send_alert_email(
		subject="System Crash Detected",
		message=message,
		event_type="CRASH"
	)


def alert_system_halted(reason: str) -> bool:
	"""Alert when the system is manually halted."""
	return send_alert_email(
		subject="Trading System Halted",
		message=f"""
The Polybot trading system has been halted.

REASON: {reason}

WHEN READY TO RESUME:
heroku restart -a polybot-trader
		""",
		event_type="HALT"
	)


def alert_app_restarted(reason: str = "Heroku dyno restart") -> bool:
	"""Alert when the app restarts."""
	return send_alert_email(
		subject="App Restarted",
		message=f"""
The Polybot app has restarted.

REASON: {reason}

STATUS:
- Check bot health: ./tail.sh
- View dashboard: https://polybot-trader-89bba5ed2d0b.herokuapp.com/
- Monitor signals: ./tail.sh signal
		""",
		event_type="RESTART"
	)


def alert_deployment(commit_sha: str, commit_message: str = "", deployment_url: str = "") -> bool:
	"""Alert when a new deployment is pushed."""
	commit_msg_display = commit_message[:100] if commit_message else "(no message)"

	return send_alert_email(
		subject="New Deployment Detected",
		message=f"""
A new version of Polybot has been deployed to production.

COMMIT: {commit_sha[:12]}
MESSAGE: {commit_msg_display}

STATUS:
- Heroku Release: https://dashboard.heroku.com/apps/polybot-trader/releases
- View logs: heroku logs -a polybot-trader --tail
- View dashboard: https://polybot-trader-89bba5ed2d0b.herokuapp.com/
		""",
		event_type="DEPLOY"
	)


def alert_critical_error(title: str, details: str) -> bool:
	"""Alert for any critical error."""
	return send_alert_email(
		subject=title,
		message=details,
		event_type="ERROR"
	)
