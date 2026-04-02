# OKX Bot Platform (UI -> Backend API -> Python Worker)

This repository now includes an MVP for the architecture you requested:

- **Frontend UI (React + Vite)** for bot control and settings
- **Backend API (Python/Flask)** exposing bot/settings/tokens/telegram/logs/trades endpoints
- **Python Worker** reading runtime config from backend on each cycle
- **OKX API v5 integration** for market data + orders
- **SQLite persistence** for settings/tokens/trades/logs and open positions
- **Telegram integration** configurable via UI/API
- **WebSocket events** for real-time UI updates

> Scope note: trading integration is OKX **CEX API v5** (API key based).  
> OKX DEX / wallet-based flow is different and not included in this MVP.

---

## Implemented architecture

```text
[ React UI ] <----HTTP/WS----> [ Backend API ]
                                   |
                                   +---- controls worker process
                                   |
                                   +---- [ SQLite ]
                                   |
[ Python Worker ] ----HTTP------> [ Backend API runtime config ]
       |
       +----> [ OKX API ]
       +----> [ Telegram Bot API ]
```

Also included in `docker-compose.yml`:

- `frontend`
- `backend`
- `worker`
- `redis` (ready for future status-flag/event use)
- `postgres` (ready for future migration from SQLite)

---

## API endpoints

### Bot control

- `POST /api/bot/start`
- `POST /api/bot/stop`
- `POST /api/bot/restart`
- `GET /api/bot/status`

### Settings

- `GET /api/settings`
- `PUT /api/settings`

### Tokens

- `GET /api/tokens`
- `POST /api/tokens`
- `PUT /api/tokens/:id`
- `DELETE /api/tokens/:id`

### Telegram

- `POST /api/telegram/test`

### Logs / trades / runtime

- `GET /api/logs`
- `GET /api/trades`
- `GET /api/runtime/config`
- `GET /api/ui/overview`

### Worker event ingestion

- `POST /api/events/trade`
- `POST /api/events/error`
- `POST /api/events/heartbeat`

### Real-time

- `WS /ws/events`
- `POST /api/ws/publish` (manual custom event publish)

---

## UI pages included (MVP)

Frontend path: `frontend/`

- **Bot Control Panel**
  - Start / Stop / Restart
  - Status + PID + last run + active trade count
- **Strategy Settings**
  - Trade amount, max active trades, poll interval, dry-run, etc.
- **Token Management**
  - list/add/delete tokens (replaces config.json at runtime)
- **Telegram Settings**
  - token/chat id + test notification
- **Logs & Trades**
  - latest data pulled from API
- **Chart panel placeholder**
  - section prepared for TradingView integration

---

## Quick start (Docker)

1) Copy env files:

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env
```

2) Fill `.env` with your OKX credentials and optional Telegram credentials.

3) Start all services:

```bash
docker compose up --build
```

4) Open:

- UI: `http://localhost:5173`
- Backend API: `http://localhost:8080/api/health`

5) In UI, click **Start** bot.

---

## Local run without Docker

### Backend API

```bash
python3 -m pip install -r requirements.txt
python3 backend_api.py
```

### Worker (separate terminal)

```bash
python3 okx_worker.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Runtime behavior

- Worker fetches runtime config from backend (`/api/runtime/config`) every cycle.
- Changing settings/tokens from UI/API is reflected without editing Python source.
- Backend start/stop controls worker process.
- Trade and error events are persisted and emitted to websocket subscribers.

---

## Key files

- `backend_api.py` - backend process manager + API server bootstrap
- `okx_worker.py` - worker loop controlled by backend state
- `okx_bot/control_api.py` - API routes + SQLite control store + websocket
- `okx_bot/engine.py` - signal/risk/execution engine with event hooks
- `okx_bot/client.py` - OKX request signing + retry logic
- `okx_bot/telegram.py` - Telegram notifications
- `frontend/src/App.tsx` - MVP control UI
- `docker-compose.yml` - full local stack

---

## Safety reminders

- Keep `dry_run=1` in settings until strategy behavior is validated.
- Use `okx_simulated=1` for simulated trading mode.
- Validate per-symbol lot size/tick size constraints before live deployment.

## Legacy CLI

The original standalone runner (`bot.py` with `config.json`) is still present for quick local testing.