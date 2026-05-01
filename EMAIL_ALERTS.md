# Email Alert Setup

Polybot can send email alerts for critical events like API credit exhaustion, app restarts, and deployments.

## Quick Setup (Gmail)

### 1. Create an App Password (Gmail)

1. Go to: https://myaccount.google.com/apppasswords
2. Select "Mail" and "Windows Computer" (or "Other")
3. Generate a password (16 characters)
4. Copy the password

### 2. Set Environment Variables on Heroku

```bash
heroku config:set -a polybot-trader \
  ALERT_EMAIL="fkelledy@gmail.com" \
  SMTP_USERNAME="your-gmail@gmail.com" \
  SMTP_PASSWORD="xxxx xxxx xxxx xxxx" \
  SMTP_SERVER="smtp.gmail.com" \
  SMTP_PORT="587"
```

### 3. Restart the App

```bash
heroku restart -a polybot-trader
```

## Alert Types

### 🚨 API Credit Exhaustion
**When**: Anthropic API returns "credit balance too low" error
**What**: Email notification that credits are exhausted
**Action**: Add credits at console.anthropic.com/settings/billing

**Example**:
```
Subject: [CRITICAL_ERROR] Anthropic API Credits Exhausted

The Anthropic API credit balance has been exhausted.

IMMEDIATE ACTION REQUIRED:
1. Go to console.anthropic.com/settings/billing
2. Add credits or upgrade your plan
3. Restart the bot: heroku restart -a polybot-trader
```

### 🔄 App Restart
**When**: Heroku dyno restarts (after deploy, crash recovery, etc.)
**What**: Email notification that app has restarted
**Action**: Check logs to ensure everything is working

**Example**:
```
Subject: [RESTART] App Restarted

The Polybot app has restarted.

REASON: Heroku dyno restart
```

### 📦 Deployment
**When**: New code is deployed via `git push heroku main`
**What**: Email notification with commit info
**Action**: Verify deployment is working

**Example**:
```
Subject: [DEPLOY] New Deployment Detected

A new version of Polybot has been deployed to production.

COMMIT: a1b2c3d4e5f6
MESSAGE: Add email alerting for critical events
```

### ⚠️ System Crash
**When**: Unhandled exception crashes the bot
**What**: Email with error details and traceback
**Action**: Investigate logs and fix the issue

## Environment Variables

| Variable | Required | Example |
|----------|----------|---------|
| `ALERT_EMAIL` | No | `fkelledy@gmail.com` |
| `SMTP_USERNAME` | Yes* | `your-gmail@gmail.com` |
| `SMTP_PASSWORD` | Yes* | `xxxx xxxx xxxx xxxx` |
| `SMTP_SERVER` | No | `smtp.gmail.com` |
| `SMTP_PORT` | No | `587` |

*Required to enable email alerts. If not set, alerts are silently disabled.

## Verify Setup

Check if alerts are configured:

```bash
heroku config -a polybot-trader | grep SMTP
```

Should output:
```
SMTP_PASSWORD: xxxx xxxx xxxx xxxx
SMTP_SERVER:   smtp.gmail.com
SMTP_USERNAME: your-gmail@gmail.com
```

## Testing Alerts

To test the alert system:

```bash
heroku run -a polybot-trader python3 << 'EOF'
from notifications import send_alert_email

result = send_alert_email(
    subject="Test Alert",
    message="This is a test alert from Polybot.\n\nIf you receive this email, your alert system is working!",
    event_type="TEST"
)

print(f"Alert sent: {result}")
EOF
```

## Supported Email Providers

### Gmail (Recommended)
- Server: `smtp.gmail.com`
- Port: `587`
- Password: App password (see setup above)

### Outlook/Office365
- Server: `smtp.office365.com`
- Port: `587`
- Password: Your email password

### SendGrid
- Server: `smtp.sendgrid.net`
- Port: `587`
- Username: `apikey`
- Password: SendGrid API key

### Custom SMTP Server
Set `SMTP_SERVER` and `SMTP_PORT` to your provider's values.

## Troubleshooting

### "Email alerts disabled"
**Cause**: `SMTP_USERNAME` and `SMTP_PASSWORD` not set
**Fix**: Set both environment variables and restart

### "Failed to send alert email"
**Cause**: Wrong credentials, server blocked, or network issue
**Fix**:
```bash
# Test SMTP connection
heroku run -a polybot-trader python3 << 'EOF'
import smtplib
try:
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login("your-email@gmail.com", "your-app-password")
    print("✓ Connection successful")
    server.quit()
except Exception as e:
    print(f"✗ Error: {e}")
EOF
```

### Gmail says "Less secure app blocked"
**Fix**: Use an App Password instead of your main password (see setup above)

## Email Format

All emails include:
- **Subject**: Alert type and title
- **From**: SMTP_USERNAME
- **To**: ALERT_EMAIL
- **Body**: Styled HTML with details, timestamp, and links to dashboard/logs
- **Links**: Direct links to Heroku dashboard and logs for quick access

## Disable Alerts

To disable email alerts without removing credentials:

Set a dummy email address:
```bash
heroku config:set -a polybot-trader ALERT_EMAIL=""
```

Or unset SMTP credentials:
```bash
heroku config:unset -a polybot-trader SMTP_USERNAME SMTP_PASSWORD
```

## Cost

- **Gmail**: Free (with free tier limits)
- **SendGrid**: Free tier = 100 emails/day
- **Outlook**: Free (included with Microsoft account)

For a trading bot with occasional alerts, free tiers are sufficient.

---

Once configured, you'll receive alerts automatically for critical events! 📧
