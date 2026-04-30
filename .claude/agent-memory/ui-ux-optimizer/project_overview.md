---
name: Polybot Dashboard — Tech Stack & Design Conventions
description: Core tech, colour palette, layout patterns, and component conventions for the Polybot terminal-style dashboard
type: project
---

## Tech Stack
- Flask backend (web/app.py) with SSE for live log streaming
- Single-page dashboard at web/templates/index.html — no JS framework, vanilla JS with fetch/setInterval polling
- Bootstrap 5 loaded but barely used — custom CSS dominates
- Chart.js 4.4.4 for the trade timeline scatter chart
- No static/ directory — all assets are inline in the HTML template

## Colour Palette (CSS custom properties)
- `--green: #00ff41` — primary brand, positive values, live status, YES direction
- `--green-d: #00b32c` — darker green for borders, progress bars, secondary green elements
- `--green-bg: #001a0a` — very dark green for badge backgrounds
- `--amber: #f0c040` — warnings, medium confidence, neutral/uncertain states, OPEN trades
- `--red: #ff3b3b` — losses, errors, NO direction, HALTED status
- `--blue: #3b9fff` — whale wallet tags only
- `--muted: #4a7a5a` — secondary text, labels, disabled states
- `--muted2: #2e5040` — very muted text (e.g. mispricing bar legend)
- `--border: #0d3320` — all panel/card borders
- `--bg: #000000` — page background
- `--surface: #050f07` — panel backgrounds
- `--surface2: #081208` — card backgrounds (slightly lighter than panel)

## Layout
- Two-row grid layout: `#main-grid` (top, ~52vh) + `#bottom-grid` (fills remaining flex space)
- Main grid: 3 columns — Signal Scanner (400px) | Elite Wallets (180px) | Live Markets (1fr)
- Bottom grid: 3 equal columns — Trade Feed | Trade Timeline | Activity Log
- Header: 38px sticky bar with logo + scrollable stats strip + fixed controls on the right
- Responsive breakpoints: 1100px (2-col) and 700px (1-col)

## Key API Endpoints
- GET /api/stats — portfolio stats, scan count, P&L, win rate
- GET /api/signals — all signals from last scan, sorted by abs(edge) desc
- GET /api/markets — raw market list
- GET /api/trades — trade history (last 100)
- GET /api/trade-timeline — trade data for chart
- GET /api/logs/stream — SSE stream of log entries
- GET /api/backtest/latest — most recent historical backtest run
- GET /api/backtest/tracker — forward prediction tracker stats

## Shared State Fields (from app.py shared_state)
- scan_count, last_scan, is_halted, balance, portfolio_value, model, edges_found, wallets_tracked

## Polling Intervals
- Stats: 5s | Signals: 8s | Markets: 8s | Trades: 10s | Chart: 20s

**Why:** Background knowledge for making consistent design decisions in future conversations.
**How to apply:** Always verify API field names against app.py before referencing them in template JS.
