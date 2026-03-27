"""
Deviation Engine — Core Domain Models & Types

Pure domain objects. These are NOT DB models — they live in memory during
a session and are persisted via the backend's SessionEvent API.

All boundaries locked from Deviation_engine_build.pdf.
"""

from __future__ import annotations
import uuid
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


# ─── Enums ───────────────────────────────────────────────────────────

class ActionFamily(str, Enum):
    ENTER = "ENTER"
    EXIT = "EXIT"
    ADD = "ADD"
    REDUCE = "REDUCE"
    NO_TRADE = "NO_TRADE"


class ActionLifecycle(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    TAKEN = "TAKEN"
    MISSED = "MISSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ExpiryPolicy(str, Enum):
    INSTANT = "INSTANT"
    WINDOW_N_SECONDS = "WINDOW_N_SECONDS"
    UNTIL_CONDITIONS_FALSE = "UNTIL_CONDITIONS_FALSE"


class DeviationType(str, Enum):
    INVALID_TRADE = "INVALID_TRADE"
    EARLY_ENTRY = "EARLY_ENTRY"
    LATE_ENTRY = "LATE_ENTRY"
    MISSED_TRADE = "MISSED_TRADE"
    OVERSIZE = "OVERSIZE"
    UNDERSIZE = "UNDERSIZE"
    COOLDOWN_VIOLATION = "COOLDOWN_VIOLATION"
    PYRAMIDING_VIOLATION = "PYRAMIDING_VIOLATION"
    DAILY_LOSS_CAP_BREACH = "DAILY_LOSS_CAP_BREACH"
    MAX_POSITIONS_BREACH = "MAX_POSITIONS_BREACH"


class DeviationFamily(str, Enum):
    VALIDITY = "VALIDITY"
    TIMING = "TIMING"
    SIZE = "SIZE"
    RISK_PROCESS = "RISK_PROCESS"


class Costability(str, Enum):
    NONE = "NONE"
    PROVISIONAL = "PROVISIONAL"
    FINAL_IMMEDIATE = "FINAL_IMMEDIATE"
    FINAL_DEFERRED = "FINAL_DEFERRED"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ─── Core Domain Objects ─────────────────────────────────────────────

@dataclass
class CompliantAction:
    """
    A frozen, session-scoped expected action generated when the rule engine
    detects that conditions are met.
    Overlap policy: one active per (session_id, symbol, side, action_family).
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    playbook_id: str = ""
    symbol: str = ""
    side: str = ""
    action_family: ActionFamily = ActionFamily.ENTER
    lifecycle: ActionLifecycle = ActionLifecycle.PENDING
    created_at: float = 0.0
    activated_at: Optional[float] = None
    resolved_at: Optional[float] = None
    expiry_policy: ExpiryPolicy = ExpiryPolicy.WINDOW_N_SECONDS
    expiry_seconds: float = 60.0
    expiry_at: Optional[float] = None
    canonical_price_at_creation: Optional[float] = None
    rule_evaluation_snapshot: Optional[Dict[str, Any]] = None
    triggered_rule_ids: List[str] = field(default_factory=list)
    composite_group_id: Optional[str] = None
    allows_overlap: bool = False
    expected_qty: Optional[float] = None
    sizing_mode: Optional[str] = None
    sizing_params: Optional[Dict[str, Any]] = None

    def activate(self, ts_ms: float):
        self.lifecycle = ActionLifecycle.ACTIVE
        self.activated_at = ts_ms
        if self.expiry_policy == ExpiryPolicy.WINDOW_N_SECONDS:
            self.expiry_at = ts_ms + (self.expiry_seconds * 1000)

    def take(self, ts_ms: float):
        self.lifecycle = ActionLifecycle.TAKEN
        self.resolved_at = ts_ms

    def miss(self, ts_ms: float):
        self.lifecycle = ActionLifecycle.MISSED
        self.resolved_at = ts_ms

    def expire(self, ts_ms: float):
        self.lifecycle = ActionLifecycle.EXPIRED
        self.resolved_at = ts_ms

    def is_active(self, ts_ms: float) -> bool:
        if self.lifecycle != ActionLifecycle.ACTIVE:
            return False
        if self.expiry_at and ts_ms > self.expiry_at:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "session_id": self.session_id, "playbook_id": self.playbook_id,
            "symbol": self.symbol, "side": self.side,
            "action_family": self.action_family.value, "lifecycle": self.lifecycle.value,
            "created_at": self.created_at, "activated_at": self.activated_at,
            "resolved_at": self.resolved_at,
            "canonical_price_at_creation": self.canonical_price_at_creation,
            "triggered_rule_ids": self.triggered_rule_ids, "expected_qty": self.expected_qty,
        }


@dataclass
class DecisionEvent:
    """An observed user fill that needs to be matched against CompliantActions."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_id: str = ""
    symbol: str = ""
    side: str = ""
    qty: float = 0.0
    filled_qty: float = 0.0
    price: Optional[float] = None
    order_id: str = ""
    timestamp_ms: float = 0.0
    canonical_bid: Optional[float] = None
    canonical_ask: Optional[float] = None
    canonical_mid: Optional[float] = None
    market_attachment_state: Optional[str] = None
    market_snapshot_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "session_id": self.session_id, "symbol": self.symbol,
            "side": self.side, "qty": self.qty, "filled_qty": self.filled_qty,
            "price": self.price, "order_id": self.order_id,
            "timestamp_ms": self.timestamp_ms, "canonical_mid": self.canonical_mid,
        }


