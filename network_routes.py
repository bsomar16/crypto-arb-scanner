"""Resolve safe crypto transfer routes between two SPOT exchanges.

A route is valid only when the source allows withdrawal, the destination
allows deposits, and both sides expose the same canonical network identifier.
No network is inferred from a symbol or selected by position in an exchange
response.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from exchange_adapter import NetworkInfo


ALIASES = {
    "ERC20": "ETH",
    "ETH": "ETH",
    "ETHEREUM": "ETH",
    "TRC20": "TRX",
    "TRON": "TRX",
    "BEP20": "BSC",
    "BSC": "BSC",
    "BEP-20": "BSC",
    "ARBITRUM": "ARBITRUM",
    "ARBITRUM ONE": "ARBITRUM",
    "ARB": "ARBITRUM",
    "OPTIMISM": "OPTIMISM",
    "OP": "OPTIMISM",
    "POLYGON": "POLYGON",
    "MATIC": "POLYGON",
    "SOL": "SOL",
    "SOLANA": "SOL",
    "AVAXC": "AVAX-C",
    "AVAX-C": "AVAX-C",
}


def canonical_network(value: str) -> str:
    raw = str(value or "").upper().strip()
    # Match common exchange forms such as USDT-TRC20 and USDT_TRX.
    for token, canonical in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if raw == token or raw.endswith("-" + token) or raw.endswith("_" + token):
            return canonical
    return raw


@dataclass(frozen=True)
class NetworkRoute:
    network: str
    source_network: str
    destination_network: str
    withdrawal_fee: float
    min_withdrawal: float
    memo_required: bool


def resolve_route(source: Iterable[NetworkInfo], destination: Iterable[NetworkInfo]) -> Optional[NetworkRoute]:
    dest = {}
    for item in destination:
        if item.deposit_enabled:
            dest.setdefault(canonical_network(item.network), item)
    candidates = []
    for src in source:
        if not src.withdrawal_enabled:
            continue
        key = canonical_network(src.network)
        dst = dest.get(key)
        if not dst:
            continue
        candidates.append(NetworkRoute(key, src.network, dst.network,
                                       src.withdrawal_fee, src.min_withdrawal,
                                       bool(src.memo_required or dst.memo_required)))
    # Deterministic and conservative: lowest fixed withdrawal fee, then name.
    return min(candidates, key=lambda x: (x.withdrawal_fee, x.network)) if candidates else None
