"""Resolve safe crypto transfer routes between two SPOT exchanges.

A route is valid only when the source allows withdrawal, the destination
allows deposits, and both sides expose a compatible canonical network.
Network aliases are accepted only when they do not hide an asset-qualified
network name (for example USDT-TRC20 must not silently match a generic TRX
network entry). No network is inferred from response ordering.
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
    for token, canonical in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        if raw == token or raw.endswith("-" + token) or raw.endswith("_" + token):
            return canonical
    return raw


def _is_asset_qualified(value: str) -> bool:
    raw = str(value or "").upper().strip()
    return any(raw.endswith("-" + token) or raw.endswith("_" + token) for token in ALIASES if token not in {"ETH", "TRX", "BSC", "SOL"})


def _compatible(source_network: str, destination_network: str) -> bool:
    source_raw = str(source_network or "").upper().strip()
    destination_raw = str(destination_network or "").upper().strip()
    if source_raw == destination_raw:
        return True
    # Generic aliases such as ERC20/ETH and TRC20/TRON are compatible.
    # Do not bridge an asset-qualified exchange label to a generic network.
    if _is_asset_qualified(source_raw) != _is_asset_qualified(destination_raw):
        return False
    return canonical_network(source_raw) == canonical_network(destination_raw)


@dataclass(frozen=True)
class NetworkRoute:
    network: str
    source_network: str
    destination_network: str
    withdrawal_fee: float
    min_withdrawal: float
    memo_required: bool


def resolve_route(source: Iterable[NetworkInfo], destination: Iterable[NetworkInfo]) -> Optional[NetworkRoute]:
    destinations = [item for item in destination if item.deposit_enabled]
    candidates = []
    for src in source:
        if not src.withdrawal_enabled:
            continue
        for dst in destinations:
            if not _compatible(src.network, dst.network):
                continue
            key = canonical_network(src.network)
            candidates.append(NetworkRoute(
                key,
                src.network,
                dst.network,
                src.withdrawal_fee,
                src.min_withdrawal,
                bool(src.memo_required or dst.memo_required),
            ))
    return min(candidates, key=lambda x: (x.withdrawal_fee, x.network)) if candidates else None
