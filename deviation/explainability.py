"""
Deviation Engine — ExplainabilityBuilder

Locked boundary #3: One normalized ExplainabilityPayload per deviation.
LLM only narrates from deterministic payload — never invents.
"""

from __future__ import annotations
import logging
from typing import Optional, Dict, Any
from deviation.models import DeviationRecord, CompliantAction, DecisionEvent

logger = logging.getLogger(__name__)


class ExplainabilityBuilder:
    def build(
        self, record: DeviationRecord,
        decision: Optional[DecisionEvent] = None,
        action: Optional[CompliantAction] = None,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "playbook_id": action.playbook_id if action else None,
            "session_id": record.session_id,
            "compliant_action_id": record.compliant_action_id,
            "decision_id": record.decision_id,
            "deviation_id": record.id,
            "summary": {
                "action_family": action.action_family.value if action else None,
                "deviation_type": record.deviation_type.value,
                "deviation_family": record.deviation_family.value,
                "severity": record.severity.value,
                "costability": record.costability.value,
                "matched_vs_expected": self._describe_match(record, decision, action),
            },
            "provenance": {
                "static_rule_ids": action.triggered_rule_ids if action else [],
            },
            "evaluation_context": {
                "canonical_price_expected": record.canonical_price_expected,
                "canonical_price_actual": record.canonical_price_actual,
                "price_delta": record.price_delta,
                "decision_symbol": decision.symbol if decision else None,
                "decision_side": decision.side if decision else None,
                "decision_qty": decision.qty if decision else None,
                "decision_price": decision.price if decision else None,
                "decision_timestamp_ms": decision.timestamp_ms if decision else None,
            },
            "cost_summary": {
                "candidate_cost": record.candidate_cost,
                "finalized_cost": record.finalized_cost,
                "unauthorized_gain": record.unauthorized_gain,
            },
        }
        if record.size_snapshot:
            payload["size_context"] = {
                "q_actual": record.size_snapshot.q_actual,
                "q_allow_final": record.size_snapshot.q_allow_final,
                "q_excess": record.size_snapshot.q_excess,
            }
        if additional_context:
            payload["additional_context"] = additional_context
        record.explainability_payload = payload
        return payload

    @staticmethod
    def _describe_match(record, decision, action) -> str:
        if not action:
            return (
                f"Trader executed a {decision.side if decision else '?'} "
                f"for {decision.symbol if decision else '?'} "
                f"with no corresponding expected action."
            )
        dt = record.deviation_type.value.replace("_", " ").lower()
        return (
            f"Expected {action.action_family.value.lower()} action for "
            f"{action.symbol} {action.side}, detected {dt}. "
            f"Price delta: {record.price_delta or 'N/A'}."
        )

    @staticmethod
    def narration_prompt(payload: Dict[str, Any]) -> str:
        summary = payload.get("summary", {})
        cost = payload.get("cost_summary", {})
        ctx = payload.get("evaluation_context", {})
        return (
            f"Explain the following trading deviation to a trader:\n"
            f"- Type: {summary.get('deviation_type', 'unknown')}\n"
            f"- Severity: {summary.get('severity', 'unknown')}\n"
            f"- Symbol: {ctx.get('decision_symbol', '?')}\n"
            f"- Side: {ctx.get('decision_side', '?')}\n"
            f"- Expected price: {ctx.get('canonical_price_expected', 'N/A')}\n"
            f"- Actual price: {ctx.get('canonical_price_actual', 'N/A')}\n"
            f"- Current cost: {cost.get('finalized_cost') or cost.get('candidate_cost', 'pending')}\n"
            f"- What happened: {summary.get('matched_vs_expected', '')}\n"
            f"Explain clearly and concisely. Do not speculate beyond the given data."
        )
