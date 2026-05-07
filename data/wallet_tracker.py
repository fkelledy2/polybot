# data/wallet_tracker.py
# ─────────────────────────────────────────────────────────────
# Finds and tracks "elite" wallets on Polymarket.
# Discovery: Next.js _next/data endpoint (no browser required).
#            leaderboard-api.polymarket.com is dead.
# Positions: data-api.polymarket.com/positions (works).
# ─────────────────────────────────────────────────────────────

import json
import re
import requests
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_LEADERBOARD_URL = "https://polymarket.com/leaderboard/overall/monthly/profit"
_NEXT_DATA_TMPL  = "https://polymarket.com/_next/data/{build_id}/en/leaderboard/overall/monthly/profit.json"


@dataclass
class WalletProfile:
    address: str
    name: str = ""
    rank: int = 0               # leaderboard rank (1 = best)
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl_usd: float = 0.0
    volume_usd: float = 0.0     # total 30-day trading volume
    current_positions: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def is_elite(self) -> bool:
        from config import MIN_TRADES_FOR_TRUST, MIN_WIN_RATE
        return (
            self.total_trades >= MIN_TRADES_FOR_TRUST
            and self.win_rate >= MIN_WIN_RATE
        )

    def __repr__(self):
        return (
            f"Wallet({self.address[:8]}... | "
            f"rank={self.rank} | "
            f"pnl=${self.total_pnl_usd:+,.2f})"
        )


@dataclass
class WalletConsensus:
    """Aggregated view of all elite traders holding a single market."""
    condition_id: str
    question: str
    winning_direction: str      # "YES" or "NO"
    consensus_score: float      # winning_weight / total_weight, range 0.5–1.0
    trader_count: int           # traders holding this market (any direction)
    yes_count: int
    no_count: int
    raw_usd: float              # simple USD sum for winning direction
    weighted_usd: float         # rank-weighted USD for winning direction
    avg_entry_price: Optional[float] = None
    avg_current_price: Optional[float] = None


@dataclass
class EliteSignalBundle:
    """Result of a full consensus pass over all elite wallets."""
    consensus: dict             # condition_id (hex str) -> WalletConsensus
    all_condition_ids: set      # every conditionId seen, including alpha-decayed positions


def _compute_alpha_remaining(pos: dict) -> Optional[float]:
    """
    How much of the trade's potential upside is still left to capture.
    Returns None when data is insufficient (skip filtering, not an error).
    A value < ALPHA_DECAY_THRESHOLD means the entry opportunity has passed.
    """
    size = float(pos.get("size", 0) or 0)
    if size <= 0:
        return None
    initial_value = float(pos.get("initialValue", 0) or 0)
    current_value = float(pos.get("currentValue", 0) or 0)
    if initial_value <= 0:
        return None

    entry_price   = initial_value / size
    current_price = current_value / size

    if not (0 < entry_price < 1):
        return None

    outcome = pos.get("outcome", "Yes").upper()
    if outcome == "YES":
        denom = 1.0 - entry_price
        return (1.0 - current_price) / denom if denom > 0 else None
    else:
        return current_price / entry_price if entry_price > 0 else None


