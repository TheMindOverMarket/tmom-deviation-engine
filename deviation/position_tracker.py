"""
Deviation Engine — Position Tracker

FIFO lot matching, execution buckets, PnL computation.
Each lot is linked to exactly one decision for causal traceability.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from deviation.models import ExecutionBucket, PositionLot

logger = logging.getLogger(__name__)


@dataclass
class ClosedSlice:
    lot_id: str = ""
    decision_id: str = ""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: float = 0.0
    realized_pnl: float = 0.0
    is_excess: bool = False
    remaining_qty_after: float = 0.0


class PositionTracker:
    def __init__(self):
        self._lots: Dict[str, List[PositionLot]] = {}
        self._buckets: Dict[str, ExecutionBucket] = {}

    def process_fill(
        self, decision_id: str, symbol: str, side: str,
        qty: float, price: float, ts_ms: float, is_excess: bool = False,
    ) -> Tuple[List[PositionLot], List[ClosedSlice]]:
        # Execution bucket
        if decision_id not in self._buckets:
            self._buckets[decision_id] = ExecutionBucket(
                decision_id=decision_id, symbol=symbol, side=side,
            )
        bucket = self._buckets[decision_id]
        bucket.add_fill(qty=qty, price=price, fill_id=f"{decision_id}_{len(bucket.fills)}")

        sym = symbol.upper()
        open_lots = [l for l in self._lots.get(sym, []) if l.remaining_qty > 0]
        new_lots, closed_slices = [], []

        if not open_lots:
            lot = self._create_lot(decision_id, sym, side, qty, price, ts_ms, is_excess)
            new_lots.append(lot)
        elif open_lots[0].side.lower() == side.lower():
            lot = self._create_lot(decision_id, sym, side, qty, price, ts_ms, is_excess)
            new_lots.append(lot)
        else:
            remaining_qty = qty
            for lot in open_lots:
                if remaining_qty <= 0:
                    break
                matched_qty = min(lot.remaining_qty, remaining_qty)
                if lot.side.lower() == "buy":
                    pnl = matched_qty * (price - lot.entry_price)
                else:
                    pnl = matched_qty * (lot.entry_price - price)
                closed_slices.append(ClosedSlice(
                    lot_id=lot.id, decision_id=lot.decision_id, symbol=sym,
                    side=lot.side, entry_price=lot.entry_price, exit_price=price,
                    qty=matched_qty, realized_pnl=pnl, is_excess=lot.is_excess,
                    remaining_qty_after=lot.remaining_qty - matched_qty,
                ))
                lot.remaining_qty -= matched_qty
                remaining_qty -= matched_qty
            if remaining_qty > 0:
                lot = self._create_lot(decision_id, sym, side, remaining_qty, price, ts_ms, is_excess)
                new_lots.append(lot)

        return new_lots, closed_slices

    def _create_lot(self, decision_id, symbol, side, qty, price, ts_ms, is_excess) -> PositionLot:
        lot = PositionLot(
            decision_id=decision_id, symbol=symbol, side=side,
            entry_price=price, qty=qty, remaining_qty=qty,
            is_excess=is_excess, created_at=ts_ms,
        )
        if symbol not in self._lots:
            self._lots[symbol] = []
        self._lots[symbol].append(lot)
        return lot

    def get_open_lots(self, symbol: str) -> List[PositionLot]:
        return [l for l in self._lots.get(symbol.upper(), []) if l.remaining_qty > 0]

    def get_position_qty(self, symbol: str) -> float:
        return sum(l.remaining_qty for l in self.get_open_lots(symbol))

    def get_position_side(self, symbol: str) -> Optional[str]:
        lots = self.get_open_lots(symbol)
        return lots[0].side if lots else None

    def get_bucket(self, decision_id: str) -> Optional[ExecutionBucket]:
        return self._buckets.get(decision_id)

    def clear_symbol(self, symbol: str):
        self._lots.pop(symbol.upper(), None)
