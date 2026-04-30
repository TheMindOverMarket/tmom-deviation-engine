"""
Deviation Engine — WebSocket Stream Clients

Subscribes to the backend's market/activity streams plus the Rule Engine's
engine-output stream. Three stream consumers:

1. Market State   → feeds the MarketAdapter with canonical pricing
2. User Activity  → processes fills through the deviation engine
3. Engine Output  → creates CompliantActions when the Rule Engine triggers
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any, Callable, Awaitable
import websockets  # type: ignore

from config import settings
from deviation.engine import DeviationEngineRegistry
from deviation.market_adapter import LiveMarketAdapter
from deviation.models import ActionFamily, ExpiryPolicy
from clients.backend_client import BackendClient

logger = logging.getLogger(__name__)

# Shared market adapter across all engines
global_market_adapter = LiveMarketAdapter()
backend_client = BackendClient()


def _extract_position_context(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pass risk/process context through when upstream provides it."""
    context = data.get("position_context") or data.get("risk_context")
    if isinstance(context, dict):
        return context

    keys = (
        "cooldown_violated",
        "pyramiding_violated",
        "daily_loss_cap_breached",
        "max_positions_breached",
    )
    extracted = {key: data[key] for key in keys if key in data}
    return extracted or None


# ─── Generic WS Listener ─────────────────────────────────────────────

async def _ws_listener(
    url: str,
    handler: Callable[[str], Awaitable[None]],
    name: str,
    reconnect_delay: float = 3.0,
):
    """Generic reconnecting WebSocket listener."""
    while True:
        try:
            logger.info(f"[{name}] Connecting to {url}...")
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info(f"[{name}] Connected.")
                async for message in ws:
                    try:
                        await handler(message)
                    except Exception as e:
                        logger.error(f"[{name}] Handler error: {e}")
        except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            logger.warning(f"[{name}] Disconnected ({e}). Reconnecting in {reconnect_delay}s...")
        except Exception as e:
            logger.error(f"[{name}] Unexpected error: {e}")
        await asyncio.sleep(reconnect_delay)


# ─── Market State Stream ─────────────────────────────────────────────

async def _market_state_handler(msg: str):
    """Process market state updates from the backend broadcast."""
    try:
        data = json.loads(msg)
        if isinstance(data, dict) and data.get("message") == "unauthorized.":
            return

        symbol = data.get("symbol", "")
        if symbol:
            global_market_adapter.update_state(symbol, data)

        # Check for expired actions on every tick
        for engine in DeviationEngineRegistry.get_all().values():
            engine.check_expired_actions()

    except json.JSONDecodeError:
        pass


async def start_market_stream(session_id: Optional[str] = None, user_id: Optional[str] = None):
    """Subscribe to the backend's market-state WebSocket."""
    url = f"{settings.backend_ws_url}/ws/market-state"
    params = []
    if session_id:
        params.append(f"session_id={session_id}")
    if user_id:
        params.append(f"user_id={user_id}")
    if params:
        url += "?" + "&".join(params)

    await _ws_listener(url, _market_state_handler, "MARKET_STREAM")


# ─── User Activity Stream ────────────────────────────────────────────

async def _user_activity_handler(msg: str):
    """Process fill events from the backend's user-activity stream."""
    try:
        data = json.loads(msg)
        if isinstance(data, dict) and data.get("message") == "unauthorized.":
            return

        # Normalize: might be a JSON string inside a string
        if isinstance(data, str):
            data = json.loads(data)

        event_type = data.get("alpaca_event_type", "")
        if event_type not in ("fill", "partial_fill"):
            return

        symbol = data.get("symbol", "")
        side = data.get("side", "")
        qty = float(data.get("qty", 0))
        filled_qty = float(data.get("filled_qty", 0))
        price = data.get("price")
        if price is not None:
            price = float(price)
        order_id = data.get("order_id", "")
        timestamp_ms = data.get("timestamp_server", time.time() * 1000)
        session_id = data.get("session_id")
        user_id = data.get("user_id")

        # Only process activity that can be scoped to a specific session or user.
        target_engines: list[tuple[str, Any]] = []
        if session_id:
            engine = DeviationEngineRegistry.get(session_id)
            if engine:
                target_engines.append((session_id, engine))
        elif user_id:
            target_engines.extend(
                (active_session_id, engine)
                for active_session_id, engine in DeviationEngineRegistry.get_all().items()
                if engine.user_id == user_id
            )
        else:
            logger.warning("[ACTIVITY_STREAM] Skipping unscoped activity event without session_id/user_id.")
            return

        for target_session_id, engine in target_engines:
            result = engine.process_decision(
                symbol=symbol, side=side, qty=qty, filled_qty=filled_qty,
                price=price, order_id=order_id, timestamp_ms=timestamp_ms,
                market_attachment_state=data.get("market_attachment_state"),
                position_context=_extract_position_context(data),
            )

            # Persist deviation events to backend
            if result and result.get("deviations"):
                for dev in result["deviations"]:
                    await backend_client.persist_deviation_event(target_session_id, dev)

            # Broadcast result via our own output registry
            from server import deviation_output_registry
            await deviation_output_registry.broadcast(
                {"type": "deviation_result", "session_id": target_session_id, "data": result},
                session_id=target_session_id,
            )

    except json.JSONDecodeError:
        pass
    except Exception as e:
        logger.error(f"[ACTIVITY_STREAM] Error: {e}")


