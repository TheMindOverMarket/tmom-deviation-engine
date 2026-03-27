"""
Deviation Engine — MarketAdapter Interface

TMOM core depends on MarketAdapter, never on exchange-specific code.
V1 binding: reads from market state snapshots pushed by the backend.
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MarketQuote:
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    last_trade: Optional[float] = None
    timestamp_ms: Optional[float] = None


class MarketAdapter(ABC):
    @abstractmethod
    def get_best_bid(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]: ...
    @abstractmethod
    def get_best_ask(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]: ...
    @abstractmethod
    def get_mid_price(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]: ...
    @abstractmethod
    def get_last_trade(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]: ...
    @abstractmethod
    def get_quote(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[MarketQuote]: ...


class PriceResolver:
    def __init__(self, adapter: MarketAdapter):
        self._adapter = adapter

    def resolve_canonical_price(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]:
        mid = self._adapter.get_mid_price(symbol, ts_ms)
        return mid or self._adapter.get_last_trade(symbol, ts_ms)

    def resolve_entry_price(self, symbol: str, side: str, ts_ms: Optional[float] = None) -> Optional[float]:
        if side.lower() == "buy":
            price = self._adapter.get_best_ask(symbol, ts_ms)
        else:
            price = self._adapter.get_best_bid(symbol, ts_ms)
        return price or self.resolve_canonical_price(symbol, ts_ms)

    def get_quote_snapshot(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[MarketQuote]:
        return self._adapter.get_quote(symbol, ts_ms)


class LiveMarketAdapter(MarketAdapter):
    """V1 binding: reads from WebSocket-fed state cache."""

    def __init__(self):
        self._state_cache: Dict[str, Dict[str, Any]] = {}

    def update_state(self, symbol: str, snapshot: Dict[str, Any]):
        self._state_cache[symbol] = snapshot

    def _get_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        if symbol in self._state_cache:
            return self._state_cache[symbol]
        alt = f"{symbol}/USD"
        if alt in self._state_cache:
            return self._state_cache[alt]
        if "/" in symbol:
            base = symbol.split("/")[0]
            if base in self._state_cache:
                return self._state_cache[base]
        return None

    def get_best_bid(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]:
        snap = self._get_snapshot(symbol)
        return snap.get("last_price") if snap else None

    def get_best_ask(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]:
        snap = self._get_snapshot(symbol)
        return snap.get("last_price") if snap else None

    def get_mid_price(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]:
        snap = self._get_snapshot(symbol)
        return snap.get("last_price") if snap else None

    def get_last_trade(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[float]:
        snap = self._get_snapshot(symbol)
        return snap.get("last_price") if snap else None

    def get_quote(self, symbol: str, ts_ms: Optional[float] = None) -> Optional[MarketQuote]:
        snap = self._get_snapshot(symbol)
        if not snap:
            return None
        price = snap.get("last_price")
        return MarketQuote(symbol=symbol, bid=price, ask=price, mid=price,
                           last_trade=price, timestamp_ms=snap.get("last_tick_timestamp_ms"))
