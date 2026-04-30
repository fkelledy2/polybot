---
name: Recurring UX Anti-Patterns in Polybot Dashboard
description: UX issues found during the initial full audit of index.html — use to avoid reintroducing these problems
type: feedback
---

## Anti-Patterns Found (April 2026 audit)

### 1. Hardcoded initial values
The h-balance stat showed `$600` on page load before any API call completed. Always initialise dashboard stats to `—` (dash) and populate on first fetch.

**Why:** Stale/wrong data on load destroys trust in a trading interface.
**How to apply:** Use `—` as placeholder for all numeric stats in HTML; only JS populates real values.

### 2. No data freshness indicators
No panel showed when data was last updated. Users couldn't tell "no edges" from "data not loading."

**Why:** In real-time trading UIs, the age of data is as important as the data itself.
**How to apply:** Add per-panel "updated HH:MM:SS" timestamps that update on each refresh. Add a header "LAST SCAN" stat for the most critical freshness signal.

### 3. Win rate colouring threshold at 50%
Win rate was coloured green above 50%. In prediction markets, 51% is not a meaningful edge — the threshold for "good" performance should be ~55%.

**Why:** 50/50 is random chance; the UI should reflect domain-appropriate benchmarks.
**How to apply:** Use 55% as green threshold, 45% as red threshold, amber in between. Applied consistently across main dashboard and backtest overlay tables.

### 4. Signal cards buried direction
The trade direction (YES/NO) was only visible inside a small action button text ("TRADED YES"). For a trading signal interface, direction is the most critical piece of information.

**Why:** Users scan card headers first. Burying direction forces re-reading the whole card.
**How to apply:** Always render direction as the first element in a card header using a prominent coloured pill.

### 5. Single-line question truncation
Market questions were truncated to one line with ellipsis. These are often 50-80 characters and the truncation removed critical context.

**Why:** The question IS the market — truncating it defeats the purpose.
**How to apply:** Use 2-line clamp (`-webkit-line-clamp: 2`) instead of single-line overflow.

### 6. No scan progress indicator
No visual indication of when the next data refresh would occur. Users couldn't tell if the system was working.

**Why:** Feedback about system state is a core principle for live data dashboards.
**How to apply:** A depleting progress bar under the scanner panel header, reset on each successful fetch, turning amber when stale (> 2x interval).

### 7. Backtest close button was position:fixed
The close button was `position: fixed` against the viewport, which caused it to overlap scrolled overlay content on short/mobile viewports.

**Why:** Fixed positioning ignores scroll context; sticky within the overlay is correct.
**How to apply:** Use `position: sticky; top: 0; float: right` within the scrollable overlay container.

### 8. Trade feed missing P&L column
The trade feed showed ID, question, direction, size, status — but no P&L outcome for closed trades. P&L is the primary outcome metric.

**Why:** Users scan trade history to evaluate performance. Hiding P&L requires opening another view.
**How to apply:** Add a 6th column to .trade-row grid for P&L; show formatted value for closed, "open" in muted for open positions.

### 9. innerHTML with unescaped user data
Signal questions and market questions were interpolated directly into innerHTML template strings, creating potential XSS vectors if market question text ever contained `<`, `>`, `"`, or `&`.

**Why:** Any user-facing content derived from external APIs must be escaped.
**How to apply:** Use the `esc()` helper function for all externally-sourced strings in innerHTML contexts.