async def start_activity_stream(session_id: Optional[str] = None, user_id: Optional[str] = None):
    """Subscribe to the backend's user-activity WebSocket."""
    url = f"{settings.backend_ws_url}/ws/user-activity"
    params = []
    if session_id:
        params.append(f"session_id={session_id}")
    if user_id:
        params.append(f"user_id={user_id}")
    if params:
        url += "?" + "&".join(params)

    await _ws_listener(url, _user_activity_handler, "ACTIVITY_STREAM")


# ─── Engine Output Stream ────────────────────────────────────────────

async def _engine_output_handler(msg: str):
    """
    Process engine output from the backend's engine-output stream.
    When rule_triggered=True, create a CompliantAction.
    """
    try:
        data = json.loads(msg)
        if isinstance(data, dict) and data.get("message") == "unauthorized.":
            return

        rule_triggered = data.get("rule_triggered", False)
        session_id = data.get("session_id")
        symbol = data.get("symbol")
        price = data.get("price")

        if not session_id:
            return

        engine = DeviationEngineRegistry.get(session_id)
        if not engine:
            return

        if not rule_triggered:
            engine.check_expired_actions()
            return

        if not symbol:
            logger.warning("[ENGINE_OUTPUT] Missing symbol in engine output")
            return

        triggered_entry_ids = data.get("triggered_entries", [])
        side = data.get("side", "buy")

        engine.register_compliant_action(
            symbol=symbol, side=side,
            triggered_rule_ids=triggered_entry_ids,
            canonical_price=price,
            expiry_policy=ExpiryPolicy.WINDOW_N_SECONDS,
            expiry_seconds=settings.DEFAULT_EXPIRY_WINDOW,
            action_family=ActionFamily.ENTER,
            rule_evaluation_snapshot=data.get("rule_evaluations"),
        )

        # 🚀 REASONING UPDATE 🚀
        # If the Rule Engine sends a deviation signal with reasoning, capture it.
        deviation = data.get("deviation", False)
        ai_reasoning = data.get("ai_reasoning")
        order_id = data.get("order_id")

        if deviation and order_id and ai_reasoning:
            if engine.update_deviation_reasoning(order_id, ai_reasoning):
                # Re-broadcast updated records to frontend for real-time swap
                updated_records = [d.to_dict() for d in engine._deviation_records if d.decision_id in [dec.id for dec in engine._decision_events if dec.order_id == order_id]]
                
                from server import deviation_output_registry
                await deviation_output_registry.broadcast(
                    {
                        "type": "deviation_result", 
                        "session_id": session_id, 
                        "data": {
                            "deviations": updated_records,
                            "session_totals": engine.get_session_summary()
                        }
                    },
                    session_id=session_id,
                )

    except json.JSONDecodeError:
        pass
    except Exception as e:
        logger.error(f"[ENGINE_OUTPUT] Error: {e}")


async def start_engine_output_stream(session_id: Optional[str] = None, user_id: Optional[str] = None):
    """Subscribe to the Rule Engine's engine-output WebSocket."""
    url = f"{settings.rule_engine_ws_url}/ws/engine-output"
    params = []
    if session_id:
        params.append(f"session_id={session_id}")
    if user_id:
        params.append(f"user_id={user_id}")
    if params:
        url += "?" + "&".join(params)

    await _ws_listener(url, _engine_output_handler, "ENGINE_OUTPUT")
