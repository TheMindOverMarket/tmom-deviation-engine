"""
Deviation Engine — FinalizationWorker

Resolves deferred deviation costs when positions are closed.
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import List, Dict, Optional
from deviation.models import DeviationRecord, DeviationType, Costability
from deviation.position_tracker import ClosedSlice

logger = logging.getLogger(__name__)


@dataclass
class FinalizationUpdate:
    record: DeviationRecord
    cost_delta: float = 0.0
    gain_delta: float = 0.0


class FinalizationWorker:
    EPSILON = 1e-12

    def __init__(self):
        self._pending: Dict[str, List[DeviationRecord]] = {}

    def register_pending(self, record: DeviationRecord):
        if record.costability != Costability.FINAL_DEFERRED:
            return
        if record.decision_id:
            if record.decision_id not in self._pending:
                self._pending[record.decision_id] = []
            self._pending[record.decision_id].append(record)

    def finalize(self, closed_slices: List[ClosedSlice]) -> List[FinalizationUpdate]:
        updates: List[FinalizationUpdate] = []
        for slice in closed_slices:
            pending_records = self._pending.get(slice.decision_id, [])
            remaining_records = []
            for record in pending_records:
                cost_delta = self._compute_cost(record, slice)
                gain_delta = 0.0
                if record.deviation_type == DeviationType.INVALID_TRADE:
                    if slice.realized_pnl > 0:
                        gain_delta = slice.realized_pnl
                        cost_delta = 0.0
                    else:
                        cost_delta = abs(slice.realized_pnl)
                        gain_delta = 0.0

                record.finalized_cost = (record.finalized_cost or 0.0) + cost_delta
                record.unauthorized_gain = (record.unauthorized_gain or 0.0) + gain_delta
                updates.append(FinalizationUpdate(record=record, cost_delta=cost_delta, gain_delta=gain_delta))

                if slice.remaining_qty_after <= self.EPSILON:
                    record.finalized_at = time.time() * 1000
                else:
                    remaining_records.append(record)

            if remaining_records:
                self._pending[slice.decision_id] = remaining_records
            else:
                self._pending.pop(slice.decision_id, None)
        return updates

    def _compute_cost(self, record: DeviationRecord, slice: ClosedSlice) -> float:
        if record.deviation_type in (DeviationType.EARLY_ENTRY, DeviationType.LATE_ENTRY):
            if record.canonical_price_expected is not None and record.canonical_price_actual is not None:
                return slice.qty * abs(record.canonical_price_actual - record.canonical_price_expected)
            return abs(slice.realized_pnl)
        elif record.deviation_type == DeviationType.INVALID_TRADE:
            return abs(slice.realized_pnl)
        return 0.0

    def get_pending_count(self, decision_id: Optional[str] = None) -> int:
        if decision_id:
            return sum(1 for record in self._pending.get(decision_id, []) if record.finalized_at is None)
        return sum(1 for records in self._pending.values() for record in records if record.finalized_at is None)

    def clear_decision(self, decision_id: str):
        self._pending.pop(decision_id, None)
