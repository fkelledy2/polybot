# signals/clustering.py
# ─────────────────────────────────────────────────────────────
# Topic clustering for correlation-aware portfolio management (S4-2).
# Groups markets by keyword overlap using BFS connected components.
# No ML dependency — pure stdlib graph traversal.
# ─────────────────────────────────────────────────────────────

import re
from collections import defaultdict

_STOP = frozenset({
    "will", "does", "is", "are", "has", "have", "the", "a", "an", "be",
    "in", "on", "at", "to", "of", "for", "from", "by", "and", "or",
    "not", "this", "that", "which", "who", "when", "what", "if", "it",
})


def _keywords(question: str) -> set[str]:
    words = re.sub(r"[^\w\s]", " ", question.lower()).split()
    return {w for w in words if w not in _STOP and len(w) > 3}


def cluster_markets(markets: list[dict]) -> dict[str, int]:
    """
    Assign each market a cluster ID based on keyword co-occurrence.
    Markets sharing ≥2 keywords are in the same cluster.
    Returns {market_id: cluster_id}.
    """
    n = len(markets)
    if n == 0:
        return {}

    ids = [m["market_id"] for m in markets]
    kws = [_keywords(m.get("question", "")) for m in markets]

    adj: dict[int, set] = defaultdict(set)
    for i in range(n):
        for j in range(i + 1, n):
            if len(kws[i] & kws[j]) >= 2:
                adj[i].add(j)
                adj[j].add(i)

    visited = [-1] * n
    cluster_id = 0
    for start in range(n):
        if visited[start] != -1:
            continue
        queue = [start]
        visited[start] = cluster_id
        while queue:
            node = queue.pop()
            for neighbor in adj[node]:
                if visited[neighbor] == -1:
                    visited[neighbor] = cluster_id
                    queue.append(neighbor)
        cluster_id += 1

    return {ids[i]: visited[i] for i in range(n)}


def get_market_cluster(market_id: str, clusters: dict[str, int]) -> int:
    return clusters.get(market_id, -1)