class WalletTracker:
    """
    Discovers and tracks high-performing wallets on Polymarket.

    Discovery: fetches the leaderboard page HTML to extract the Next.js
    build ID, then calls the _next/data endpoint directly — returns the
    full SSR leaderboard JSON with wallet addresses and 30-day PnL.
    No headless browser required.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self.tracked_wallets: dict[str, WalletProfile] = {}
        self.elite_wallets: list[WalletProfile] = []

    def _get_build_id(self) -> Optional[str]:
        """Extract the Next.js build ID from the leaderboard page HTML."""
        try:
            resp = self.session.get(_LEADERBOARD_URL, timeout=15)
            resp.raise_for_status()
            match = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
            if not match:
                match = re.search(r'/(build-[A-Za-z0-9_-]+)/', resp.text)
            return match.group(1) if match else None
        except requests.RequestException as e:
            logger.warning(f"Could not fetch leaderboard page for build ID: {e}")
            return None

    def fetch_top_wallets(self, limit: int = 100) -> list[dict]:
        """
        Fetch top traders from Polymarket's leaderboard via the Next.js
        _next/data endpoint (SSR JSON, no browser needed).
        """
        build_id = self._get_build_id()
        if not build_id:
            logger.warning("Could not determine Next.js build ID — wallet tracking disabled")
            return []

        url = _NEXT_DATA_TMPL.format(build_id=build_id)
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            queries = (
                data.get("pageProps", {})
                .get("dehydratedState", {})
                .get("queries", [])
            )
            profit_query = next(
                (
                    q for q in queries
                    if len(q.get("queryKey", [])) >= 2
                    and q["queryKey"][1] == "profit"
                ),
                None,
            )
            if not profit_query:
                logger.warning("No profit leaderboard query in _next/data response")
                return []

            traders = profit_query["state"]["data"][:limit]
            logger.info(f"Fetched {len(traders)} traders from leaderboard")
            return traders

        except Exception as e:
            logger.warning(f"Leaderboard _next/data fetch failed: {e}")
            return []

    def fetch_wallet_positions(self, address: str) -> list[dict]:
        """Fetch open positions for a wallet via data-api.polymarket.com."""
        try:
            response = self.session.get(
                "https://data-api.polymarket.com/positions",
                params={"user": address, "sizeThreshold": "10", "limit": 50},
                timeout=10,
            )
            response.raise_for_status()
            positions = response.json()
            logger.debug(f"Wallet {address[:8]}... has {len(positions)} open positions")
            return positions
        except requests.RequestException as e:
            logger.warning(f"Could not fetch positions for {address[:8]}...: {e}")
            return []

    def build_elite_list(self, top_n: int = 20) -> list[WalletProfile]:
        """Build the elite wallet list from the top-N leaderboard traders."""
        logger.info("Building elite wallet list from leaderboard...")

        traders = self.fetch_top_wallets(limit=top_n * 3)
        if not traders:
            logger.info("No traders fetched — running without wallet signals")
            return []

        profiles = []
        for t in traders:
            addr = t.get("proxyWallet", "")
            if not addr:
                continue
            pnl = float(t.get("pnl", 0) or 0)
            if pnl <= 0:
                continue

            raw_name = t.get("name") or t.get("pseudonym") or ""
            if raw_name.startswith("0x") and "-" in raw_name:
                raw_name = ""
            name = raw_name or f"{addr[:6]}...{addr[-4:]}"

            profile = WalletProfile(
                address=addr,
                name=name,
                rank=t.get("rank", len(profiles) + 1),
                total_trades=100,
                winning_trades=60,
                total_pnl_usd=pnl,
                volume_usd=float(t.get("volume", 0) or 0),
            )
            profiles.append(profile)
            self.tracked_wallets[addr] = profile
            logger.info(
                f"  Elite (rank {profile.rank}): {name} "
                f"{addr[:10]}... PnL=${pnl:,.0f}"
            )

            if len(profiles) >= top_n:
                break

        self.elite_wallets = profiles
        logger.info(f"Elite wallet list ready: {len(self.elite_wallets)} wallets")
        return self.elite_wallets

    def get_elite_consensus(self) -> EliteSignalBundle:
        """
        Fetch all elite wallet positions, compute rank-weighted consensus per
        market, and flag alpha-decayed positions.

        Returns an EliteSignalBundle with:
          - consensus: dict[condition_id -> WalletConsensus] (non-decayed only)
          - all_condition_ids: every conditionId seen (for market discovery)
        """
        from config import ALPHA_DECAY_THRESHOLD

        n = len(self.elite_wallets)
        if n == 0:
            return EliteSignalBundle(consensus={}, all_condition_ids=set())

        # Per-market accumulators: condition_id -> aggregation dict
        market_acc: dict[str, dict] = {}
        all_condition_ids: set[str] = set()

        for wallet in self.elite_wallets:
            rank_weight = max(1, n + 1 - wallet.rank)
            positions = self.fetch_wallet_positions(wallet.address)

            for pos in positions:
                cid = pos.get("conditionId") or pos.get("condition_id") or ""
                if not cid:
                    continue

                all_condition_ids.add(cid)

                # Alpha decay check — exclude from consensus but keep for discovery
                alpha = _compute_alpha_remaining(pos)
                if alpha is not None and alpha < ALPHA_DECAY_THRESHOLD:
                    logger.debug(
                        f"Alpha-decayed position skipped: {cid[:12]}… "
                        f"alpha={alpha:.2f} wallet={wallet.name}"
                    )
                    continue

                outcome = pos.get("outcome", "Yes").upper()
                if outcome not in ("YES", "NO"):
                    outcome = "YES"

                current_value = float(pos.get("currentValue", 0) or 0)
                size          = float(pos.get("size", 0) or 0)
                initial_value = float(pos.get("initialValue", 0) or 0)

                entry_price   = (initial_value / size) if size > 0 and initial_value > 0 else None
                current_price = (current_value  / size) if size > 0 and current_value > 0 else None

                if cid not in market_acc:
                    market_acc[cid] = {
                        "question":      pos.get("title", "Unknown"),
                        "yes_weight":    0.0,
                        "no_weight":     0.0,
                        "yes_usd":       0.0,
                        "no_usd":        0.0,
                        "yes_count":     0,
                        "no_count":      0,
                        "entry_prices":  [],
                        "current_prices":[],
                    }

                acc = market_acc[cid]
                weighted = rank_weight * current_value

                if outcome == "YES":
                    acc["yes_weight"] += weighted
                    acc["yes_usd"]    += current_value
                    acc["yes_count"]  += 1
                else:
                    acc["no_weight"] += weighted
                    acc["no_usd"]    += current_value
                    acc["no_count"]  += 1

                if entry_price is not None:
                    acc["entry_prices"].append(entry_price)
                if current_price is not None:
                    acc["current_prices"].append(current_price)

        # Build WalletConsensus objects
        consensus: dict[str, WalletConsensus] = {}
        for cid, acc in market_acc.items():
            total_weight = acc["yes_weight"] + acc["no_weight"]
            if total_weight == 0:
                continue

            if acc["yes_weight"] >= acc["no_weight"]:
                direction   = "YES"
                win_weight  = acc["yes_weight"]
                raw_usd     = acc["yes_usd"]
            else:
                direction   = "NO"
                win_weight  = acc["no_weight"]
                raw_usd     = acc["no_usd"]

            ep = acc["entry_prices"]
            cp = acc["current_prices"]

            consensus[cid] = WalletConsensus(
                condition_id      = cid,
                question          = acc["question"],
                winning_direction = direction,
                consensus_score   = round(win_weight / total_weight, 3),
                trader_count      = acc["yes_count"] + acc["no_count"],
                yes_count         = acc["yes_count"],
                no_count          = acc["no_count"],
                raw_usd           = round(raw_usd, 2),
                weighted_usd      = round(win_weight, 2),
                avg_entry_price   = round(sum(ep) / len(ep), 4) if ep else None,
                avg_current_price = round(sum(cp) / len(cp), 4) if cp else None,
            )

        logger.info(
            f"Elite consensus: {len(consensus)} active markets, "
            f"{len(all_condition_ids)} total positions seen"
        )
        return EliteSignalBundle(consensus=consensus, all_condition_ids=all_condition_ids)

    def get_discovered_markets(
        self,
        known_condition_ids: set,
        polymarket_client,
    ) -> list[dict]:
        """
        Fetch and parse markets that elite traders hold but are NOT in the
        current scan (not in known_condition_ids). Batches gamma API calls
        to avoid URL length limits.
        """
        from config import MIN_DAYS_TO_RESOLVE, MAX_DAYS_TO_RESOLVE

        # Collect undiscovered condition IDs from the last consensus run
        all_cids = set()
        for wallet in self.elite_wallets:
            for pos in self.fetch_wallet_positions(wallet.address):
                cid = pos.get("conditionId") or pos.get("condition_id") or ""
                if cid:
                    all_cids.add(cid)

        new_cids = [c for c in all_cids if c not in known_condition_ids]
        if not new_cids:
            return []

        logger.info(f"Market discovery: fetching {len(new_cids)} markets not in current scan")
        discovered = []
        batch_size = 20

        for i in range(0, len(new_cids), batch_size):
            batch = new_cids[i : i + batch_size]
            try:
                resp = polymarket_client.session.get(
                    f"{polymarket_client.base_url}/markets",
                    params=[("conditionIds", cid) for cid in batch],
                    timeout=15,
                )
                if not resp.ok:
                    logger.warning(f"Discovery batch {i//batch_size + 1} failed: {resp.status_code}")
                    continue

                for raw in resp.json():
                    parsed = polymarket_client.parse_market_price(raw)
                    if not parsed or not parsed.get("market_id"):
                        continue

                    days = parsed.get("days_to_resolve")
                    if days is not None:
                        if MAX_DAYS_TO_RESOLVE and days > MAX_DAYS_TO_RESOLVE:
                            continue
                        if MIN_DAYS_TO_RESOLVE and days < MIN_DAYS_TO_RESOLVE:
                            continue

                    parsed["is_discovered_market"] = True
                    discovered.append(parsed)

            except Exception as e:
                logger.warning(f"Discovery batch error: {e}")

        logger.info(f"Market discovery: added {len(discovered)} new markets")
        return discovered

    def get_elite_signals(self) -> list[dict]:
        """Legacy flat signal list — kept for backward compatibility."""
        signals = []
        for wallet in self.elite_wallets:
            positions = self.fetch_wallet_positions(wallet.address)
            for pos in positions:
                outcome = pos.get("outcome", "Yes").upper()
                if outcome not in ("YES", "NO"):
                    outcome = "YES"
                signals.append({
                    "wallet":       wallet.address,
                    "win_rate":     wallet.win_rate,
                    "total_trades": wallet.total_trades,
                    "market_id":    pos.get("conditionId") or pos.get("market_id"),
                    "question":     pos.get("title", "Unknown"),
                    "outcome":      outcome,
                    "size_usd":     float(pos.get("currentValue", 0)),
                })

        logger.info(f"Got {len(signals)} elite wallet signals")
        return signals
