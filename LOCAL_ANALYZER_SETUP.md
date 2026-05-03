# Local Trading Analyzer Setup

Run the trading system analyzer locally on a schedule (in addition to the remote agent).

## Quick Start

**One-time run:**
```bash
bash run_analyzer.sh
```

## Setup: Daily at 9am (Local Time)

Choose one method below based on your OS:

---

## Method 1: macOS LaunchAgent (Recommended)

Creates a persistent daemon that runs at 9am every day.

### Step 1: Create the LaunchAgent plist

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.polybot.analyzer.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.polybot.analyzer</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/ferguskelledy/polybot/run_analyzer.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/ferguskelledy/polybot/logs/analyzer.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/ferguskelledy/polybot/logs/analyzer_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF
```

### Step 2: Create logs directory

```bash
mkdir -p ~/polybot/logs
```

### Step 3: Load the LaunchAgent

```bash
launchctl load ~/Library/LaunchAgents/com.polybot.analyzer.plist
```

### Step 4: Verify it's running

```bash
launchctl list | grep polybot
```

### To disable:
```bash
launchctl unload ~/Library/LaunchAgents/com.polybot.analyzer.plist
```

### To restart:
```bash
launchctl unload ~/Library/LaunchAgents/com.polybot.analyzer.plist
launchctl load ~/Library/LaunchAgents/com.polybot.analyzer.plist
```

---

## Method 2: crontab (Linux/macOS)

Standard Unix cron job.

### Step 1: Create log directory

```bash
mkdir -p ~/polybot/logs
```

### Step 2: Edit crontab

```bash
crontab -e
```

### Step 3: Add this line

For **9am your local time**:
```cron
0 9 * * * cd /Users/ferguskelledy/polybot && bash run_analyzer.sh >> logs/analyzer.log 2>&1
```

### Step 4: Save and exit

Press Ctrl+X (or Cmd+X), then Y, then Enter

### Step 5: Verify

```bash
crontab -l
```

You should see your new entry.

---

## Method 3: Manual

Run whenever you want:

```bash
cd ~/polybot
bash run_analyzer.sh
```

---

## Output & Logs

Both methods create logs at:
- **Standard output:** `logs/analyzer.log`
- **Errors:** `logs/analyzer_error.log`

View the latest results:
```bash
tail -50 logs/analyzer.log
```

---

## Troubleshooting

**"Permission denied" error:**
```bash
chmod +x run_analyzer.sh
```

**Cron job not running:**
- Check system cron logs: `log stream --predicate 'eventMessage contains[cd] "polybot"'`
- Verify crontab entry: `crontab -l`
- Test manually: `bash ~/polybot/run_analyzer.sh`

**LaunchAgent not loading:**
```bash
launchctl load -w ~/Library/LaunchAgents/com.polybot.analyzer.plist
```

**Check if running:**
```bash
ps aux | grep analyzer
```

---

## Dual Mode: Local + Remote

You now have **two** instances:

| Mode | Schedule | Trigger | Output |
|------|----------|---------|--------|
| **Remote** | 9am UTC daily | https://claude.ai/code/routines/trig_01Y8eTMtiEWnG2FdztqyfFho | Auto-commits to GitHub |
| **Local** | 9am your timezone | LaunchAgent or cron | Logs to disk + auto-commits |

Both will:
- Analyze trades.db
- Generate .claude/analysis_report.json
- Auto-commit findings (if paper trading + no critical issues)
- Flag paid API opportunities

**Benefit:** Redundancy + local logs you can review immediately

---

## Tips

- Set different times if you prefer (e.g., local at 8am, remote at 9am UTC) to avoid duplicate commits
- Monitor `logs/analyzer.log` daily for key findings
- Remote agent runs regardless of local machine state (cloud-based)
- Local version keeps audit trail on your machine

