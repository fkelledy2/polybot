# Polybot Product Roadmap

## Vision
A self-improving prediction market trading system that compounds edge through three feedback loops:
1. **Signal quality** — Claude analysis improves with calibration data over time
2. **Data richness** — cross-market consensus and live enrichment narrow mispricing
3. **Capital efficiency** — risk management tightens as historical win-rate data accumulates

The system gets measurably smarter the longer it runs.

---

## Baseline (v0.1 — shipped)
- 60-second scan loop, basic Claude analysis, RSS enrichment
- Paper trading with SQLite persistence
- Flask dashboard at :8080
- Basic risk gates (daily loss limit, max positions)
- Deployed on Heroku Basic dyno ($7/month)

---

## Sprint 1 — Signal Quality Foundation
**Goal:** Maximum ROI improvements, no new external dependencies.

| ID | Feature | File(s) | Impact |
|----|---------|---------|--------|
| S1-1 | **Prompt caching** — cache static system prompt; ~80% input token cost reduction | `signals/claude_signal.py` | Cost |
| S1-2 | **Tool use for structured output** — replace fragile JSON-in-text parsing with tool definitions | `signals/claude_signal.py` | Reliability |
| S1-3 | **Resolution criteria in prompts** — pull exact resolution criteria from Polymarket API and inject | `data/polymarket.py`, `signals/claude_signal.py` | Signal quality |
| S1-4 | **Metaculus cross-reference** — query public Metaculus API; Polymarket vs expert consensus divergence = signal | `data/enrichment/metaculus.py`, `dispatcher.py` | Alpha |
| S1-5 | **Price momentum tracking** — store per-market price history; 24h velocity as pre-filter and signal | `data/polymarket.py`, `main.py`, DB schema | Alpha |

---

## Sprint 2 — Enrichment Upgrade
**Goal:** Replace generic RSS with targeted real-world data per market.

| ID | Feature | File(s) | Impact |
|----|---------|---------|--------|
| S2-1 | **Web search per market** — Brave Search / Serper API; targeted search for each question | `data/enrichment/search.py`, `dispatcher.py` | Signal quality |
| S2-2 | **Extended thinking for high-edge markets** — enable Claude extended thinking when edge >20% | `signals/claude_signal.py` | Signal quality |
| S2-3 | **Heroku Postgres migration** — persistent trade + calibration data across deploys | `execution/paper_trader.py`, `backtest/tracker.py`, `web/app.py` | Infrastructure |
| S2-4 | **Bookmaker lines for sports** — Odds API consensus is more accurate than Claude for sports | `data/enrichment/sports.py` | Alpha (sports) |

---

## Sprint 3 — Calibration Feedback Loop
**Goal:** Close the self-improvement loop. The system gets smarter with every resolved market.

| ID | Feature | File(s) | Impact |
|----|---------|---------|--------|
| S3-1 | **Per-category calibration correction** — use `predictions` table to compute Claude's bias per category; apply correction to edge | `backtest/calibration.py`, `signals/claude_signal.py` | Compounding moat |
| S3-2 | **New market detection** — flag markets listed <48h with volume >$5k; structural alpha at formation | `data/polymarket.py`, `main.py` | Alpha |
| S3-3 | **Related market pair detection** — detect complementary markets where probabilities don't sum to 1 | `signals/arbitrage.py` | Model-free alpha |
| S3-4 | **Notifications** — Discord/Telegram alerts for high-edge signals, trade open/close, daily P&L, halt | `notifications/discord.py` | Operational |

---

## Sprint 4 — Architecture Upgrade
**Goal:** Move from polling to real-time; add portfolio-level intelligence.

| ID | Feature | File(s) | Impact |
|----|---------|---------|--------|
| S4-1 | **CLOB WebSocket feed** — replace 60s polling with real-time orderbook events | `data/clob_stream.py`, `main.py` | Latency |
| S4-2 | **Correlation-aware portfolio** — topic clustering; cap total exposure per cluster | `risk/manager.py`, `signals/clustering.py` | Risk |
| S4-3 | **Dynamic stop-loss** — close positions if market moves >2× entry edge against us | `execution/resolver.py`, `main.py` | Risk |
| S4-4 | **Batch API for re-analysis** — use Anthropic Messages Batch API for non-urgent open-position review; 50% cost reduction | `signals/claude_signal.py` | Cost |

---

## Claude API Skill Usage

All Claude API integrations should follow these standards:

- **Prompt caching** on all static content (system prompt, category guidance, tool definitions)
- **Tool use** instead of JSON-in-text for all structured outputs
- **Extended thinking** for high-conviction analysis (edge > 20%, `budget_tokens: 5000`)
- **Batch API** for non-time-sensitive bulk analysis
- **Model routing**: Haiku for routine scans, Sonnet for high-edge confirmation, Opus reserved for extended thinking on exceptional signals

---

## Cost Model (target)

| Component | Current | After S1 | After S2 |
|-----------|---------|----------|----------|
| Claude input tokens (per scan) | ~3,000 | ~600 (cached) | ~600 |
| Claude output tokens (per scan) | ~500 | ~500 | ~500 |
| Est. daily cost (24 scans) | ~$0.08 | ~$0.02 | ~$0.02 |
| Heroku | $7/mo | $7/mo | $12/mo (+Postgres) |

---

## Success Metrics

| Metric | Baseline | Sprint 1 target | Sprint 3 target |
|--------|---------|----------------|----------------|
| Directional accuracy | unknown | tracking | >55% |
| Traded win rate | unknown | tracking | >58% |
| Edge threshold calibration | flat 12% | flat 12% | per-category |
| Markets with enrichment | ~40% | ~60% (+Metaculus) | ~85% (+search) |
| API cost per day | $0.08 | $0.02 | $0.02 |
