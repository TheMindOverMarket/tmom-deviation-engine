#!/usr/bin/env python3
"""
Run a scripted trade test plan against tmom-app-backend and tmom-deviation-engine.

This script is intentionally gated:
  - mode=mock uses backend /mock-trade
  - mode=paper uses backend /trade and requires ALLOW_PAPER_TRADES=true
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import certifi


def _build_http_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _default_output_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("artifacts") / f"deviation_test_report_{ts}.json"


def _load_steps(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("Orders file must be a JSON array of test steps.")
    for i, step in enumerate(payload):
        if "order" not in step or not isinstance(step["order"], dict):
            raise ValueError(f"Step index {i} is missing an 'order' object.")
        step.setdefault("test_id", f"step_{i+1}")
        step.setdefault("wait_before_seconds", 0.0)
    return payload


def _index_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["id"]: r for r in records if isinstance(r, dict) and "id" in r}


def _extract_changed_records(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    changed: List[Dict[str, Any]] = []
    tracked_fields = (
        "deviation_type",
        "candidate_cost",
        "finalized_cost",
        "unauthorized_gain",
        "finalized_at",
        "costability",
    )
    for rec_id, rec_after in after.items():
        rec_before = before.get(rec_id)
        if rec_before is None:
            changed.append(rec_after)
            continue
        for field in tracked_fields:
            if rec_before.get(field) != rec_after.get(field):
                changed.append(rec_after)
                break
    return changed


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"GET {url} failed ({resp.status}): {text}")
        return json.loads(text)


async def _post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: Dict[str, Any],
) -> Any:
    async with session.post(url, json=payload) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"POST {url} failed ({resp.status}): {text}")
        return json.loads(text)


async def _wait_for_active_action(
    session: aiohttp.ClientSession,
    deviation_url: str,
    session_id: str,
    timeout_seconds: float,
    poll_seconds: float = 1.0,
) -> Tuple[bool, int, Optional[Dict[str, Any]]]:
    deadline = time.time() + timeout_seconds
    actions_url = _build_http_url(deviation_url, f"/deviations/session/{session_id}/actions")
    checks = 0
    while time.time() < deadline:
        checks += 1
        actions = await _get_json(session, actions_url)
        for action in actions:
            if action.get("lifecycle") == "ACTIVE":
                return True, checks, action
        await asyncio.sleep(poll_seconds)
    return False, checks, None


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.mode == "paper":
        if os.getenv("ALLOW_PAPER_TRADES", "").lower() != "true":
            raise RuntimeError(
                "Paper trading is gated. Set ALLOW_PAPER_TRADES=true to run mode=paper."
            )
        trade_path = "/trade"
    else:
        trade_path = "/mock-trade"

    steps = _load_steps(Path(args.orders_file))

    deviation_start_url = _build_http_url(
        args.deviation_url,
        (
            f"/deviations/session/start?session_id={args.session_id}"
            f"&playbook_id={args.playbook_id}&user_id={args.user_id}"
        ),
    )
    deviation_summary_url = _build_http_url(
        args.deviation_url, f"/deviations/session/{args.session_id}/summary"
    )
    deviation_records_url = _build_http_url(
        args.deviation_url, f"/deviations/session/{args.session_id}/records"
    )
    deviation_stop_url = _build_http_url(
        args.deviation_url, f"/deviations/session/stop?session_id={args.session_id}"
    )
    trade_url = _build_http_url(args.backend_url, trade_path)

    report: Dict[str, Any] = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "backend_trade_url": trade_url,
        "deviation_url": args.deviation_url,
        "session_id": args.session_id,
        "playbook_id": args.playbook_id,
        "user_id": args.user_id,
        "steps": [],
    }

    timeout = aiohttp.ClientTimeout(total=60)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:
        if not args.skip_start:
            await _post_json(http, deviation_start_url, payload={})

        pre_records = await _get_json(http, deviation_records_url)
        pre_summary = await _get_json(http, deviation_summary_url)
        previous_index = _index_records(pre_records)

        for step in steps:
            wait_before = float(step.get("wait_before_seconds", 0))
            if wait_before > 0:
                await asyncio.sleep(wait_before)

            waited_for_action = None
            selected_action = None
            if step.get("require_active_action"):
                found, checks, action = await _wait_for_active_action(
                    http,
                    args.deviation_url,
                    args.session_id,
                    timeout_seconds=float(step.get("action_wait_timeout_seconds", 30)),
                )
                selected_action = action
                waited_for_action = {"required": True, "found": found, "checks": checks}
                if action is not None:
                    waited_for_action["action_id"] = action.get("id")
                    waited_for_action["action_activated_at"] = action.get("activated_at")

            # Optional deterministic timing anchor:
            # place order at action.activated_at + N seconds.
            if selected_action and step.get("wait_after_action_activation_seconds") is not None:
                activated_at_ms = selected_action.get("activated_at")
                if isinstance(activated_at_ms, (int, float)):
                    target_epoch_s = (activated_at_ms / 1000.0) + float(
                        step.get("wait_after_action_activation_seconds", 0)
                    )
                    sleep_for = max(0.0, target_epoch_s - time.time())
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                    if waited_for_action is None:
                        waited_for_action = {}
                    waited_for_action["anchored_wait_seconds"] = float(
                        step.get("wait_after_action_activation_seconds", 0)
                    )
                    waited_for_action["anchored_sleep_applied"] = sleep_for

            order_payload = step["order"]
            placed_at = time.time()
            trade_response = await _post_json(http, trade_url, order_payload)

            await asyncio.sleep(float(args.wait_after_seconds))

            summary = await _get_json(http, deviation_summary_url)
            records = await _get_json(http, deviation_records_url)
            current_index = _index_records(records)
            changed_records = _extract_changed_records(previous_index, current_index)
            previous_index = current_index

            report["steps"].append(
                {
                    "test_id": step["test_id"],
                    "notes": step.get("notes"),
                    "order": order_payload,
                    "placed_at_epoch_s": placed_at,
                    "trade_response": trade_response,
                    "waited_for_action": waited_for_action,
                    "changed_records": changed_records,
                    "session_totals": {
                        "total_deviation_cost": summary.get("total_deviation_cost"),
                        "total_unauthorized_gain": summary.get("total_unauthorized_gain"),
                        "trade_count": summary.get("trade_count"),
                        "deviation_count": summary.get("deviation_count"),
                        "pending_finalization": summary.get("pending_finalization"),
                    },
                }
            )

        report["summary_before"] = pre_summary
        report["summary_after"] = await _get_json(http, deviation_summary_url)

        if args.stop_session:
            report["stop_session_response"] = await _post_json(http, deviation_stop_url, payload={})

    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scripted BTC/USD test trades.")
    parser.add_argument("--orders-file", required=True, help="Path to JSON array of test steps.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--playbook-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--deviation-url", default="http://localhost:8100")
    parser.add_argument(
        "--backend-url",
        default=os.getenv("TMOM_BACKEND_BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument("--mode", choices=["mock", "paper"], default="mock")
    parser.add_argument("--wait-after-seconds", type=float, default=4.0)
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--stop-session", action="store_true")
    parser.add_argument(
        "--output-file",
        default=str(_default_output_path()),
        help="Output JSON report path.",
    )
    args = parser.parse_args()

    report = asyncio.run(run(args))
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote report: {output_path}")


if __name__ == "__main__":
    main()
