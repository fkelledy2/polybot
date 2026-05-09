# signals/categorizer.py
# ─────────────────────────────────────────────────────────────
# Detects the category of a prediction market from its question.
# Each category gets a specialized analysis prompt in claude_signal.py.
# ─────────────────────────────────────────────────────────────

import re

CATEGORIES = {
    "CRYPTO": [
        r"\bbitcoin\b", r"\bbtc\b", r"\beth(ereum)?\b", r"\bcrypto\b",
        r"\bsolana\b", r"\bsol\b", r"\bxrp\b", r"\balts?\b",
        r"all.?time.?high", r"\bhalving\b", r"\bdefi\b", r"\bnft\b",
        r"\b\$\d+[km]?\b.*price", r"above \$\d+,\d+",
    ],
    "SPORTS": [
        r"\bnfl\b", r"\bnba\b", r"\bnhl\b", r"\bnba\b", r"\bmlb\b",
        r"\bpremier league\b", r"\bla liga\b", r"\bchampions league\b",
        r"\bsuper bowl\b", r"\bworld series\b", r"\bstanley cup\b",
        r"\bwimbledon\b", r"\bus open\b", r"\bfrench open\b", r"\baustralia\b.*open",
        r"\bmasters\b", r"\bpga\b", r"\bf1\b", r"\bformula 1\b",
        r"\bufc\b", r"\bbox(ing)?\b", r"\bwrestle\b",
        r"\bwinner\b.*\b(match|game|series|tournament|cup|championship)\b",
        r"\b(beat|defeat|win)\b.*\bvs?\b",
        r"esports", r"\blol\b", r"\bcs:?go\b", r"\bdota\b", r"\bvalorant\b",
        r"\bbo[135]\b",  # Best-of-X series
    ],
    "POLITICS": [
        r"\belection\b", r"\bpresident\b", r"\bsenator\b", r"\bcongress\b",
        r"\bvote\b", r"\bapproval\b", r"\bpoll\b", r"\bcampaign\b",
        r"\bprimary\b", r"\bballot\b", r"\bparty\b", r"\bdemocrat\b",
        r"\brepublican\b", r"\btrump\b", r"\bbiden\b", r"\bvance\b",
        r"\bmayor\b", r"\bgovernor\b", r"\bparliament\b", r"\bpm\b",
        r"\bprime minister\b", r"\bcabinet\b", r"\bimpeach\b",
    ],
    "MACRO": [
        r"\bfed\b", r"\bfederal reserve\b", r"\brate cut\b", r"\brate hike\b",
        r"\binflation\b", r"\bcpi\b", r"\bgdp\b", r"\brecession\b",
        r"\bunemployment\b", r"\bjobs?\b.*report", r"\becb\b", r"\bfomc\b",
        r"\byield\b", r"\bbond\b", r"\btreasury\b", r"\btariff\b",
        r"\btrade war\b", r"\bsanction\b",
    ],
    "TECH": [
        r"\bipo\b", r"\blaunch\b", r"\brelease\b", r"\bship(s|ped)?\b",
        r"\bacquisition\b", r"\bmerger\b", r"\bapple\b", r"\bgoogle\b",
        r"\bmicrosoft\b", r"\bmeta\b", r"\bai\b", r"\bgpt\b", r"\bclaude\b",
        r"\bstarship\b", r"\bspacex\b", r"\blevel \d\b",
        r"\bfda\b.*approv", r"\bapprov.*\bfda\b",
    ],
    "ENTERTAINMENT": [
        r"\boscar\b", r"\bgrammy\b", r"\bemmy\b", r"\bbafta\b",
        r"\bbox office\b", r"\bmovie\b", r"\bfilm\b", r"\balbum\b",
        r"\baward\b", r"\bmrbeast\b", r"\byoutube\b", r"\bviews?\b",
        r"\bstreaming\b", r"\bnetflix\b",
    ],
    "GEO": [
        r"\bwar\b", r"\bceasefire\b", r"\bmilitary\b", r"\binvasion\b",
        r"\bgaza\b", r"\bukraine\b", r"\bussia\b", r"\biran\b", r"\bchina\b",
        r"\bnato\b", r"\bsanction\b", r"\bconflict\b", r"\battack\b",
    ],
    "EARNINGS": [
        r"\beat.*earnings\b", r"\bearnings.*beat\b",
        r"\bquarterly (earnings|results|revenue)\b",
        r"\beps\b", r"\bbeat.*eps\b", r"\bmiss.*eps\b",
        r"\bearnings per share\b", r"\bq[1-4] (earnings|results)\b",
        r"\bbeat.*consensus\b", r"\b(revenue|profit).*estimates?\b",
        r"\bearnings (surprise|miss|beat)\b",
    ],
}

