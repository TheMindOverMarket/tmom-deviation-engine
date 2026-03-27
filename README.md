# TMOM Deviation Engine

## Overview

Standalone microservice that supervises trading sessions and computes **deviation costs** — the economic delta between what a trader actually did vs. what their playbook said to do.

**This service does NOT modify the backend, frontend, or Rule Engine code.** It communicates with them via HTTP and WebSocket.

## Architecture

```
┌─────────────────────┐    WS: /ws/market-state     ┌────────────────────┐
│                     │◄─────────────────────────────│                    │
│  tmom-app-backend   │    WS: /ws/user-activity     │  Deviation Engine  │
│  (Vallab)           │◄─────────────────────────────│  (You)             │
│                     │    WS: /ws/engine-output     │                    │
│                     │◄─────────────────────────────│                    │
│                     │    HTTP: POST /sessions/events│                    │
│                     │◄─────────────────────────────│                    │
└─────────────────────┘                              └────────┬───────────┘
                                                              │
                                                     REST: /deviations/*
                                                     WS: /ws/deviation-output
                                                              │
                                                     ┌────────▼───────────┐
                                                     │  tmom-app-frontend │
                                                     │  (Vallab)          │
                                                     └────────────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env

# 3. Run the server
python server.py
```

The server starts on `http://localhost:8100` and automatically connects to the backend's WebSocket streams.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service status |
| GET | `/health` | Health check with active engine count |
| GET | `/deviations/session/{id}/summary` | Session deviation cost summary |
| GET | `/deviations/session/{id}/records` | All deviation records for a session |
| GET | `/deviations/session/{id}/actions` | All CompliantActions for a session |
| GET | `/deviations/active-engines` | List active engines |
| POST | `/deviations/session/start` | Start engine for a session |
| POST | `/deviations/session/stop` | Stop engine and persist summary |
| WS | `/ws/deviation-output` | Real-time deviation event stream |

## Data Flow

1. **Rule Engine triggers** → Backend broadcasts via `/ws/engine-output` → Deviation Engine creates a `CompliantAction`
2. **Trader fills** → Backend broadcasts via `/ws/user-activity` → Deviation Engine matches against actions, attributes deviations, computes costs
3. **Position closes** → FinalizationWorker resolves deferred costs (timing, invalid trades)
4. **Results** → Persisted to backend via HTTP, broadcast to frontend via `/ws/deviation-output`

## Project Structure

```
tmom-deviation-engine/
├── server.py                  # FastAPI entry point
├── config.py                  # Environment configuration
├── requirements.txt           # Python dependencies
├── deviation/                 # Core engine logic
│   ├── models.py              # Domain types & enums
│   ├── market_adapter.py      # Market price abstraction
│   ├── compliant_actions.py   # Expected action lifecycle
│   ├── matcher.py             # Decision-to-action matching
│   ├── attributor.py          # Hierarchy-based attribution
│   ├── position_tracker.py    # FIFO lot matching & PnL
│   ├── finalization.py        # Deferred cost resolution
│   ├── explainability.py      # Deterministic audit payloads
│   └── engine.py              # Main orchestrator + registry
├── clients/                   # External service communication
│   ├── stream_clients.py      # WebSocket consumers
│   └── backend_client.py      # HTTP client for persistence
└── api/                       # REST API layer
    └── router.py              # FastAPI routes
```
