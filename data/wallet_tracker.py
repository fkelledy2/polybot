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
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl_usd: float = 0.0
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
            f"trades={self.total_trades} | "
            f"win_rate={self.win_rate:.1%} | "
            f"pnl=${self.total_pnl_usd:+,.2f})"
        )


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
                # Fallback: build ID also appears as a path segment
                match = re.search(r'/(build-[A-Za-z0-9_-]+)/', resp.text)
            return match.group(1) if match else None
        except requests.RequestException as e:
            logger.warning(f"Could not fetch leaderboard page for build ID: {e}")
            return None

    def fetch_top_wallets(self, limit: int = 100) -> list[dict]:
        """
        Fetch top traders from Polymarket's leaderboard via the Next.js
        _next/data endpoint (SSR JSON, no browser needed).

        Returns list of dicts: {rank, proxyWallet, pnl, name, ...}
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
        """
        Build the elite wallet list from the top-N leaderboard traders
        with positive 30-day PnL.
        """
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

            name = t.get("name") or t.get("pseudonym") or addr[:10]
            profile = WalletProfile(
                address=addr,
                total_trades=100,   # leaderboard wallets have many trades by definition
                winning_trades=60,  # 60% assumed for leaderboard top-profit traders
                total_pnl_usd=pnl,
            )
            profiles.append(profile)
            self.tracked_wallets[addr] = profile
            logger.info(
                f"  Elite (rank {t.get('rank')}): {name} "
                f"{addr[:10]}... PnL=${pnl:,.0f}"
            )

            if len(profiles) >= top_n:
                break

        self.elite_wallets = profiles
        logger.info(f"Elite wallet list ready: {len(self.elite_wallets)} wallets")
        return self.elite_wallets

    def get_elite_signals(self) -> list[dict]:
        """Return current open positions of elite wallets as trading signals."""
        signals = []
        for wallet in self.elite_wallets:
            positions = self.fetch_wallet_positions(wallet.address)
            for pos in positions:
                outcome = pos.get("outcome", "Yes").upper()
                if outcome not in ("YES", "NO"):
                    outcome = "YES"
                signals.append({
                    "wallet": wallet.address,
                    "win_rate": wallet.win_rate,
                    "total_trades": wallet.total_trades,
                    "market_id": pos.get("conditionId") or pos.get("market_id"),
                    "question": pos.get("title", "Unknown"),
                    "outcome": outcome,
                    "size_usd": float(pos.get("currentValue", 0)),
                })

        logger.info(f"Got {len(signals)} elite wallet signals")
        return signals
