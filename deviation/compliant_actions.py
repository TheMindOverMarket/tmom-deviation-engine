"""
Deviation Engine — CompliantAction Store

Manages lifecycle. Enforces overlap policy (boundary #1):
  One active CompliantAction per (session_id, symbol, side, action_family).
"""

from __future__ import annotations
import logging
from typing import Optional, Dict, List, Tuple
from deviation.models import CompliantAction, ActionFamily, ActionLifecycle

logger = logging.getLogger(__name__)


class CompliantActionStore:
    def __init__(self):
        self._actions: Dict[str, CompliantAction] = {}
        self._active_index: Dict[Tuple[str, str, str, str], str] = {}

    def _make_key(self, session_id: str, symbol: str, side: str, family: ActionFamily) -> Tuple[str, str, str, str]:
        return (session_id, symbol.upper(), side.lower(), family.value)

    def add(self, action: CompliantAction) -> bool:
        key = self._make_key(action.session_id, action.symbol, action.side, action.action_family)
        if key in self._active_index and not action.allows_overlap:
            existing = self._actions.get(self._active_index[key])
            if existing and existing.lifecycle == ActionLifecycle.ACTIVE:
                logger.warning(f"[COMPLIANT_STORE] Overlap violation: {key}. Rejecting {action.id}.")
                return False
        self._actions[action.id] = action
        if action.lifecycle == ActionLifecycle.ACTIVE:
            self._active_index[key] = action.id
        return True

    def activate(self, action_id: str, ts_ms: float) -> Optional[CompliantAction]:
        action = self._actions.get(action_id)
        if not action:
            return None
        key = self._make_key(action.session_id, action.symbol, action.side, action.action_family)
        if key in self._active_index and self._active_index[key] != action_id:
            existing = self._actions.get(self._active_index[key])
            if existing and existing.lifecycle == ActionLifecycle.ACTIVE:
                existing.expire(ts_ms)
        action.activate(ts_ms)
        self._active_index[key] = action.id
        return action

    def find_active(self, session_id: str, symbol: str, side: str,
                    family: ActionFamily = ActionFamily.ENTER,
                    ts_ms: Optional[float] = None) -> Optional[CompliantAction]:
        key = self._make_key(session_id, symbol, side, family)
        action_id = self._active_index.get(key)
        if not action_id:
            return None
        action = self._actions.get(action_id)
        if not action:
            del self._active_index[key]
            return None
        if ts_ms and action.expiry_at and ts_ms > action.expiry_at:
            if action.lifecycle == ActionLifecycle.ACTIVE:
                action.miss(ts_ms)
            del self._active_index[key]
            return None
        if action.lifecycle != ActionLifecycle.ACTIVE:
            del self._active_index[key]
            return None
        return action

    def resolve(self, action_id: str, lifecycle: ActionLifecycle, ts_ms: float):
        action = self._actions.get(action_id)
        if not action:
            return
        action.lifecycle = lifecycle
        action.resolved_at = ts_ms
        key = self._make_key(action.session_id, action.symbol, action.side, action.action_family)
        if key in self._active_index and self._active_index[key] == action_id:
            del self._active_index[key]

    def get_all_actions(self, session_id: str) -> List[CompliantAction]:
        return [a for a in self._actions.values() if a.session_id == session_id]

    def get_action(self, action_id: str) -> Optional[CompliantAction]:
        return self._actions.get(action_id)

    def clear_session(self, session_id: str):
        to_remove = [aid for aid, a in self._actions.items() if a.session_id == session_id]
        for aid in to_remove:
            action = self._actions.pop(aid, None)
            if action:
                key = self._make_key(action.session_id, action.symbol, action.side, action.action_family)
                self._active_index.pop(key, None)
