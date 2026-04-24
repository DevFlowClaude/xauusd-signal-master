# XAUUSD Signal Master

Automated signal provider system for XAU/USD (Gold) trading on MetaTrader 5, based on the **Sunrise Ogle** pullback strategy. Designed for FundingPips prop firm challenges with strict drawdown compliance.

---

## Table of Contents

1. [What This Does](#what-this-does)
2. [Strategy Explanation](#strategy-explanation)
3. [System Architecture](#system-architecture)
4. [Signal Flow (End-to-End)](#signal-flow-end-to-end)
5. [Trading Logic](#trading-logic)
6. [Risk Management](#risk-management)
7. [FundingPips Guards](#fundingpips-guards)
8. [Paper Trading Mode](#paper-trading-mode)
9. [Components Deep Dive](#components-deep-dive)
10. [Performance Expectations](#performance-expectations)
11. [Installation](#installation)
12. [Running the System](#running-the-system)
13. [MT5 Expert Advisor Setup](#mt5-expert-advisor-setup)
14. [Monitoring](#monitoring)
15. [Deployment to VPS](#deployment-to-vps)

---

## What This Does

This system automatically generates and executes XAU/USD trading signals based on a validated technical strategy. It splits the work across two components:

- **The Master** — a Python program that runs the strategy and decides when to trade
- **The Client** — an MT5 Expert Advisor that receives signals and executes trades

One master can serve multiple clients simultaneously. Each client uses its own account and own risk settings.

**Validated performance** (5-year backtest 2020-2025):

| Metric | Value |
|--------|-------|
| Total return | +44.75% |
| Win rate | 55.43% |
| Profit factor | 1.64 |
| Max drawdown | 5.81% |
| Total trades | 175 |
| Sharpe ratio | 0.89 |

**Last-year out-of-sample** (2024-2025): +6.31%, 60% WR, Sharpe 1.95, DD 3.12%.

---

## Strategy Explanation

### The Sunrise Ogle Pullback Strategy

Sunrise Ogle is a trend-following pullback strategy. It waits for gold to be in a clear uptrend, then enters on a shallow pullback with tight risk control.

The strategy uses a **4-phase state machine**:

1. **PHASE 1 — Trend Detection**: confirm price is in an uptrend by checking that close is above EMA200 and the EMA slope is positive
2. **PHASE 2 — Pullback Window**: wait for price to pull back toward the EMA without breaking it
3. **PHASE 3 — Entry Signal**: when price bounces off the pullback and shows momentum recovery, trigger a buy
4. **PHASE 4 — Management**: ride the trade with fixed SL/TP based on ATR

### Entry Conditions (simplified)

All of these must be true for a LONG entry:

- Close above EMA200 (on M5 timeframe)
- EMA200 slope positive (angle filter)
- Pullback has occurred (price touched within X% of EMA)
- ATR increment detected (momentum recovery)
- No active news within the exclusion window
- Outside weekend hours (UTC)

### Exit Conditions

- **Stop loss**: 4.5× ATR below entry
- **Take profit**: 6.5× ATR above entry
- **Risk:Reward ratio**: approximately 1:1.44

The strategy **does not use trailing stops**. Every trade has fixed SL and TP set at entry.

### Why Only Long Trades

Gold has a strong upward bias historically. The strategy has `enable_short=false` because short setups on gold are statistically less reliable and FundingPips' overall drawdown limit is too tight for the worse win rate that shorts would introduce.

---

## System Architecture

```
+--------------------------------------------------------+
|  VPS or Local PC (Windows)                             |
|                                                        |
|  +-------------------------------------------------+   |
|  |  Python Master Process                          |   |
|  |                                                 |   |
|  |  MT5 Terminal <-> mt5_feed.py -> engine.py ----+|   |
|  |                                      |         ||   |
|  |  sunrise_ogle_wrapper.py <-----------+         ||   |
|  |         |                                      ||   |
|  |         v Signal                               ||   |
|  |  fundingpips_guards -> approved? -> broadcast -+|   |
|  |                                         |       |   |
|  |                  +----------------------+       |   |
|  |                  v                              |   |
|  |     +------------+----------+                   |   |
|  |     |                       |                   |   |
|  |     v                       v                   |   |
|  |  WebSocket              HTTP API                |   |
|  |  :8765                  :8766                   |   |
|  |                           +-> JSON file backup  |   |
|  +---------------------------+---------------------+   |
|                              |                         |
|  Telegram <- alerts          |   Dashboard <- JSON reads
+------------------------------+-------------------------+
                               |
                               v  HTTP GET every 30 sec
                        +-------------+
                        | MT5 EA      |  <- on FundingPips account
                        | (MQL5)      |     (or any MT5 account)
                        |             |
                        | 1. Poll /signals/latest
                        | 2. Calculate lot from balance x risk%
                        | 3. Execute trade with SL/TP
                        | 4. POST /ack with result
                        +-------------+
```

### Why Split Master and Client?

1. **Multi-account support**: one master can feed FundingPips + FTMO + demo accounts simultaneously
2. **Signal provider model**: in the future, paid subscribers can connect their own MT5 terminals
3. **Strategy isolation**: the master's Python code never touches the broker directly — MT5 EA handles all execution, which keeps audit trails clean
4. **Resilience**: if the master crashes, the EA keeps existing positions managed (SL/TP already set on broker side)

---

## Signal Flow (End-to-End)

Here's what happens when the strategy decides to enter a trade.

### Step 1 — Bar Closes

Every 5 minutes, a new M5 bar closes on MT5. The master's strategy thread detects this:

```
New bar closed: 2026-04-24T13:25:00+00:00 O=4683.53 H=4686.20 L=4682.16 C=4682.16
```

### Step 2 — Strategy Evaluates

The master loads the last 500 bars from MT5 and feeds them into the Sunrise Ogle strategy (Backtrader framework). The strategy evaluates all its conditions on this sliding window.

Most bars produce **no signal** (90%+ of the time). The strategy is highly selective.

### Step 3 — Signal Generated

When conditions align, the strategy calls `self.buy()`. Our wrapper intercepts this call and creates a Signal object:

```python
Signal(
    signal_id="uuid-...",
    timestamp=1777027800.0,
    signal_type=OPEN_LONG,
    symbol="XAUUSD",
    entry_price=4712.50,
    stop_loss=4705.20,
    take_profit=4725.80,
    risk_percent=1.5,
    strategy_name="sunrise_ogle",
    valid_until=1777028100.0,  # 5 min expiry
)
```

### Step 4 — Guards Check

Before broadcasting, the signal passes through FundingPips guards:

1. Is the bot permanently disabled? (overall loss hit)
2. Is daily loss >= 4.5%?
3. Is it weekend?
4. Is it Friday after 16:00 UTC?
5. Is there a high-impact USD news event in the window?
6. Are we within 5 hours after a news event?

If **any** check fails, the signal is blocked and logged (but not broadcast). Otherwise:

```
Signal APPROVED: OPEN_LONG @ 4712.50 SL=4705.20 TP=4725.80
```

### Step 5 — Broadcast

The signal is:

1. **HMAC-signed** with master secret
2. **Written to JSON file** `data/signals_live.json` (backup channel)
3. **Pushed to WebSocket subscribers** (future use)
4. **Stored in memory** as the "latest signal" accessible via HTTP GET

### Step 6 — MT5 EA Polls

Every 30 seconds, the MT5 EA does:

```
GET http://127.0.0.1:8766/signals/latest
```

Response:
```json
{
  "signal": {
    "signal_id": "abc-123",
    "signal_type": "OPEN_LONG",
    "entry_price": 4712.50,
    "stop_loss": 4705.20,
    "take_profit": 4725.80,
    "risk_percent": 1.5,
    "timestamp": 1777027800.0,
    ...
  }
}
```

### Step 7 — EA Processes Signal

The EA:

1. Checks if this signal_id was already processed → skip if yes
2. Checks if signal expired (>600 sec old) → skip if yes
3. Checks client-side filters (`InpAllowLong`, `InpMaxLotSize`, etc.)
4. Checks if there's already a position → skip if yes (one position at a time)

### Step 8 — Lot Size Calculation

Based on the account balance and risk%, the EA calculates position size:

```
Balance: $10,000
Risk%: 1.5%
Risk money: $150

SL distance: |4712.50 - 4705.20| = 7.30 points (730 pips on gold)
Point: 0.01
Tick value: $1.00 per 0.01 per 1 lot

Value per point per 1 lot: $1.00

Lots = Risk money / (SL distance in points × Value per point)
     = $150 / (730 × $1.00)
     = 0.205 lots

After rounding down to lot step (0.01):
Final lot = 0.20
```

The EA logs this calculation explicitly for auditing:

```
LotCalc: balance=10000.00 risk=1.50% riskMoney=150.00
         entry=4712.50 SL=4705.20 slPts=730 point=0.01000
         tickValue=1.00000 tickSize=0.01000 valPerPt=1.00000
         lots_raw=0.2054 -> rounded=0.20 (min=0.01 step=0.01)
         margin=628.33 free=10000.00
```

### Step 9 — Margin Safety Check

The EA ensures the margin required doesn't exceed 80% of free margin. If it does, the lot is reduced automatically.

### Step 10 — Execute (or Paper Log)

**If `InpPaperMode = true`** (default):
- Lot, entry, SL, TP written to `MQL5/Files/paper_trades.csv`
- **No real trade placed**
- ACK returned as "FILLED" with comment "PAPER"

**If `InpPaperMode = false`**:
- EA calls `trade.Buy(lots, symbol, 0, sl, tp, comment)`
- MT5 sends order to FxPro/FundingPips broker
- If filled, returns ticket number

### Step 11 — ACK Back to Master

The EA sends an acknowledgment:

```
POST http://127.0.0.1:8766/ack
{
  "signal_id": "abc-123",
  "client_id": "client_fxpro_demo",
  "status": "FILLED",
  "executed_price": 4712.53,
  "executed_lots": 0.20,
  "ticket": 123456789,
  "account_balance": 10000.00,
  "account_equity": 10000.00
}
```

The master logs this for audit.

### Step 12 — Trade Runs

The trade now lives on the broker. SL and TP are set on the broker's server, so even if the master crashes, the trade will close at SL or TP automatically.

### Step 13 — Trade Closes

When price hits SL or TP (or if EA closes it due to CLOSE_ALL signal), the trade closes on MT5. The EA tracks this and can report back.

---

## Trading Logic

### When Does the System Trade?

Approximately **1 signal per day** during active market hours:

- **London session**: 08:00-11:00 UTC (10:00-13:00 CEST)
- **NY overlap**: 12:30-17:00 UTC (14:30-19:00 CEST)
- **Asian session**: rarely fires (low volatility)

### When Does It NOT Trade?

- **Weekends**: market closed, hard block
- **Friday after 16:00 UTC**: weekend gap risk
- **10 minutes before / 5 minutes after high-impact USD news** (NFP, CPI, Fed, FOMC)
- **5 hours after a news event** (FundingPips rule — profits not counted)
- **Daily loss limit reached**: blocks until next UTC day
- **Overall loss limit reached**: permanent disable

### Typical Day on the System

```
Monday 10:00 CEST — London session opens, activity increases
Monday 11:30 CEST — Strategy detects pullback setup
                  — Signal generated: OPEN_LONG @ 3305.20, SL 3298.50, TP 3318.20
                  — Paper log: "Would BUY 0.18 lots"
Monday 13:40 CEST — Price hits TP @ 3318.20
                  — Trade closed with +$128 profit
Monday 14:00-18:00 — No further signals (filter conditions not met)
Monday 22:00 CEST — NY session closes, activity dies
Tuesday 02:00 CEST — Asian session, typically quiet
Tuesday 10:00 CEST — Next potential setup window
```

---

## Risk Management

### Per-Trade Risk

Configurable via `default_risk_percent` in `master/config.yaml`. Default: **1.5%**.

At 1.5% risk per trade:
- Each loss = 1.5% of account = $150 on $10k
- Each win = ~2.1% of account (R:R 1:1.44)
- Expected value per trade = positive (win rate 55% × 2.1% - 45% × 1.5% = +0.48%)

### Drawdown Projection

Based on 5-year backtest at 1.5% risk:

| Metric | Value |
|--------|-------|
| Expected annual return | 12-14% |
| Expected max drawdown | 8.7% |
| Worst observed 3-month stretch | -3.2% |

### Lot Sizing Is Per-Account

The master sends **risk percentage**, not absolute lot size. Each client calculates its own lot based on its balance:

- $10,000 account → 0.20 lots typical
- $50,000 account → 1.00 lots typical
- $100,000 funded → 2.00 lots typical

This means the same signal serves any account size, and each one takes the same proportional risk.

---

## FundingPips Guards

The guards module enforces FundingPips 2-Step Standard challenge rules with extra safety margins.

### Daily Loss Guard

FundingPips limit: **5%** per day (relative to start-of-day equity)

Our guard: **4.5%** (0.5% safety buffer)

If daily loss hits 4.5%, the master **blocks all new signals** until 00:00 UTC next day. This is automatic — you don't need to intervene.

### Overall Loss Guard

FundingPips limit: **10%** from initial balance

Our guard: **9.0%** (1% safety buffer)

If overall loss hits 9%, the master is **permanently disabled** and requires manual reset (delete `data/guards_state.json`). This protects against buggy code or flash crashes blowing up a funded account.

### Consistency Rule (Informational)

FundingPips rule: No single trade can produce more than **30% of total profit**.

The guards module tracks this but does NOT block trades. Instead, it logs a warning if consistency is violated. This is intentional — if the strategy has one big winner, you might want to keep it and compensate with more trades, rather than reject the winner.

### News Filter

Uses ForexFactory calendar to detect USD high-impact events:
- NFP (Non-Farm Payrolls) — first Friday each month
- CPI (Consumer Price Index) — monthly
- Fed FOMC meetings — 8 times per year
- Core PCE, Retail Sales, PPI

Blocks trading:
- **10 minutes before** the event
- **5 minutes after** the event
- **5 hours after** the event (FundingPips rule — profits not counted)

### Friday Close

After 16:00 UTC on Friday, no new trades are opened. This protects against weekend gap risk if a position were still open.

---

## Paper Trading Mode

**CRITICAL SAFETY FEATURE**

The MT5 EA defaults to `InpPaperMode = true`. In paper mode:

### What Happens

1. Signal received from master
2. Lot size calculated normally
3. SL/TP normalized
4. **Instead of sending to broker**, the EA writes to `paper_trades.csv`:

```csv
timestamp,signal_id,direction,entry,stop_loss,take_profit,lots,risk_percent,balance,account
2026-04-24 14:05:12,abc-123,LONG,4712.50,4705.20,4725.80,0.20,1.50,10000.00,591540213
```

5. ACK sent back to master tagged with "PAPER"

### When to Use Paper Mode

**Always for the first 1-2 weeks of deployment**. Purposes:

- Verify master ↔ EA connectivity works
- Validate lot sizing is correct
- Check FundingPips guards trigger as expected
- Ensure SL/TP values are reasonable

### Switching to Live

Only when paper mode has run cleanly for at least 2 weeks with no issues:

1. MT5 → Chart with EA → F7 (Properties) → Inputs tab
2. Change `InpPaperMode` from `true` to `false`
3. Click OK

Now every new signal will place a real trade.

---

## Components Deep Dive

### master/engine.py — Main Orchestrator

The root process. Responsibilities:

1. Connect to MT5 via `MetaTrader5` Python package
2. Start the `SignalBroadcaster` on port 8765 (WebSocket)
3. Start the `HTTPSignalAPI` on port 8766 (HTTP REST)
4. Start the strategy thread that polls for new bars
5. Process the signal queue (strategy thread → async broadcast)
6. Handle graceful shutdown (Ctrl+C)

Runs forever until interrupted.

### master/mt5_feed.py — Data Feed

Wraps the MetaTrader5 Python API. Provides:

- **Symbol resolution**: tries XAUUSD, XAUUSD.r, XAUUSDm, GOLD, GOLDm to find what the broker uses
- **Historical bars**: `get_bars(count=500)` for strategy warmup
- **Real-time polling**: `wait_for_new_bar()` blocks until the next closed bar
- **Account info**: balance, equity, margin, free margin

Has a **MockMT5Feed** class for offline testing (reads CSV instead of MT5).

### master/sunrise_ogle_wrapper.py — Strategy Wrapper

Subclasses the original `SunriseOgle` Backtrader strategy. Overrides:

- `buy()` — intercepts, emits OPEN_LONG signal
- `sell()` — intercepts, emits OPEN_SHORT signal
- `close()` — intercepts, emits CLOSE_ALL signal
- `buy_bracket()` / `sell_bracket()` — handles SL/TP bracket orders

Key features:

- **`live_mode=True`**: only emits signals for the most recent bar; filters out signals from the warmup pass
- **Deterministic**: same bars → same decisions every run
- **No modification of original code**: imports from the sunrise_ogle repo unchanged

### master/signal_broadcaster.py — WebSocket + JSON

Runs a WebSocket server on port 8765. Clients can subscribe for real-time push. Also:

- Signs every signal with HMAC
- Writes every signal to `data/signals_live.json` (ring buffer, last 100)
- Stores signal history in memory for replay
- Tracks ACKs from clients

### master/http_api.py — REST API

FastAPI server on port 8766. Endpoints:

- `GET /health` — liveness check
- `GET /signals/latest` — most recent non-expired signal
- `GET /signals/history?n=10` — last N signals
- `GET /status` — master status, connected clients count
- `POST /ack` — clients submit execution results

This is the primary channel MT5 EAs use to communicate.

### master/guards/fundingpips.py — Safety Guards

Before every broadcast, signals pass through `check_signal()`:

```python
allowed, reason = guards.check_signal(balance, equity, news_events)
if not allowed:
    log.warning(f"Signal BLOCKED: {reason}")
    return  # Don't broadcast
```

State is persisted to `data/guards_state.json` across restarts.

### master/guards/news_filter.py — News Filter

Fetches USD high-impact events from ForexFactory JSON endpoint:

```
https://nfs.faireconomy.media/ff_calendar_thisweek.json
```

Caches for 60 minutes. Checks against current time in the guards.

### master/integrations/telegram.py — Telegram Bot

Sends formatted messages for:

- Master startup/shutdown
- New signal generated (with entry, SL, TP, R:R)
- Trade closed (PnL)
- Guard blocks
- Daily summary

Reuses the bot token from your NAS100 project via `.env`.

### master/integrations/dashboard.py — Streamlit UI

Real-time dashboard showing:

- Last signal with timezone-localized time
- FundingPips guards status
- Daily baseline and P&L
- Recent signals table
- Realized trades and win rate

Has a timezone selector (default: Europe/Berlin).

### clients/mt5_ea/XAUUSD_Signal_Client.mq5 — MT5 EA

The Expert Advisor that runs on the MT5 client. Responsibilities:

- Poll the master's `/signals/latest` every 30 seconds (via `WebRequest`)
- Parse signal JSON (custom lightweight parser, no DLL required)
- Calculate lot size from account balance × risk%
- Check margin availability (cap at 80%)
- Normalize SL/TP to symbol precision
- Execute trade (or log to paper CSV if paper mode)
- Report ACK back to master

---

## Performance Expectations

### At 1.5% Risk

Based on 5-year backtest:

- **Annual return**: 12-14%
- **Max drawdown**: ~8.7%
- **Expected winning month rate**: ~62%
- **Best month**: +4-6%
- **Worst month**: -3-5%

### FundingPips Challenge Timelines

**Phase 1 (8% target, 10% max DD, 5% daily)**:

- Best case: 2-3 months
- Typical: **6-12 months**
- If failed once: retry, average succeed in 2-3 attempts

**Phase 2 (5% target, same DD rules)**:

- Typical: **4-6 months**

**Funded account ongoing**:

- ~10-12% annual return
- After 80% profit split: ~$1,000/year on $10k funded
- Scales linearly: $100k funded ≈ $10,000/year

### This Is NOT Get Rich Quick

The strategy trades **rarely and carefully**. If you expect 50% monthly returns, look elsewhere. This is a **slow, compounding edge** with capital preservation as priority.

---

## Installation

### Requirements

- Windows 10/11 or Linux (with Wine for MT5)
- Python 3.10 or newer
- MetaTrader 5 terminal (FxPro or any broker)
- Git
- 2GB RAM minimum, 4GB recommended
- Internet connection

### Setup

```bash
# Clone the Sunrise Ogle strategy (required)
git clone https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd.git sunrise_ogle

# Clone this repo
git clone https://github.com/YOUR_USERNAME/xauusd_signal_master.git

# Install dependencies
cd xauusd_signal_master
python -m venv venv
venv\Scripts\activate          # Windows
# or: source venv/bin/activate  # Linux

pip install -r requirements.txt
```

### Configuration

Copy and edit:

```bash
copy .env.example .env
notepad .env
```

Fill in:

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
XAUUSD_MASTER_SECRET=RANDOM_32_CHAR_STRING
```

Generate the secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Running the System

### Step 1 — Start MT5

Open MetaTrader 5. Log in to your FxPro account. Verify XAUUSD or GOLD is visible in Market Watch.

### Step 2 — Start the Master

```bash
cd xauusd_signal_master
venv\Scripts\activate
python -u -m master.engine
```

You should see:

```
XAUUSD Signal Master v1.0.0
Symbol info: {'name': 'GOLD', ...}
Account: AccountInfo(login=..., balance=10000.0, ...)
Strategy thread started
Master engine fully started.
Preloaded 500 bars. Last closed: ...
```

**Do not close this terminal.** Keep it running.

### Step 3 — Verify HTTP API

In a browser:

```
http://127.0.0.1:8766/health
```

Should return: `{"status":"ok","timestamp":...}`

### Step 4 — Install the MT5 EA

See [MT5 Expert Advisor Setup](#mt5-expert-advisor-setup) below.

### Step 5 (Optional) — Start the Dashboard

```bash
streamlit run master/integrations/dashboard.py
```

Opens at `http://localhost:8501`

---

## MT5 Expert Advisor Setup

### Step 1 — Copy the EA File

Copy `clients/mt5_ea/XAUUSD_Signal_Client.mq5` to MT5's Experts folder:

```
C:\Users\<YOU>\AppData\Roaming\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Experts\
```

To find this folder: in MT5, go to **File → Open Data Folder → MQL5 → Experts**.

### Step 2 — Compile

Open MetaEditor (F4 in MT5), open the file, press **F7**.

Should see "0 errors, 0 warnings".

### Step 3 — Enable WebRequest

In MT5: **Tools → Options → Expert Advisors** tab

- ✅ Allow algorithmic trading
- ✅ Allow WebRequest for listed URL
- Add URL: `http://127.0.0.1:8766`

Click **OK**.

### Step 4 — Attach EA to Chart

1. Open a GOLD (or XAUUSD) M5 chart
2. Drag `XAUUSD_Signal_Client` from Navigator → Experts onto the chart
3. Configure inputs in the dialog:

```
InpMasterURL       = http://127.0.0.1:8766
InpClientId        = client_fxpro_demo
InpApiKey          = (leave empty for now)
InpSymbol          = GOLD              (FxPro uses "GOLD", not "XAUUSD")
InpPollIntervalSec = 30
InpRiskOverride    = 0.0               (0 = use master's risk%)
InpMaxLotSize      = 0.0               (0 = no cap)
InpAllowLong       = true
InpAllowShort      = true
InpPaperMode       = true              (IMPORTANT for first deployment!)
```

4. Click **OK**

### Step 5 — Verify Connection

In MT5, check the **Experts** tab at the bottom. You should see:

```
=== XAUUSD Signal Client v1.00 ===
Master URL: http://127.0.0.1:8766
Client ID: client_fxpro_demo
Symbol: GOLD (point=0.01000 digits=2)
Account: #... balance=10000.00 USD broker=FXPRO Financial Services Ltd

***********************************************************
*** PAPER MODE ENABLED - NO REAL TRADES WILL BE PLACED ***
*** Signals will be logged to: paper_trades.csv         ***
*** Set InpPaperMode=false when ready to trade live.    ***
***********************************************************

Master connection OK (HTTP 200): {"status":"ok",...}
```

If you see this, **the system is fully operational**.

---

## Monitoring

### Master CMD Window

- Every 5 minutes a new bar log appears
- When a signal is generated: `Signal APPROVED: ...`
- When a signal is blocked: `Signal BLOCKED: reason=...`

### MT5 Experts Tab

- Every 30 seconds a poll log (`Master connection OK` or transient error)
- When signal received: `SIGNAL: id=... type=OPEN_LONG ...`
- When lot calculated: detailed calculation breakdown
- When paper trade logged: `[PAPER] Would BUY ... (NOT EXECUTED)`

### Paper Trades CSV

Located at:
```
C:\Users\<YOU>\AppData\Roaming\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Files\paper_trades.csv
```

Open with Excel or any CSV viewer. Each row = one paper trade with all details.

### Telegram (if configured)

You'll receive:
- Startup notification
- Every signal with formatted details (entry, SL, TP, R:R)
- Daily P&L summary at 21:00 UTC
- Guard block alerts

### Streamlit Dashboard

Real-time view with timezone selector. Update automatically every 10 seconds.

### Log Files

Rotating log files in `logs/`:
- `master_engine.log` — main orchestrator
- `mt5_feed.log` — MT5 connection
- `broadcaster.log` — signal broadcast events
- `fundingpips_guards.log` — guard checks

Files rotate at 10MB, keep last 10 backups.

---

## Deployment to VPS

### Why VPS?

Running on your home PC means the system stops when you shut down. For 24/7 operation, use a VPS.

### Recommended Provider

**Contabo VPS M Windows**: €3.99/month, 4 vCPU, 8GB RAM, 200GB SSD.

Order at: https://contabo.com/en/vps/

### VPS Setup

Full guide in `deploy/windows_vps_setup.md`, summary:

1. Provision Windows Server 2022 on VPS
2. Connect via RDP
3. Install Python 3.10+, Git, FxPro MT5
4. Clone both repos (`sunrise_ogle` and `xauusd_signal_master`)
5. Install dependencies
6. Configure `.env`
7. Set up as Windows service with NSSM for auto-start on boot
8. Install MT5 EA on the same VPS or on a separate client VPS

### Firewall Rules

```cmd
netsh advfirewall firewall add rule name="XAUUSD Master WS" dir=in action=allow protocol=TCP localport=8765
netsh advfirewall firewall add rule name="XAUUSD Master HTTP" dir=in action=allow protocol=TCP localport=8766
```

### Cost Breakdown

| Item | Cost |
|------|------|
| Contabo VPS M Windows | €4/month |
| FxPro demo MT5 | Free |
| FundingPips $10K 2-Step challenge | $32 one-time |
| **Total monthly** | **~€4** |

---

## FAQ

### Why is my account balance still $10,000 but the log says "Final Value: 100,000"?

The `100,000` is a virtual balance **inside** the Backtrader strategy simulation. It's unrelated to your real MT5 account. The strategy runs an internal simulation to make decisions, but real trades are executed by the MT5 EA based on your actual account balance.

### Why are there "0 signals evaluated"?

The Sunrise Ogle strategy is highly selective. It averages ~1 signal per trading day, sometimes 2-3, sometimes 0. Most 5-minute bars don't meet all entry conditions.

### Can I run multiple EAs on different accounts?

Yes. That's the whole point of the signal provider architecture. Install the EA on each account's MT5 terminal, point them all at the same master URL. Each one calculates its own lot size based on its own balance.

### Should I run this on weekends?

No need. The master's weekend guard will block all signals on Saturday and Sunday. You can leave it running or shut it down — your choice.

### What if my PC loses internet connection?

The EA has existing positions already set with SL/TP on the broker side. They will close at SL or TP automatically even if the master is unreachable. New signals just won't arrive until connection is restored.

### What if the Windows Defender blocks it?

Add firewall rules (see VPS section). On your local PC, Windows may prompt the first time — allow it.

### How do I know it's actually working?

1. Master CMD shows "New bar closed" every 5 minutes → master is alive
2. MT5 Experts tab shows "Master connection OK" every 30 sec → EA is connected
3. `netstat -an | findstr 8766` shows ESTABLISHED → connection is live
4. Browser test: `http://127.0.0.1:8766/health` returns JSON → API works

---

## Troubleshooting

### "Master returned HTTP 1001" in MT5

This is an MT5 network socket error, not a real HTTP code. Usually means:
- Master not running → start it
- Port blocked by firewall → add firewall rule
- WebRequest URL not whitelisted → add it in Options → Expert Advisors

### "initializing of XAUUSD_Signal_Client failed with code 1"

EA INIT_FAILED. Check:
- `InpMasterURL` is correct
- `InpSymbol` matches broker (FxPro = "GOLD", FundingPips = "XAUUSD")
- WebRequest URL whitelisted

### Port 8765 or 8766 already in use

A previous master instance is still running:

```cmd
netstat -ano | findstr 8765
taskkill /F /PID <the-PID>
```

### "Strategy run error" in master log

Usually a data issue. Check:
- MT5 is actually logged in
- XAUUSD/GOLD is visible in Market Watch
- Account has access to historical data

### Master starts but HTTP API sorok nincs a logban

The logger for `http_api` might not be configured. The API is still running — verify with:

```cmd
netstat -an | findstr 8766
```

If LISTENING, it's working.

---

## License

MIT — same as upstream Sunrise Ogle strategy.

## Disclaimer

⚠️ **Trading financial instruments involves substantial risk of loss.**

- Past performance does NOT guarantee future results
- This software is for EDUCATIONAL and RESEARCH purposes
- Test thoroughly in paper mode before live trading
- Start with very small position sizes
- The authors are not responsible for any financial losses

Trade at your own risk.

---

## Credits

- Strategy: [ilahuerta-IA/backtrader-pullback-window-xauusd](https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd)
- MT5 Integration: [MetaTrader5 Python package](https://pypi.org/project/MetaTrader5/)
- Framework: [Backtrader](https://www.backtrader.com/)
- News Data: [ForexFactory](https://www.forexfactory.com/)
