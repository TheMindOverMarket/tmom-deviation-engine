"""
Deviation Engine — REST API Router

Exposes deviation data for frontend consumption.
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
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
