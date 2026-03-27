"""
Deviation Engine — REST API Router

Exposes deviation data for frontend consumption.
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import time
from deviation.engine import DeviationEngineRegistry

router = APIRouter(prefix="/deviations", tags=["deviations"])


@router.get("/session/{session_id}/summary")
def get_session_summary(session_id: str) -> Dict[str, Any]:
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No active engine for this session")
    return engine.get_session_summary()


@router.get("/session/{session_id}/records")
def get_session_records(session_id: str) -> List[Dict[str, Any]]:
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No active engine for this session")
    return engine.get_all_deviations()


@router.get("/session/{session_id}/actions")
def get_session_actions(session_id: str) -> List[Dict[str, Any]]:
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No active engine for this session")
    return engine.get_all_actions()


@router.get("/active-engines")
def list_active_engines() -> Dict[str, Any]:
    engines = DeviationEngineRegistry.get_all()
    return {
        "count": len(engines),
        "sessions": [
            {
                "session_id": sid,
                "playbook_id": eng.playbook_id,
                "trade_count": eng._trade_count,
                "total_deviation_cost": eng._total_deviation_cost,
            }
            for sid, eng in engines.items()
        ],
    }


@router.post("/session/start")
def start_session(session_id: str, playbook_id: str, user_id: str) -> Dict[str, Any]:
    """Manually start a deviation engine for a session (called by backend or testing)."""
    from clients.stream_clients import global_market_adapter

    existing = DeviationEngineRegistry.get(session_id)
    if existing:
        return {"status": "already_running", "session_id": session_id}

    engine = DeviationEngineRegistry.create(
        session_id=session_id, playbook_id=playbook_id,
        user_id=user_id, market_adapter=global_market_adapter,
    )
    return {"status": "started", "session_id": session_id, "playbook_id": playbook_id}


@router.post("/session/stop")
async def stop_session(session_id: str) -> Dict[str, Any]:
    """Stop a deviation engine and persist summary to backend."""
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        raise HTTPException(status_code=404, detail="No active engine for this session")

    summary = engine.get_session_summary()

    # Persist summary to backend
    from clients.backend_client import BackendClient
    client = BackendClient()
    await client.persist_deviation_summary(session_id, summary)

    DeviationEngineRegistry.remove(session_id)
    return {"status": "stopped", "summary": summary}


# ─── Mock Simulation Endpoints (for dev/testing) ──────────────────────

class MockRuleSignal(BaseModel):
    symbol: str
    side: str
    price: float
    rule_ids: List[str]
    expected_qty: Optional[float] = 1.0

class MockTraderFill(BaseModel):
    symbol: str
    side: str
    qty: float
    price: float

@router.post("/simulation/rule-signal")
async def mock_rule_signal(data: MockRuleSignal, session_id: str = "smoke-test"):
    """Simulate a Rule Engine signal firing."""
    from clients.stream_clients import global_market_adapter
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        # Auto-create for session
        engine = DeviationEngineRegistry.create(session_id, "pb-1", "user-1", global_market_adapter)
    
    action = engine.register_compliant_action(
        data.symbol, data.side, data.rule_ids, 
        canonical_price=data.price, expected_qty=data.expected_qty
    )
    return {"status": "matched", "action_id": action.id}


@router.post("/simulation/trader-fill")
async def mock_trader_fill(data: MockTraderFill, session_id: str = "smoke-test"):
    """Simulate a trader execution/fill."""
    from deviation.engine import DeviationEngineRegistry
    from server import deviation_output_registry
    
    engine = DeviationEngineRegistry.get(session_id)
    if not engine:
        return {"error": "No active engine for this session"}
    
    result = engine.process_decision(data.symbol, data.side, data.qty, data.qty, data.price, f"mock-trade-{int(time.time())}")
    
    # Broadcast to frontend
    await deviation_output_registry.broadcast({
        "type": "deviation_result",
        "data": result
    }, session_id=session_id)
    
    return {"status": "processed", "result": result}