@dataclass
class SizeSnapshot:
    """Persisted at decision time. Never recomputed."""
    q_actual: float = 0.0
    q_allow_final: float = 0.0
    q_excess: float = 0.0
    r_used: float = 0.0
    equity_e: float = 0.0
    atr: Optional[float] = None


@dataclass
class DeviationRecord:
    """Attribution record linking a deviation to a decision and CompliantAction."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    decision_id: Optional[str] = None
    compliant_action_id: Optional[str] = None
    deviation_type: DeviationType = DeviationType.INVALID_TRADE
    deviation_family: DeviationFamily = DeviationFamily.VALIDITY
    costability: Costability = Costability.NONE
    severity: Severity = Severity.MEDIUM
    candidate_cost: Optional[float] = None
    finalized_cost: Optional[float] = None
    unauthorized_gain: Optional[float] = None
    canonical_price_expected: Optional[float] = None
    canonical_price_actual: Optional[float] = None
    price_delta: Optional[float] = None
    size_snapshot: Optional[SizeSnapshot] = None
    detected_at: float = 0.0
    finalized_at: Optional[float] = None
    explainability_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "session_id": self.session_id,
            "decision_id": self.decision_id, "compliant_action_id": self.compliant_action_id,
            "deviation_type": self.deviation_type.value, "deviation_family": self.deviation_family.value,
            "costability": self.costability.value, "severity": self.severity.value,
            "candidate_cost": self.candidate_cost, "finalized_cost": self.finalized_cost,
            "unauthorized_gain": self.unauthorized_gain, "price_delta": self.price_delta,
            "detected_at": self.detected_at, "finalized_at": self.finalized_at,
            "explainability_payload": self.explainability_payload,
        }


@dataclass
class ExecutionBucket:
    """Groups fills from a single decision into a VWAP-weighted entry."""
    decision_id: str = ""
    symbol: str = ""
    side: str = ""
    fills: List[Dict[str, Any]] = field(default_factory=list)
    total_qty: float = 0.0
    vwap_price: float = 0.0
    open_qty: float = 0.0

    def add_fill(self, qty: float, price: float, fill_id: str = ""):
        self.fills.append({"fill_id": fill_id, "qty": qty, "price": price})
        cost_before = self.vwap_price * self.total_qty
        self.total_qty += qty
        self.open_qty += qty
        if self.total_qty > 0:
            self.vwap_price = (cost_before + qty * price) / self.total_qty


@dataclass
class PositionLot:
    """A single tranche linked to exactly one decision. FIFO close attribution."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    decision_id: str = ""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    qty: float = 0.0
    remaining_qty: float = 0.0
    is_excess: bool = False
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "decision_id": self.decision_id,
            "symbol": self.symbol, "side": self.side,
            "entry_price": self.entry_price, "qty": self.qty,
            "remaining_qty": self.remaining_qty, "is_excess": self.is_excess,
        }


@dataclass
class SessionPolicy:
    """Locked defaults from PDF boundary #4."""
    requires_flat_at_end: bool = False
    inherit_open_positions: bool = True
    inherit_intraday_counters: bool = False


# ─── Mappings ──────────────────────────────────────────────────────────

DEVIATION_FAMILY_MAP: Dict[DeviationType, DeviationFamily] = {
    DeviationType.INVALID_TRADE: DeviationFamily.VALIDITY,
    DeviationType.EARLY_ENTRY: DeviationFamily.TIMING,
    DeviationType.LATE_ENTRY: DeviationFamily.TIMING,
    DeviationType.MISSED_TRADE: DeviationFamily.TIMING,
    DeviationType.OVERSIZE: DeviationFamily.SIZE,
    DeviationType.UNDERSIZE: DeviationFamily.SIZE,
    DeviationType.COOLDOWN_VIOLATION: DeviationFamily.RISK_PROCESS,
    DeviationType.PYRAMIDING_VIOLATION: DeviationFamily.RISK_PROCESS,
    DeviationType.DAILY_LOSS_CAP_BREACH: DeviationFamily.RISK_PROCESS,
    DeviationType.MAX_POSITIONS_BREACH: DeviationFamily.RISK_PROCESS,
}

COSTABILITY_MAP: Dict[DeviationType, Costability] = {
    DeviationType.INVALID_TRADE: Costability.FINAL_DEFERRED,
    DeviationType.EARLY_ENTRY: Costability.FINAL_DEFERRED,
    DeviationType.LATE_ENTRY: Costability.FINAL_DEFERRED,
    DeviationType.MISSED_TRADE: Costability.NONE,
    DeviationType.OVERSIZE: Costability.FINAL_IMMEDIATE,
    DeviationType.UNDERSIZE: Costability.NONE,
    DeviationType.COOLDOWN_VIOLATION: Costability.NONE,
    DeviationType.PYRAMIDING_VIOLATION: Costability.NONE,
    DeviationType.DAILY_LOSS_CAP_BREACH: Costability.NONE,
    DeviationType.MAX_POSITIONS_BREACH: Costability.NONE,
}
