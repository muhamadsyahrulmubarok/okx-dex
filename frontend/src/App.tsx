import { useEffect, useMemo, useState } from "react";

type BotStatusResponse = {
  db: Record<string, unknown>;
  runtime: {
    running?: boolean;
    pid?: number | null;
  };
};

type Settings = Record<string, unknown>;
type TokenRow = Record<string, unknown>;
type LogRow = Record<string, unknown>;
type TradeRow = Record<string, unknown>;

const apiBase = import.meta.env.VITE_API_BASE ?? "http://localhost:8080";
const wsBase = import.meta.env.VITE_WS_BASE ?? "ws://localhost:8080/ws/events";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`API ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

function toDisplay(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function App() {
  const [status, setStatus] = useState<BotStatusResponse | null>(null);
  const [settings, setSettings] = useState<Settings>({});
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const [newToken, setNewToken] = useState({
    symbol: "",
    buy_drop_pct: "",
    buy_price: "",
    sell_profit_pct: "",
    sell_price: "",
    stop_loss_pct: "",
  });

  async function refreshAll() {
    setLoading(true);
    setError("");
    try {
      const [s, cfg, tk, lg, tr] = await Promise.all([
        api<BotStatusResponse>("/api/bot/status"),
        api<Settings>("/api/settings"),
        api<TokenRow[]>("/api/tokens"),
        api<LogRow[]>("/api/logs?limit=100"),
        api<TradeRow[]>("/api/trades?limit=100"),
      ]);
      setStatus(s);
      setSettings(cfg);
      setTokens(tk);
      setLogs(lg);
      setTrades(tr);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refreshAll();
    const ws = new WebSocket(wsBase);
    ws.onmessage = () => {
      refreshAll();
    };
    ws.onerror = () => {
      setError("WebSocket disconnected. Showing last fetched data.");
    };
    return () => ws.close();
  }, []);

  const activeTradesCount = useMemo(() => {
    const open = trades.filter((t) => String(t.action) === "BUY").length;
    return open;
  }, [trades]);

  async function botCommand(path: "/api/bot/start" | "/api/bot/stop" | "/api/bot/restart") {
    await api(path, { method: "POST" });
    await refreshAll();
  }

  async function saveSettings() {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(settings),
    });
    await refreshAll();
  }

  function updateSetting(key: string, value: string) {
    const maybeNumber = Number(value);
    const normalized = Number.isNaN(maybeNumber) ? value : maybeNumber;
    setSettings((prev) => ({ ...prev, [key]: normalized }));
  }

  async function addToken() {
    const payload: Record<string, unknown> = {
      symbol: newToken.symbol.trim(),
      is_active: true,
    };
    for (const key of [
      "buy_drop_pct",
      "buy_price",
      "sell_profit_pct",
      "sell_price",
      "stop_loss_pct",
    ]) {
      const value = (newToken as Record<string, string>)[key];
      if (value !== "") payload[key] = Number(value);
    }
    await api("/api/tokens", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setNewToken({
      symbol: "",
      buy_drop_pct: "",
      buy_price: "",
      sell_profit_pct: "",
      sell_price: "",
      stop_loss_pct: "",
    });
    await refreshAll();
  }

  async function deleteToken(id: number) {
    await api(`/api/tokens/${id}`, { method: "DELETE" });
    await refreshAll();
  }

  async function testTelegram() {
    await api("/api/telegram/test", { method: "POST", body: JSON.stringify({}) });
    await refreshAll();
  }

  return (
    <div className="page">
      <h1>OKX Bot Control Panel</h1>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <section className="card">
          <h2>Bot Control</h2>
          <p>
            Status:{" "}
            <strong>{status?.db?.status ? String(status.db.status) : "UNKNOWN"}</strong>
          </p>
          <p>Worker running: {String(status?.runtime?.running ?? false)}</p>
          <p>PID: {toDisplay(status?.runtime?.pid)}</p>
          <p>
            Active Trades: {activeTradesCount} / {toDisplay(settings.max_active_trades)}
          </p>
          <p>Last Run: {toDisplay(status?.db?.last_execution_at)}</p>
          <div className="actions">
            <button onClick={() => botCommand("/api/bot/start")}>Start</button>
            <button onClick={() => botCommand("/api/bot/stop")}>Stop</button>
            <button onClick={() => botCommand("/api/bot/restart")}>Restart</button>
            <button onClick={refreshAll} disabled={loading}>
              Refresh
            </button>
          </div>
        </section>

        <section className="card">
          <h2>Strategy Settings</h2>
          <label>
            Trade Amount USD
            <input
              value={toDisplay(settings.trade_amount_usd)}
              onChange={(e) => updateSetting("trade_amount_usd", e.target.value)}
            />
          </label>
          <label>
            Max Active Trades
            <input
              value={toDisplay(settings.max_active_trades)}
              onChange={(e) => updateSetting("max_active_trades", e.target.value)}
            />
          </label>
          <label>
            Poll Interval Seconds
            <input
              value={toDisplay(settings.poll_interval_seconds)}
              onChange={(e) => updateSetting("poll_interval_seconds", e.target.value)}
            />
          </label>
          <label>
            Dry Run (0/1)
            <input
              value={toDisplay(settings.dry_run)}
              onChange={(e) => updateSetting("dry_run", e.target.value)}
            />
          </label>
          <button onClick={saveSettings}>Save Settings</button>
        </section>
      </div>

      <div className="row">
        <section className="card">
          <h2>Token Management</h2>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Symbol</th>
                <th>Buy Drop %</th>
                <th>Buy Price</th>
                <th>Sell Profit %</th>
                <th>Sell Price</th>
                <th>Stop Loss %</th>
                <th>Active</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={String(t.id)}>
                  <td>{toDisplay(t.id)}</td>
                  <td>{toDisplay(t.symbol)}</td>
                  <td>{toDisplay(t.buy_drop_pct)}</td>
                  <td>{toDisplay(t.buy_price)}</td>
                  <td>{toDisplay(t.sell_profit_pct)}</td>
                  <td>{toDisplay(t.sell_price)}</td>
                  <td>{toDisplay(t.stop_loss_pct)}</td>
                  <td>{toDisplay(t.is_active)}</td>
                  <td>
                    <button onClick={() => deleteToken(Number(t.id))}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="inline-form">
            <input
              placeholder="SYMBOL (e.g. ETH-USDT)"
              value={newToken.symbol}
              onChange={(e) => setNewToken((p) => ({ ...p, symbol: e.target.value }))}
            />
            <input
              placeholder="buy_drop_pct"
              value={newToken.buy_drop_pct}
              onChange={(e) => setNewToken((p) => ({ ...p, buy_drop_pct: e.target.value }))}
            />
            <input
              placeholder="buy_price"
              value={newToken.buy_price}
              onChange={(e) => setNewToken((p) => ({ ...p, buy_price: e.target.value }))}
            />
            <input
              placeholder="sell_profit_pct"
              value={newToken.sell_profit_pct}
              onChange={(e) =>
                setNewToken((p) => ({ ...p, sell_profit_pct: e.target.value }))
              }
            />
            <input
              placeholder="sell_price"
              value={newToken.sell_price}
              onChange={(e) => setNewToken((p) => ({ ...p, sell_price: e.target.value }))}
            />
            <input
              placeholder="stop_loss_pct"
              value={newToken.stop_loss_pct}
              onChange={(e) => setNewToken((p) => ({ ...p, stop_loss_pct: e.target.value }))}
            />
            <button onClick={addToken}>Add Token</button>
          </div>
        </section>

        <section className="card">
          <h2>Telegram Settings</h2>
          <label>
            Telegram Enabled (0/1)
            <input
              value={toDisplay(settings.telegram_enabled)}
              onChange={(e) => updateSetting("telegram_enabled", e.target.value)}
            />
          </label>
          <label>
            Telegram Bot Token
            <input
              value={toDisplay(settings.telegram_bot_token)}
              onChange={(e) => updateSetting("telegram_bot_token", e.target.value)}
            />
          </label>
          <label>
            Telegram Chat ID
            <input
              value={toDisplay(settings.telegram_chat_id)}
              onChange={(e) => updateSetting("telegram_chat_id", e.target.value)}
            />
          </label>
          <div className="actions">
            <button onClick={saveSettings}>Save Telegram Settings</button>
            <button onClick={testTelegram}>Test Notification</button>
          </div>
        </section>
      </div>

      <div className="row">
        <section className="card">
          <h2>Recent Logs</h2>
          <pre>{JSON.stringify(logs.slice(0, 30), null, 2)}</pre>
        </section>
        <section className="card">
          <h2>Recent Trades</h2>
          <pre>{JSON.stringify(trades.slice(0, 30), null, 2)}</pre>
        </section>
      </div>

      <div className="row">
        <section className="card">
          <h2>Chart Page (MVP Placeholder)</h2>
          <p>
            Use TradingView widget in this panel and overlay entry/exit markers from
            <code>/api/trades</code>.
          </p>
          <p>
            Example symbol format for widget: <code>OKX:ETHUSDT</code>
          </p>
        </section>
      </div>
    </div>
  );
}
