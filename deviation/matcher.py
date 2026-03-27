"""
Deviation Engine — DecisionMatcher

Matches a DecisionEvent to the best CompliantAction and classifies timing.
"""

from __future__ import annotations
import logging
from typing import Optional, List
from dataclasses import dataclass, field
from deviation.models import (
    CompliantAction, DecisionEvent, ActionFamily, ActionLifecycle,
)
from deviation.compliant_actions import CompliantActionStore

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    decision: DecisionEvent
    matched_action: Optional[CompliantAction] = None
    is_matched: bool = False
    timing_class: str = "INVALID"
    time_delta_ms: Optional[float] = None
    candidates_considered: List[str] = field(default_factory=list)


class DecisionMatcher:
    LATE_ENTRY_GRACE_MS = 30_000  # 30s after expiry still counts as LATE

    def __init__(self, action_store: CompliantActionStore):
        self._store = action_store

    def match(self, decision: DecisionEvent) -> MatchResult:
        result = MatchResult(decision=decision)

        # 1. Try exact match (session, symbol, side, ENTER)
        active = self._store.find_active(
            session_id=decision.session_id, symbol=decision.symbol,
            side=decision.side, family=ActionFamily.ENTER, ts_ms=decision.timestamp_ms,
        )

        if active:
            result.matched_action = active
            result.is_matched = True
            result.time_delta_ms = decision.timestamp_ms - (active.activated_at or active.created_at)

            if active.is_active(decision.timestamp_ms):
                result.timing_class = "COMPLIANT"
            else:
                result.timing_class = "LATE_ENTRY"

            active.take(decision.timestamp_ms)
            self._store.resolve(active.id, ActionLifecycle.TAKEN, decision.timestamp_ms)
            logger.info(f"[MATCHER] Matched decision {decision.id[:8]} → action {active.id[:8]} timing={result.timing_class}")
            return result

        # 2. Check for recently expired actions (LATE_ENTRY)
        all_actions = self._store.get_all_actions(decision.session_id)
        for action in reversed(all_actions):
            if (action.symbol.upper() == decision.symbol.upper() and
                action.side.lower() == decision.side.lower() and
                action.action_family == ActionFamily.ENTER):

                if action.lifecycle in (ActionLifecycle.MISSED, ActionLifecycle.EXPIRED):
                    if action.resolved_at:
                        elapsed = decision.timestamp_ms - action.resolved_at
                        if elapsed <= self.LATE_ENTRY_GRACE_MS:
                            result.matched_action = action
                            result.is_matched = True
                            result.timing_class = "LATE_ENTRY"
                            result.time_delta_ms = elapsed
                            logger.info(f"[MATCHER] Late match: {decision.id[:8]} → expired action {action.id[:8]}")
                            return result

                if action.lifecycle == ActionLifecycle.ACTIVE:
                    if action.activated_at and decision.timestamp_ms < action.activated_at:
                        result.matched_action = action
                        result.is_matched = True
                        result.timing_class = "EARLY_ENTRY"
                        result.time_delta_ms = action.activated_at - decision.timestamp_ms
                        logger.info(f"[MATCHER] Early entry: {decision.id[:8]} → before action {action.id[:8]}")
                        return result

        # 3. No match → INVALID
        result.timing_class = "INVALID"
        logger.info(f"[MATCHER] No match for decision {decision.id[:8]} {decision.symbol} {decision.side}")
        return result
