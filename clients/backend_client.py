"""
Deviation Engine — Backend HTTP Client

Communicates with Vallab's tmom-app-backend for:
1. Persisting deviation events via the /sessions/{id}/events endpoint
2. Fetching session metadata
3. No direct DB access — all through the backend's REST API
"""

from __future__ import annotations
import logging
from typing import Dict, Any, Optional
import aiohttp
from config import settings

logger = logging.getLogger(__name__)


class BackendClient:
    """Async HTTP client for the tmom-app-backend."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.BACKEND_BASE_URL).rstrip("/")

    async def persist_deviation_event(
        self, session_id: str, deviation_data: Dict[str, Any],
    ) -> bool:
        """POST a deviation event to the backend's session events endpoint."""
        url = f"{self.base_url}/sessions/{session_id}/events"
        payload = {
            "type": "DEVIATION",
            "event_data": deviation_data,
            "event_metadata": {
                "source": "deviation_engine",
                "channel": "deviation_output",
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"accept": "application/json"}) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"[BACKEND_CLIENT] Persisted deviation for session {session_id[:8]}")
                        return True
                    err = await resp.text()
                    logger.warning(f"[BACKEND_CLIENT] Failed to persist: {resp.status} {err}")
                    return False
        except Exception as e:
            logger.error(f"[BACKEND_CLIENT] Error persisting deviation: {e}")
            return False

    async def persist_deviation_summary(
        self, session_id: str, summary: Dict[str, Any],
    ) -> bool:
        """POST the session-level deviation summary at session end."""
        url = f"{self.base_url}/sessions/{session_id}/events"
        payload = {
            "type": "SYSTEM",
            "event_data": {"action": "DEVIATION_SUMMARY", **summary},
            "event_metadata": {
                "source": "deviation_engine",
                "channel": "deviation_summary",
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"accept": "application/json"}) as resp:
                    if resp.status in (200, 201):
                        return True
                    return False
        except Exception as e:
            logger.error(f"[BACKEND_CLIENT] Error persisting summary: {e}")
            return False

    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Fetch session metadata from the backend."""
        url = f"{self.base_url}/sessions/{session_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception as e:
            logger.error(f"[BACKEND_CLIENT] Error fetching session: {e}")
            return None

    async def get_playbook_info(self, playbook_id: str) -> Optional[Dict[str, Any]]:
        """Fetch playbook metadata from the backend."""
        url = f"{self.base_url}/playbooks/{playbook_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception as e:
            logger.error(f"[BACKEND_CLIENT] Error fetching playbook: {e}")
            return None
