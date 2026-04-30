---
name: Design Decisions — Rationale for Implemented Changes
description: Why specific design choices were made in the Polybot dashboard redesign
type: project
---

## Decisions Made in April 2026 Full Redesign

### Header stats as scrollable strip
Rather than wrapping or compressing stats on narrower viewports, the stats area is a horizontally-scrollable flex row with hidden scrollbar. Controls (backtest button, mode badge, clock) are in a separate `h-controls` flex container that never scrolls.

**Why:** The controls are critical on any screen size. Stats can scroll. Prevents the header from collapsing to unusable on 1200px screens.

### Scan progress bar instead of a countdown timer
A thin (2px) depleting green bar sits directly under the scanner panel title. It resets to 100% on each successful signal fetch and ticks down every 500ms. It turns amber when elapsed > 2× the refresh interval.

**Why:** A visual bar communicates "freshness" without adding cognitive load of a number. The amber colour shift is a non-intrusive warning without an alert.

### Mispricing visualiser bar in signal cards
A horizontal bar with two markers — grey for market price, green for Claude's fair value — shows the mispricing graphically instead of just two numbers.

**Why:** The gap between markers communicates magnitude of mispricing at a glance. Two raw numbers (e.g. "42¢ vs 58¢") require arithmetic; a visual gap is instant.

### Direction pill as first element in signal card header
The YES/NO direction pill appears before the edge badge and confidence badge, using clear green/red colouring.

**Why:** Direction is the primary decision axis. Users scanning 10+ signal cards need to see YES vs NO instantly.

### Win rate thresholds: <45% red, 45-55% amber, >55% green
Previous code used 50% as the green threshold. Changed to domain-appropriate 55%/45% thresholds.

**Why:** In prediction markets, random guessing yields ~50% directional accuracy. Only >55% represents meaningful outperformance. The backtest overlay tables were also updated with the same thresholds for consistency.

### `esc()` utility function for all external string interpolation
All strings from API responses (market questions, signal questions, wallet data) are now passed through an HTML-escaping function before innerHTML interpolation.

**Why:** Security baseline for any web interface that renders API data. Market question text could contain HTML special characters.

### Portfolio value stat added to header
Added `h-portfolio` stat showing total portfolio value (balance + open positions cost). Coloured relative to starting balance rather than relative to zero.

**Why:** Balance alone doesn't capture deployed capital. Portfolio value shows total book value and is a better "are we up or down" metric.

### W/L record stat added (separate from Win%)
Shows "3 / 1" format alongside the percentage, giving context to the percentage (55% from 2 trades vs 55% from 200 trades are very different).

**Why:** Win rate without trade count is misleading in early-stage trading bots. Both stats are needed.
