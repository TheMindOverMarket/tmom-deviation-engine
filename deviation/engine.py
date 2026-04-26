"""
Deviation Engine — Main Orchestrator

Session-scoped facade that ties all components together.
Each session gets its own instance. The registry mirrors the
pattern used by the backend's _active_sessions.
"""

from __future__ import annotations
import logging
import time
from typing import Optional, Dict, Any, List
from deviation.models import (
    CompliantAction, DecisionEvent, DeviationRecord,
    ActionFamily, ActionLifecycle, ExpiryPolicy, Costability, SessionPolicy,
)
from deviation.market_adapter import MarketAdapter, PriceResolver, LiveMarketAdapter
from deviation.compliant_actions import CompliantActionStore
from deviation.matcher import DecisionMatcher, MatchResult
from deviation.attributor import DeviationAttributor
from deviation.position_tracker import PositionTracker, ClosedSlice
from deviation.finalization import FinalizationWorker
from deviation.explainability import ExplainabilityBuilder

logger = logging.getLogger(__name__)


class DeviationEngine:
    def __init__(
        self, session_id: str, playbook_id: str, user_id: str,
        market_adapter: Optional[MarketAdapter] = None,
        session_policy: Optional[SessionPolicy] = None,
        default_expected_qty: Optional[float] = None,
    ):
        self.session_id = session_id
        self.playbook_id = playbook_id
        self.user_id = user_id
        self.session_policy = session_policy or SessionPolicy()
        self.default_expected_qty = default_expected_qty

        self._market_adapter = market_adapter or LiveMarketAdapter()
        self._price_resolver = PriceResolver(self._market_adapter)
        self._action_store = CompliantActionStore()
        self._matcher = DecisionMatcher(self._action_store)
        self._attributor = DeviationAttributor(self._price_resolver)
        self._position_tracker = PositionTracker()
        self._finalization = FinalizationWorker()
        self._explainability = ExplainabilityBuilder()

        self._trade_count: int = 0
        self._pending_reasoning: Dict[str, str] = {}  # order_id -> reasoning buffer

        logger.info(f"[ENGINE] Init session={session_id[:8]} playbook={playbook_id[:8]}")

    # ─── PUBLIC API ─────────────────────────────────────────────────

    def register_compliant_action(
        self, symbol: str, side: str, triggered_rule_ids: List[str],
        canonical_price: Optional[float] = None, expected_qty: Optional[float] = None,
        expiry_policy: ExpiryPolicy = ExpiryPolicy.WINDOW_N_SECONDS,
        expiry_seconds: float = 60.0,
        action_family: ActionFamily = ActionFamily.ENTER,
        rule_evaluation_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[CompliantAction]:
        ts_ms = time.time() * 1000
        if canonical_price is None:
            canonical_price = self._price_resolver.resolve_canonical_price(symbol, ts_ms)
        if expected_qty is None:
            expected_qty = self.default_expected_qty

        action = CompliantAction(
            session_id=self.session_id, playbook_id=self.playbook_id,
            symbol=symbol.upper(), side=side.lower(), action_family=action_family,
            created_at=ts_ms, canonical_price_at_creation=canonical_price,
            triggered_rule_ids=triggered_rule_ids, expected_qty=expected_qty,
            expiry_policy=expiry_policy, expiry_seconds=expiry_seconds,
            rule_evaluation_snapshot=rule_evaluation_snapshot,
        )

        if not self._action_store.add(action):
            logger.warning(f"[ENGINE] Overlap policy rejected action for {symbol} {side}")
            return None

        self._action_store.activate(action.id, ts_ms)
        logger.info(f"[ENGINE] CompliantAction created: {action.id[:8]} {symbol} {side} price={canonical_price}")
        return action

    def process_decision(
        self, symbol: str, side: str, qty: float, filled_qty: float,
        price: Optional[float], order_id: str,
        timestamp_ms: Optional[float] = None,
        market_attachment_state: Optional[str] = None,
        position_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = timestamp_ms or (time.time() * 1000)

        # 1. Create DecisionEvent
        quote = self._price_resolver.get_quote_snapshot(symbol, ts)
        decision = DecisionEvent(
            session_id=self.session_id, user_id=self.user_id,
            symbol=symbol.upper(), side=side.lower(), qty=qty, filled_qty=filled_qty,
            price=price, order_id=order_id, timestamp_ms=ts,
            canonical_bid=quote.bid if quote else None,
            canonical_ask=quote.ask if quote else None,
            canonical_mid=quote.mid if quote else None,
            market_attachment_state=market_attachment_state,
        )
        self._decision_events.append(decision)
        self._trade_count += 1

        # 1.5 Check reasoning buffer for late-arriving trade
        if order_id in self._pending_reasoning:
            reasoning = self._pending_reasoning.pop(order_id)
            # This will be applied to deviations found in step 3 below
            # We store it temporarily in the decision object for easier attribution
            decision.ai_reasoning_buffer = reasoning

        # 2. Match
        match_result = self._matcher.match(decision)

        # 3. Attribute
        deviations = self._attributor.attribute(match_result, position_context)

        # 4. Explainability + cost accounting
        for dev in deviations:
            if hasattr(decision, 'ai_reasoning_buffer'):
                dev.ai_reasoning = getattr(decision, 'ai_reasoning_buffer')

            self._explainability.build(record=dev, decision=decision, action=match_result.matched_action)
            if dev.costability == Costability.FINAL_DEFERRED:
                self._finalization.register_pending(dev)
            if dev.costability == Costability.FINAL_IMMEDIATE and dev.candidate_cost:
                self._total_deviation_cost += dev.candidate_cost
        self._deviation_records.extend(deviations)

        # 5. Position tracking
        actual_qty = filled_qty or qty
        new_lots, closed_slices = self._position_tracker.process_fill(
            decision_id=decision.id, symbol=symbol.upper(), side=side.lower(),
            qty=actual_qty, price=price or 0.0, ts_ms=ts,
        )

        # 6. Finalize deferred
        finalized_records = []
        if closed_slices:
            finalized_records = self._finalization.finalize(closed_slices)
            for rec in finalized_records:
                if rec.finalized_cost:
                    self._total_deviation_cost += rec.finalized_cost
                if rec.unauthorized_gain:
                    self._total_unauthorized_gain += rec.unauthorized_gain

        return {
            "decision": decision.to_dict(),
            "match": {
                "is_matched": match_result.is_matched, "timing_class": match_result.timing_class,
                "matched_action_id": match_result.matched_action.id if match_result.matched_action else None,
                "time_delta_ms": match_result.time_delta_ms,
            },
            "deviations": [d.to_dict() for d in deviations],
            "finalized": [d.to_dict() for d in finalized_records],
            "position": {
                "new_lots": [l.to_dict() for l in new_lots],
                "closed_slices": len(closed_slices),
                "open_position_qty": self._position_tracker.get_position_qty(symbol),
            },
            "session_totals": {
                "total_deviation_cost": self._total_deviation_cost,
                "total_unauthorized_gain": self._total_unauthorized_gain,
                "trade_count": self._trade_count,
                "deviation_count": len(self._deviation_records),
                "pending_finalization": self._finalization.get_pending_count(),
            },
        }

    def update_deviation_reasoning(self, order_id: str, reasoning: str) -> bool:
        """
        Updates the AI reasoning for deviation records associated with a specific order.
        This is called when the Rule Engine finishes generating its explanation.
        """
        updated = False
        # Find decisions matching this order_id
        matching_decision_ids = [d.id for d in self._decision_events if d.order_id == order_id]
        
        if not matching_decision_ids:
            # Race Condition: AI reasoning arrived BEFORE the Alpaca trade fill.
            # Buffer it so process_decision can pick it up.
            self._pending_reasoning[order_id] = reasoning
            logger.info(f"[ENGINE] Buffered early reasoning for session {self.session_id[:8]} (Order: {order_id})")
            return True
            
        for dev in self._deviation_records:
            if dev.decision_id in matching_decision_ids:
                dev.ai_reasoning = reasoning
                updated = True
                logger.info(f"[ENGINE] Updated reasoning for deviation in session {self.session_id[:8]} (Order: {order_id})")
        
        return updated

    def check_expired_actions(self) -> List[CompliantAction]:
        ts_ms = time.time() * 1000
        missed = []
        for action in self._action_store.get_all_actions(self.session_id):
            if action.lifecycle == ActionLifecycle.ACTIVE:
                if action.expiry_at and ts_ms > action.expiry_at:
                    action.miss(ts_ms)
                    missed.append(action)
        return missed

    def get_session_summary(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id, "playbook_id": self.playbook_id,
            "total_deviation_cost": self._total_deviation_cost,
            "total_unauthorized_gain": self._total_unauthorized_gain,
            "trade_count": self._trade_count,
            "deviation_count": len(self._deviation_records),
            "pending_finalization": self._finalization.get_pending_count(),
            "deviations_by_type": self._group_by_type(),
            "deviations_by_family": self._group_by_family(),
        }

    def get_all_deviations(self) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self._deviation_records]

    def get_all_actions(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._action_store.get_all_actions(self.session_id)]

    def _group_by_type(self) -> Dict[str, int]:
        g = {}
        for d in self._deviation_records:
            g[d.deviation_type.value] = g.get(d.deviation_type.value, 0) + 1
        return g

    def _group_by_family(self) -> Dict[str, float]:
        g = {}
        for d in self._deviation_records:
            cost = d.finalized_cost or d.candidate_cost or 0
            g[d.deviation_family.value] = g.get(d.deviation_family.value, 0) + cost
        return g

    def shutdown(self):
        logger.info(f"[ENGINE] Shutdown session={self.session_id[:8]} cost={self._total_deviation_cost:.2f}")
        self._action_store.clear_session(self.session_id)


class DeviationEngineRegistry:
    _engines: Dict[str, DeviationEngine] = {}

    @classmethod
    def create(cls, session_id: str, playbook_id: str, user_id: str,
               market_adapter: Optional[MarketAdapter] = None,
               default_expected_qty: Optional[float] = None) -> DeviationEngine:
        engine = DeviationEngine(session_id=session_id, playbook_id=playbook_id,
                                 user_id=user_id, market_adapter=market_adapter,
                                 default_expected_qty=default_expected_qty)
        cls._engines[session_id] = engine
        return engine

    @classmethod
    def get(cls, session_id: str) -> Optional[DeviationEngine]:
        return cls._engines.get(session_id)

    @classmethod
    def remove(cls, session_id: str):
        engine = cls._engines.pop(session_id, None)
        if engine:
            engine.shutdown()

    @classmethod
    def get_all(cls) -> Dict[str, DeviationEngine]:
        return dict(cls._engines)
