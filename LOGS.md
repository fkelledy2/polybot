# Viewing Application Logs

Polybot provides two scripts for tailing Heroku logs in real-time.

## Quick Start

### Option 1: Full-Featured (Recommended)

```bash
./tail.sh              # Stream all logs
./tail.sh error        # Stream only errors
./tail.sh signal       # Stream only signal logs
./tail.sh backtest     # Stream only backtest logs
./tail.sh <keyword>    # Stream logs containing keyword
./tail.sh -h           # Show help
```

### Option 2: Minimal/Quick

```bash
./logs.sh              # Stream all logs
./logs.sh error        # Stream logs with "error"
./logs.sh signal       # Stream logs with "signal"
```

---

## Detailed Usage

### tail.sh (Full-Featured)

The `tail.sh` script is the main tool for log inspection. It includes:

- **Color output** for better readability
- **Built-in filters** for common keywords
- **Error checking** (validates heroku CLI)
- **Help system** (run `./tail.sh -h`)

#### Examples

```bash
# Stream everything (all logs in real-time)
./tail.sh

# Watch for errors only
./tail.sh error

# Watch for warnings
./tail.sh warning

# Watch signal generation
./tail.sh signal

# Watch backtest runs
./tail.sh backtest

# Watch Claude API interactions
./tail.sh claude

# Custom filter: show only trade execution
./tail.sh trade

# Custom filter: show API responses
./tail.sh "200 OK"

# Complex filter: show failed API calls
./tail.sh "error\|ERROR\|failed"
```

#### Log Output Format

Each log line shows:
```
TIMESTAMP  [SOURCE] MESSAGE

2026-05-01T10:45:23.456Z app[web.1]: [2026-05-01 10:45:23] [INFO] signals.claude_signal: Signal: ↑YES | market=52% | claude=67% | edge=+15% | trade=true
```

Components:
- **TIMESTAMP**: When the log was generated
- **[SOURCE]**: Process that generated the log (web.1, router, etc.)
- **MESSAGE**: The actual log content with level and logger

### logs.sh (Quick/Minimal)

The `logs.sh` is a minimal wrapper if you prefer simplicity:

```bash
# Stream all logs
./logs.sh

# Filter for errors
./logs.sh error

# Filter with regex
./logs.sh "Signal|Trade"
```

---

## Common Log Patterns

### Monitor Trading Activity

```bash
# Watch trades being executed
./tail.sh "Trade\|trade"

# Watch signals being generated
./tail.sh "Signal"

# Watch win/loss outcomes
./tail.sh "won\|lost"
```

### Debug Issues

```bash
# Watch for any errors
./tail.sh error

# Watch API errors
./tail.sh "API error\|HTTPError"

# Watch Claude API usage
./tail.sh claude

# Watch database issues
./tail.sh "database\|db\|query"
```

### Monitor System Health

```bash
# Watch scanning activity
./tail.sh "scan"

# Watch market fetches
./tail.sh "fetch\|markets"

# Watch health checks
./tail.sh "health\|ready"
```

---

## How to Read Logs

### Log Levels

```
[DEBUG]   - Detailed diagnostic info (not usually important)
[INFO]    - General information about what the bot is doing
[WARNING] - Something unexpected happened, but it recovered
[ERROR]   - Something went wrong, may impact trading
[CRITICAL] - System failure, immediate attention needed
```

### Example Log Session

```bash
$ ./tail.sh

Streaming logs from polybot-trader...
Press Ctrl+C to stop

2026-05-01T10:45:20.123Z app[web.1]: [INFO] web.app: GET /api/stats HTTP 200
2026-05-01T10:45:22.456Z app[web.1]: [INFO] main: ══ SCAN #47 started
2026-05-01T10:45:23.789Z app[web.1]: [INFO] signals.claude_signal: Analysing 15 markets with Claude...
2026-05-01T10:45:25.234Z app[web.1]: [INFO] signals.claude_signal: Signal: ↑YES | market=52% | claude=67% | edge=+15% | trade=true
2026-05-01T10:45:25.567Z app[web.1]: [INFO] main: Found 2 tradeable signals
2026-05-01T10:45:26.890Z app[web.1]: [INFO] trades: Executing trade #5: Market ABC, YES direction
2026-05-01T10:45:27.123Z app[web.1]: [INFO] trades: Trade #5 executed: size=$50, entry=52%
2026-05-01T10:45:30.456Z app[web.1]: [INFO] main: ══ SCAN #47 complete (10.2 sec)
```

What's happening:
1. Scan starts (every 60 seconds)
2. Markets are fetched and analyzed
3. Claude generates probability estimates
4. Signals that meet criteria are identified
5. Qualifying trades are executed
6. Scan completes

---

## Useful Log Queries

### Daily Activity Summary

```bash
# Count how many scans ran today
./tail.sh "SCAN" | grep $(date +%Y-%m-%d) | wc -l

# Count trades executed today
./tail.sh "executed" | grep $(date +%Y-%m-%d) | wc -l

# Show all errors from today
./tail.sh error | grep $(date +%Y-%m-%d)
```

### Real-Time Monitoring

```bash
# Watch in one terminal
./tail.sh error

# In another terminal, grep the logs for specific issues
heroku logs --tail -a polybot-trader | grep -i "timeout\|connection"
```

---

## Troubleshooting

### "heroku CLI not found"

Install Heroku CLI:
```bash
# macOS
brew install heroku

# Or download from:
# https://devcenter.heroku.com/articles/heroku-cli
```

### "App 'polybot-trader' not found"

Make sure you're authenticated:
```bash
heroku auth:login

# Or check your apps:
heroku apps
```

### Logs stop updating

The app may have crashed. Check the app status:
```bash
heroku ps -a polybot-trader
heroku logs -a polybot-trader --lines 100  # Last 100 lines
```

### Too much output/noise

Use filters to narrow down:
```bash
./tail.sh error              # Only errors
./tail.sh signal             # Only signals
./tail.sh "custom pattern"   # Your own pattern
```

---

## Advanced Usage

### Filter for specific time period

```bash
# Last 1000 lines
heroku logs -a polybot-trader -n 1000

# Within last 5 minutes
heroku logs -a polybot-trader --since 5m
```

### Export logs to file

```bash
# Save last 1000 lines to file
heroku logs -a polybot-trader -n 1000 > logs.txt

# Stream logs to file
./tail.sh > logs.txt &

# Stream errors to file
./tail.sh error > errors.txt &
```

### Watch multiple patterns

```bash
# Watch for trades OR errors
./tail.sh "trade\|error"

# Watch for any market data
./tail.sh "market\|signal\|edge"
```

---

## Log Retention

- **Heroku free tier**: Logs are kept for 1 hour
- **Heroku paid tiers**: Logs are kept for ~10 days
- **Note**: Old logs are automatically discarded

To keep logs long-term, use a logging service:
- Papertrail (recommended, free tier available)
- Splunk
- Datadog
- AWS CloudWatch

---

## Quick Reference

| Command | Purpose |
|---------|---------|
| `./tail.sh` | Stream all logs |
| `./tail.sh error` | Stream errors only |
| `./tail.sh signal` | Stream signal logs |
| `./tail.sh backtest` | Stream backtest logs |
| `./tail.sh -h` | Show help |
| `./logs.sh` | Minimal log streaming |
| `heroku logs -a polybot-trader -n 100` | Last 100 lines |
| `heroku logs -a polybot-trader --since 10m` | Last 10 minutes |

---

For more info, see the script headers:
```bash
head -20 tail.sh
head -20 logs.sh
```
