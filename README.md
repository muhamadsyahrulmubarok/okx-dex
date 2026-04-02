# OKX Bot Platform (UI -> Backend API -> Python Worker)

This repository now includes an MVP for the architecture you requested:

- **Frontend UI (React + Vite)** for bot control and settings
- **Backend API (Python/Flask)** exposing bot/settings/tokens/telegram/logs/trades endpoints
- **Python Worker** reading runtime config from backend on each cycle
- **Dual exchange mode support**:
  - **CEX mode** (OKX API v5 spot instrument pairs, e.g. `ETH-USDT`)
  - **DEX mode** (OKX Web3 DEX aggregator for contract-address tokens)
- **SQLite persistence** for settings/tokens/trades/logs and open positions
- **Telegram integration** configurable via UI/API
- **WebSocket events** for real-time UI updates

> Scope note: DEX mode uses OKX Web3 DEX aggregator endpoints and requires
> wallet parameters. Live DEX execution requires private key + RPC endpoint.

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
- In DEX mode, token entries can be address-based and do not require CEX pair format.

---

## DEX support (new)

If your token is not available as CEX instrument pair, use DEX mode.

### DEX settings (Strategy Settings page or `PUT /api/settings`)

- `market_type`: `DEX`
- `dex_project_id`: OKX Web3 project id
- `dex_chain_id`: chain id (example: `1`)
- `dex_quote_token_address`: quote token contract (example Ethereum USDT)
- `dex_quote_token_decimals`: quote token decimals (example `6`)
- `dex_slippage`: 0..1 (example `0.005` for 0.5%)
- `dex_wallet_address`: your wallet address
- `dex_price_probe_quote_amount`: quote amount used for price probing (default `1.0`)
- optional live execution fields:
  - `dex_live_execute`: `1` to send on-chain tx
  - `dex_private_key`
  - `dex_rpc_url`

### DEX token fields (`POST /api/tokens`)

- `market_type`: `DEX`
- `symbol`: human label (or token address if you prefer)
- `dex_token_address`: target token contract address
- optional token overrides:
  - `dex_chain_id`
  - `dex_quote_token_address`
  - `dex_quote_token_decimals`
  - `dex_slippage`

### Safety notes for DEX

- Start with:
  - `dry_run=1`
  - `dex_live_execute=0`
- Enable live execution only after validating quotes/routes.
- For live DEX mode, ensure:
  - wallet has gas token
  - approvals are handled (if required by route/protocol)
  - RPC is stable

---

## Key files

- `backend_api.py` - backend process manager + API server bootstrap
- `okx_worker.py` - worker loop controlled by backend state
- `okx_bot/control_api.py` - API routes + SQLite control store + websocket
- `okx_bot/engine.py` - signal/risk/execution engine with event hooks
- `okx_bot/client.py` - OKX CEX request signing + retry logic
- `okx_bot/dex_client.py` - OKX DEX quote/swap API client
- `okx_bot/exchange.py` - CEX/DEX exchange adapters
- `okx_bot/runtime_factory.py` - runtime builder for market mode
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