CATEGORY_CONTEXT = {
    "CRYPTO": (
        "This is a CRYPTOCURRENCY market. Key analysis factors:\n"
        "- Current market cycle (bull/bear), BTC halving schedule\n"
        "- On-chain metrics, exchange flows, whale activity\n"
        "- Macro correlation (risk-on/off, Fed policy)\n"
        "- Historical volatility — crypto moves faster than implied probabilities suggest\n"
        "- Specific price targets: check if the level is a key resistance/support"
    ),
    "SPORTS": (
        "This is a SPORTS/ESPORTS market. Key analysis factors:\n"
        "- Team/player current form and recent results\n"
        "- Head-to-head record and home/away advantage\n"
        "- Injuries, roster changes, fatigue\n"
        "- Tournament format (single game vs series)\n"
        "- Betting markets as signal (line movement, public vs sharp money)"
    ),
    "POLITICS": (
        "This is a POLITICS/ELECTION market. Key analysis factors:\n"
        "- Current polling averages and trend direction\n"
        "- Incumbent advantage (incumbents win ~70% of re-elections)\n"
        "- Fundamentals model (economy, approval rating)\n"
        "- Historical base rates for this type of outcome\n"
        "- Time remaining and how much can change"
    ),
    "MACRO": (
        "This is a MACROECONOMIC market. Key analysis factors:\n"
        "- Current central bank guidance and dot plot projections\n"
        "- Forward market pricing (futures, OIS swaps imply probability)\n"
        "- Recent economic data trajectory\n"
        "- Consensus economist forecasts\n"
        "- Sticky vs flexible components of the question"
    ),
    "TECH": (
        "This is a TECHNOLOGY/BUSINESS market. Key analysis factors:\n"
        "- Company's public statements, roadmaps, and recent announcements\n"
        "- Industry analyst consensus and track record of delivery\n"
        "- Regulatory environment and approval timelines\n"
        "- Comparable events/launches as base rate anchors"
    ),
    "ENTERTAINMENT": (
        "This is an ENTERTAINMENT market. Key analysis factors:\n"
        "- Historical patterns for this type of award/event\n"
        "- Current frontrunner based on precursor results\n"
        "- Creator's track record and recent trend\n"
        "- Specific numeric targets: check if they are historically achievable"
    ),
    "GEO": (
        "This is a GEOPOLITICAL market. Key analysis factors:\n"
        "- Current state of the conflict/situation\n"
        "- Historical base rate for this type of geopolitical outcome\n"
        "- Diplomatic signals, back-channel negotiations\n"
        "- Key actors' incentives and red lines\n"
        "- Deadline proximity: harder to resolve as deadline nears"
    ),
    "EARNINGS": (
        "This is a CORPORATE EARNINGS market. Key analysis factors:\n"
        "- Historical beat rate for this company and sector (~70% for S&P 500 broadly)\n"
        "- Analyst consensus estimate vs company guidance\n"
        "- Recent earnings trend and management credibility\n"
        "- NOTE: Claude's training data may not include current quarter guidance — apply\n"
        "  caution and lean on base rates rather than company-specific current data"
    ),
    "GENERAL": (
        "Analyse this market carefully using base rates and available evidence."
    ),
}


def detect_category(question: str) -> str:
    """Return the best-matching category for a market question."""
    q = question.lower()
    scores = {cat: 0 for cat in CATEGORIES}
    for cat, patterns in CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, q, re.IGNORECASE):
                scores[cat] += 1
    best_cat = max(scores, key=scores.get)
    return best_cat if scores[best_cat] > 0 else "GENERAL"


def get_category_context(question: str) -> tuple[str, str]:
    """Return (category_name, analysis_context_string) for a question."""
    cat = detect_category(question)
    return cat, CATEGORY_CONTEXT.get(cat, CATEGORY_CONTEXT["GENERAL"])
