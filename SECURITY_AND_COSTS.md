# Security & Cost Tracking Setup

## 🔐 Dashboard Authentication

Your Polybot dashboard is now password-protected. You must set credentials before accessing it in production.

### Setup Instructions

#### Option 1: Heroku (Production)

Set environment variables on Heroku:

```bash
# Generate a strong random secret
openssl rand -hex 32

# Set environment variables on Heroku
heroku config:set -a polybot-trader \
  FLASK_SECRET_KEY="<output-from-above>" \
  DASHBOARD_USERNAME="admin" \
  DASHBOARD_PASSWORD="<choose-a-strong-password>"
```

Then restart the app:
```bash
heroku restart -a polybot-trader
```

#### Option 2: Local Development

Create a `.env` file in the project root:

```
FLASK_SECRET_KEY=your-secret-key-here
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=your-password-here
```

The app will load these automatically.

### Accessing the Dashboard

1. Go to https://polybot-trader-89bba5ed2d0b.herokuapp.com/
2. You'll be redirected to `/login`
3. Enter your username and password
4. Click "Sign In" to access the dashboard
5. Click "Logout" button (top-right corner) to log out

### Security Notes

- **Credentials are checked on every request** — if authentication fails, you're redirected to login
- **Session expires when browser closes** — refresh the page if you get logged out
- **All API endpoints are protected** — only authenticated users can access data
- **Password is checked in plaintext** — use environment variables, never commit credentials to git

## 💰 Cost Tracking Dashboard

A new **COSTS** panel shows the real-time cost of running Polybot.

### What's Tracked

The costs panel displays:

1. **Service Status** (enabled/disabled)
   - ✓ Anthropic API (always enabled for trading)
   - ✓ Brave Search (optional, web research) — **ENABLED**
   - ✓ Odds API (optional, sports data) — **ENABLED**
   - ✓ Heroku (always enabled for hosting)

2. **Weekly Costs** (last 7 days)
   - Anthropic: $0.XX (based on token usage)
   - Heroku: $1.62 (prorated from $7/month)

3. **Monthly Costs** (last 30 days)
   - Anthropic: $X.XX (estimated from trade activity)
   - Heroku: $7.00 (fixed monthly fee)

### Cost Calculation

- **Anthropic**: Estimated from trade count
  - Baseline: 1M tokens minimum per month (~$0.80)
  - Each trade implies ~5 scans × 20 markets × 1,000 tokens = 100k tokens
  - Running 200M tokens/month = $160/month maximum

- **Heroku**: Fixed cost
  - Eco Dyno: $7/month
  - Includes 550 dyno hours, auto-scaling

- **Optional Services**: Only charged if API keys are set
  - Brave Search: $1/1000 queries (not enabled)
  - Odds API: $4.99/1000 requests (not enabled)

### Reading the Costs Panel

```
SERVICES
● Anthropic API
● Brave Search API
● Odds API
● Heroku Dyno

WEEKLY
anthropic        $2.35
heroku           $1.62
─────────────────────
TOTAL            $3.97

MONTHLY
anthropic        $9.40
heroku           $7.00
─────────────────────
TOTAL            $16.40
```

- **●** = Service enabled (API key configured)
- **○** = Service disabled (no API key)
- **Amber color** = Weekly cost
- **Green color** = Monthly cost

### Cost Optimization Tips

1. **Keep Anthropic costs low**
   - Fewer trades = fewer API calls
   - Use a high MIN_EDGE_TO_TRADE to be selective
   - Use MIN_ENTRY_PROBABILITY = 0.03 (already set)

2. **Heroku costs**
   - Eco Dyno is the cheapest option ($7/month)
   - Auto-sleeps after 30 minutes of inactivity
   - Upgrade to Standard Dyno ($50/month) only if you need always-on

3. **Optional services enabled**
   - **BRAVE_SEARCH_API_KEY** — Web search for market context (cost: $1/1000 queries, typically <$1/month)
   - **ODDS_API_KEY** — Sports data enrichment (cost: $4.99/1000 requests, typically <$2/month with selective use)

### Monitoring Costs

The costs panel updates **every 60 seconds**. Check it weekly to:

1. Verify costs are in line with expectations
2. Identify cost spikes (indicate unusual API usage)
3. Adjust trading parameters if costs are too high
4. Plan monthly budget

### Projected Monthly Costs

With current settings:
- **Heroku**: $7.00/month (fixed)
- **Anthropic**: $10-20/month (depends on trade volume)
- **Brave Search**: <$1/month (selective use)
- **Odds API**: <$2/month (selective use for sports markets)

**Total: ~$18-30/month**

---

## 🔧 Environment Variables Reference

```bash
# Required for authentication
FLASK_SECRET_KEY=<random-string>           # Session encryption key
DASHBOARD_USERNAME=admin                    # Username for login
DASHBOARD_PASSWORD=<secure-password>        # Password for login

# Required for trading (already set)
ANTHROPIC_API_KEY=<your-key>               # Claude API access
DATABASE_URL=<database>                     # (optional) PostgreSQL connection

# Optional for enhanced features (cost: leave unset to disable)
BRAVE_SEARCH_API_KEY=<key>                 # Web search ($1/1000 queries) — ENABLED
ODDS_API_KEY=<key>                          # Sports odds ($4.99/1000 requests) — ENABLED
DISCORD_WEBHOOK_URL=<url>                   # Notifications (free) — ENABLED
```

## ⚠️ Important Notes

1. **Never commit credentials to git**
   - `.env` files are in `.gitignore`
   - Use `heroku config:set` for production

2. **Change default password immediately**
   - The default example uses `admin`/`password`
   - Replace with strong credentials

3. **Monitor API usage**
   - If costs spike unexpectedly, check the activity log
   - Unusual patterns might indicate bugs

4. **Session security**
   - Sessions are stored in browser cookies (encrypted with FLASK_SECRET_KEY)
   - Sessions last as long as the browser is open
   - Log out when done using the dashboard

---

For questions or issues, check the logs:
```bash
heroku logs --tail -a polybot-trader
```
