# OKX Rule-Based Trading Bot (Starter)

Command-style Python starter bot for OKX API v5 spot trading with:

- Buy when price drops by threshold (e.g. `-25%`) **or** reaches a fixed buy price
- Sell at profit threshold (e.g. `+25%`) **or** fixed sell price
- Optional stop-loss per token (e.g. `-10%`)
- Fixed trade size in USD (e.g. `$100`)
- Max concurrent active positions (e.g. `6`)
- SQLite persistence for open positions (survives restart)

## Important scope note

This starter uses **OKX CEX API v5** (API key + secret + passphrase).  
If you need **OKX DEX / wallet-based Web3 routing**, that is a different integration path and requires wallet signing + Web3 SDK flow.

---

## 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 2) Configure

Create config and env files from examples:

```bash
cp config.example.json config.json
cp .env.example .env
```

Fill `.env`:

```dotenv
OKX_API_KEY=your_key
OKX_SECRET_KEY=your_secret
OKX_PASSPHRASE=your_passphrase
BASE_URL=https://www.okx.com
OKX_SIMULATED=false
```

Edit `config.json` with your token rules.

Example:

```json
{
  "max_active_trades": 6,
  "trade_amount_usd": 100,
  "poll_interval_seconds": 10,
  "request_timeout_seconds": 10,
  "max_retries": 3,
  "retry_backoff_seconds": 1.5,
  "tokens": [
    {
      "symbol": "ETH-USDT",
      "buy_drop_pct": -25,
      "buy_drop_reference_price": null,
      "buy_price": null,
      "sell_profit_pct": 25,
      "sell_price": null,
      "stop_loss_pct": -10
    },
    {
      "symbol": "ARB-USDT",
      "buy_drop_pct": null,
      "buy_drop_reference_price": null,
      "buy_price": 1.2,
      "sell_profit_pct": null,
      "sell_price": 1.6,
      "stop_loss_pct": -10
    }
  ]
}
```

### `buy_drop_reference_price` behavior

- If set (number), drop % is measured against that fixed reference.
- If `null`, the bot uses an in-memory reference initialized from first seen price for that symbol and refreshed after each completed trade cycle.

---

## 3) Run

Single cycle (safe smoke test):

```bash
python3 bot.py --config config.json --db positions.db --once --dry-run
```

Continuous run:

```bash
python3 bot.py --config config.json --db positions.db
```

Useful options:

- `--dry-run` : evaluate signals without placing orders
- `--once` : run one loop and exit
- `--log-level DEBUG` : verbose logs

---

## Bot architecture

- `okx_bot/config.py` - load and validate JSON config
- `okx_bot/client.py` - OKX v5 request signing + HTTP retries
- `okx_bot/engine.py` - signal engine + trade executor + bot loop cycle
- `okx_bot/risk.py` - fixed size + max concurrent trade constraints
- `okx_bot/storage.py` - SQLite persistence for open positions
- `bot.py` - CLI entrypoint

---

## Safety checklist before live trading

- Start with `OKX_SIMULATED=true`
- Keep `--dry-run` enabled until logs match expected triggers
- Validate instrument rules (tick size, min order size) for each symbol
- Add monitoring/alerts for order failures and stuck positions
- Consider websocket market data for lower latency vs polling