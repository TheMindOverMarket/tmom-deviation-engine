"""
Deviation Engine — DeviationAttributor

Evaluates the hierarchy of supervision:
  VALIDITY → TIMING → SIZE → RISK/PROCESS

Once a higher-priority family triggers, lower families are NOT evaluated
to prevent double-counting costs.
"""

from __future__ import annotations
import logging
import time
from typing import Optional, Dict, Any, List
from deviation.models import (
    DeviationRecord, DeviationType, DeviationFamily, Costability, Severity,
    DEVIATION_FAMILY_MAP, COSTABILITY_MAP,
)
from deviation.matcher import MatchResult
from deviation.market_adapter import PriceResolver

logger = logging.getLogger(__name__)

SEVERITY_MAP: Dict[DeviationType, Severity] = {
    DeviationType.INVALID_TRADE: Severity.CRITICAL,
    DeviationType.EARLY_ENTRY: Severity.HIGH,
    DeviationType.LATE_ENTRY: Severity.MEDIUM,
    DeviationType.MISSED_TRADE: Severity.MEDIUM,
    DeviationType.OVERSIZE: Severity.HIGH,
    DeviationType.UNDERSIZE: Severity.LOW,
    DeviationType.COOLDOWN_VIOLATION: Severity.HIGH,
    DeviationType.PYRAMIDING_VIOLATION: Severity.HIGH,
    DeviationType.DAILY_LOSS_CAP_BREACH: Severity.CRITICAL,
    DeviationType.MAX_POSITIONS_BREACH: Severity.CRITICAL,
}


class DeviationAttributor:
    def __init__(self, price_resolver: PriceResolver):
        self._price_resolver = price_resolver

    def attribute(
        self, match_result: MatchResult,
        position_context: Optional[Dict[str, Any]] = None,
    ) -> List[DeviationRecord]:
        deviations: List[DeviationRecord] = []
        decision = match_result.decision
        action = match_result.matched_action
        now_ms = time.time() * 1000

        # ─── Layer 1: VALIDITY ────────────────────────────────────
        if not match_result.is_matched:
            dev = self._create_record(
                decision=decision, deviation_type=DeviationType.INVALID_TRADE,
                ts=now_ms, action=action,
            )
            deviations.append(dev)
            return deviations  # Hierarchy: stop here

        # ─── Layer 2: TIMING ─────────────────────────────────────
        timing = match_result.timing_class
        if timing == "EARLY_ENTRY":
            dev = self._create_record(
                decision=decision, deviation_type=DeviationType.EARLY_ENTRY,
                ts=now_ms, action=action,
            )
            dev.canonical_price_expected = action.canonical_price_at_creation if action else None
            dev.canonical_price_actual = decision.price
            if dev.canonical_price_expected and dev.canonical_price_actual:
                dev.price_delta = dev.canonical_price_actual - dev.canonical_price_expected
            deviations.append(dev)
            return deviations

        if timing == "LATE_ENTRY":
            dev = self._create_record(
                decision=decision, deviation_type=DeviationType.LATE_ENTRY,
                ts=now_ms, action=action,
            )
            dev.canonical_price_expected = action.canonical_price_at_creation if action else None
            dev.canonical_price_actual = decision.price
            if dev.canonical_price_expected and dev.canonical_price_actual:
                dev.price_delta = dev.canonical_price_actual - dev.canonical_price_expected
            deviations.append(dev)
            return deviations

        # COMPLIANT timing — continue to SIZE evaluation
        # ─── Layer 3: SIZE ────────────────────────────────────────
        if action and action.expected_qty and decision.filled_qty:
            ratio = decision.filled_qty / action.expected_qty
            if ratio > 1.15:
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.OVERSIZE,
                    ts=now_ms, action=action,
                )
                excess_qty = decision.filled_qty - action.expected_qty
                if decision.price and action.canonical_price_at_creation:
                    dev.candidate_cost = excess_qty * abs(decision.price - action.canonical_price_at_creation)
                deviations.append(dev)
                return deviations
            elif ratio < 0.85:
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.UNDERSIZE,
                    ts=now_ms, action=action,
                )
                deviations.append(dev)

        # ─── Layer 4: RISK/PROCESS ────────────────────────────────
        if position_context:
            if position_context.get("cooldown_violated"):
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.COOLDOWN_VIOLATION,
                    ts=now_ms, action=action,
                )
                deviations.append(dev)
            if position_context.get("pyramiding_violated"):
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.PYRAMIDING_VIOLATION,
                    ts=now_ms, action=action,
                )
                deviations.append(dev)
            if position_context.get("daily_loss_cap_breached"):
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.DAILY_LOSS_CAP_BREACH,
                    ts=now_ms, action=action,
                )
                deviations.append(dev)
            if position_context.get("max_positions_breached"):
                dev = self._create_record(
                    decision=decision, deviation_type=DeviationType.MAX_POSITIONS_BREACH,
                    ts=now_ms, action=action,
                )
                deviations.append(dev)

        return deviations

    def _create_record(
        self, decision, deviation_type: DeviationType,
        ts: float, action=None,
    ) -> DeviationRecord:
        return DeviationRecord(
            session_id=decision.session_id,
            decision_id=decision.id,
            compliant_action_id=action.id if action else None,
            deviation_type=deviation_type,
            deviation_family=DEVIATION_FAMILY_MAP[deviation_type],
            costability=COSTABILITY_MAP[deviation_type],
            severity=SEVERITY_MAP[deviation_type],
            detected_at=ts,
        )
