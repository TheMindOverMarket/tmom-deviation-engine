"""
Deviation Engine — FinalizationWorker

Resolves deferred deviation costs when positions are closed.
"""

from __future__ import annotations
import logging
from typing import List, Dict, Optional
from deviation.models import DeviationRecord, DeviationType, Costability
from deviation.position_tracker import ClosedSlice

logger = logging.getLogger(__name__)


class FinalizationWorker:
    def __init__(self):
        self._pending: Dict[str, List[DeviationRecord]] = {}

    def register_pending(self, record: DeviationRecord):
        if record.costability != Costability.FINAL_DEFERRED:
            return
        if record.decision_id:
            if record.decision_id not in self._pending:
                self._pending[record.decision_id] = []
            self._pending[record.decision_id].append(record)

    def finalize(self, closed_slices: List[ClosedSlice]) -> List[DeviationRecord]:
        finalized = []
        for slice in closed_slices:
            pending_records = self._pending.get(slice.decision_id, [])
            for record in pending_records:
                if record.finalized_cost is not None:
                    continue
                finalized_cost = self._compute_cost(record, slice)
                record.finalized_cost = finalized_cost
                if record.deviation_type == DeviationType.INVALID_TRADE:
                    if slice.realized_pnl > 0:
                        record.unauthorized_gain = slice.realized_pnl
                        record.finalized_cost = 0.0
                    else:
                        record.finalized_cost = abs(slice.realized_pnl)
                        record.unauthorized_gain = 0.0
                finalized.append(record)
        return finalized

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
            return len(self._pending.get(decision_id, []))
        return sum(len(v) for v in self._pending.values())

    def clear_decision(self, decision_id: str):
        self._pending.pop(decision_id, None)
