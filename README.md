# XAUUSD Signal Master https://linktr.ee/DevFlow_Claude

Signal provider system for XAU/USD (Gold) based on the **Sunrise Ogle** strategy.

**Validated performance** (5-year backtest 2020-2025):
- +44.75% total return
- 55.43% win rate (97W / 78L / 175 trades)
- Profit Factor 1.64
- Max Drawdown 5.81%
- Sharpe Ratio 0.89

Last year out-of-sample (2024-2025) was even better: **PF 1.90, Sharpe 1.95, 
+6.31% return, only 3.12% max DD**.

## Architecture

```
Master (Python) → HTTP/WebSocket → N × MT5 EA Clients
     ↓
 Telegram + Streamlit Dashboard
```

The master process:
1. Connects to MT5 and polls XAUUSD 5-minute data
2. Runs the Sunrise Ogle strategy on it
3. Emits signals to the broadcaster
4. Applies FundingPips guards (daily/overall loss, news filter, consistency)
5. Broadcasts approved signals via HTTP/WebSocket
6. Sends Telegram notifications

Each client (MT5 Expert Advisor) polls the HTTP endpoint, receives signals,
and executes trades on its own account with its configured risk%.

## Features

- **Multi-client ready** — one master, many subscriber accounts
- **FundingPips compliant** — static drawdown, news filter, consistency rule
- **HTTP REST API** (primary) + WebSocket (future high-frequency) + JSON file (backup)
- **Authentication** — HMAC-signed signals, per-client API keys
- **Telegram alerts** — trade signals, daily summaries, guard blocks
- **Streamlit dashboard** — real-time status, signal history, trades
- **Risk controls** — risk% capping, max lot size, direction filters per client
- **Broker symbol aliases** — XAUUSD / GOLD / XAUUSDm variants handled

## Installation

### On Windows VPS (production):

```cmd
# Python 3.10+ required
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### MT5 terminal setup:

1. Install FxPro MetaTrader 5 (or other broker)
2. Log into your account (demo for development)
3. Verify XAUUSD is visible in Market Watch

### Sunrise Ogle strategy:

Clone the reference repo alongside this project:

```
git clone https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd.git sunrise_ogle
```

The master imports the strategy from this location without modification.

## Configuration

Edit `master/config.yaml` to set:
- Risk% per trade (default 1.5)
- FundingPips guards thresholds
- WebSocket / HTTP ports
- Session times

Create `.env` in the project root:

```
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
XAUUSD_MASTER_SECRET=your_32_char_random_secret
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=FxPro-MT5
```

## Running

### Start the master:

```bash
python -m master.engine
```

### Start the dashboard (separate terminal):

```bash
streamlit run master/integrations/dashboard.py
```

The dashboard has a timezone selector in the sidebar. Default is 
`Europe/Berlin` (for users in Germany). All times in the UI convert 
automatically. Internally, all calculations use UTC.

### Testing without MT5/VPS: Signal Replayer

You can test the full pipeline (master + HTTP API + clients) using
historical data before deploying:

```bash
# Replay 3 months of data at 100x speed (about 22 minutes real time)
python -m master.replay --speed 100 --start 2024-07-01 --end 2024-10-01

# Fast demo: 3 months in ~80 seconds
python -m master.replay --speed 100000 --start 2024-07-01 --end 2024-10-01

# Use different ports if default ones are busy
python -m master.replay --speed 60 --http-port 8776 --ws-port 8775
```

While the replayer runs, clients (Python or MT5 EA) can connect to the HTTP
endpoint and receive signals as they would in live trading.

### Deploy the MT5 EA (on each client account):

1. Copy `clients/mt5_ea/XAUUSD_Signal_Client.mq5` to MT5's 
   `MQL5/Experts/` directory.
2. Compile in MetaEditor (F7).
3. Add your master URL to `Tools > Options > Expert Advisors`:
   - Allow WebRequest for listed URL: `http://YOUR_VPS_IP:8766`
4. Attach the EA to a XAUUSD chart.
5. Configure parameters:
   - `InpMasterURL` = your master URL
   - `InpClientId` = unique identifier (e.g. "fundingpips_10k_01")
   - `InpApiKey` = API key (if auth enabled)
   - `InpSymbol` = broker-specific symbol (XAUUSD / GOLD / etc.)
   - `InpRiskOverride` = cap risk% if needed (0 = use master's)
   - **`InpPaperMode` = true (SAFE DEFAULT)**: signals are logged to CSV but 
     no real trades are placed. Switch to false only after validating the
     full pipeline works in paper mode for at least 1-2 weeks.

### Paper Mode (IMPORTANT for first deployment)

The EA defaults to `InpPaperMode = true`. In this mode:
- Signals are received and fully processed (lot size calculated, SL/TP normalized)
- Results are logged to `MQL5/Files/paper_trades.csv` with all details
- **No real trades are sent to the broker**
- The master still receives ACKs marked with "PAPER" comment

Use paper mode for:
- First week of deployment to verify master <-> EA connectivity
- Validating lot sizing matches your risk tolerance
- Testing the FundingPips guards without risking capital

Switch to live trading by setting `InpPaperMode = false` in the EA inputs.

## Project Structure

```
xauusd_signal_master/
├── master/                  # Python master process
│   ├── engine.py            # Main orchestrator
│   ├── replay.py            # Signal replayer (CSV -> simulated live)
│   ├── mt5_feed.py          # MT5 data connector
│   ├── sunrise_ogle_wrapper.py  # Strategy signal-mode wrapper
│   ├── signal_broadcaster.py    # WebSocket + JSON
│   ├── http_api.py          # FastAPI HTTP endpoint
│   ├── config.yaml          # Configuration
│   ├── guards/              # Safety rules
│   │   ├── fundingpips.py   # Daily/overall loss (UTC-based), 5-hour news rule
│   │   └── news_filter.py   # ForexFactory USD events
│   └── integrations/
│       ├── telegram.py      # Telegram notifications
│       └── dashboard.py     # Streamlit UI (with timezone selector)
├── clients/
│   ├── mt5_ea/
│   │   └── XAUUSD_Signal_Client.mq5  # MT5 Expert Advisor
│   └── python_client/
│       └── test_client.py   # For testing only
├── shared/
│   ├── protocol.py          # Signal format, HMAC signing
│   ├── auth.py              # API key store
│   └── logger.py            # Rotating file logger
├── sunrise_ogle/            # Cloned strategy repo (external)
├── tests/
│   ├── test_signal_mode.py  # Strategy wrapper test
│   └── test_e2e.py          # End-to-end integration test
├── data/                    # Runtime data (JSON state)
├── logs/                    # Rotating log files
└── deploy/                  # VPS setup docs
```

## FundingPips 2-Step Configuration

Default guards match FundingPips 2-Step Standard challenge rules:

| Rule | FundingPips limit | Our guard (safer) |
|------|---------------|-----------------|
| Max Daily Loss | 5% | 4.5% |
| Max Overall Loss | 10% | 9.0% |
| Consistency | 30% of total profit | 30% monitoring |
| Min Trading Days | 4 days | tracked |
| News window | No trade 3min before/after | 10min before / 5min after |
| 5-hour rule | Profits excluded 5hr of news | Block trades 5hr after |

Adjust in `config.yaml` `fundingpips_guards:` section.

## Expected Performance

At 1.5% risk per trade (your chosen setting):
- Expected annual return: ~12-14%
- Expected max DD: ~8.7%
- Phase 1 target (8% profit): 6-12 months typical
- Phase 2 target (5% profit): 4-6 months typical
- Funded account ongoing: ~10-12% annual return

**Important:** these are based on 5-year backtest. Live performance will 
differ. Always demo test for 4+ weeks before live FundingPips challenge.

## Development Roadmap

### Phase 1: MVP (✅ complete)
- [x] Master engine
- [x] Sunrise Ogle strategy wrapper
- [x] MT5 data feed (with Mock fallback)
- [x] HTTP API + WebSocket broadcaster
- [x] FundingPips guards
- [x] MT5 Expert Advisor client
- [x] Telegram integration
- [x] Streamlit dashboard
- [x] End-to-end tests

### Phase 2: Multi-client hardening
- [ ] Per-client authentication & rate limiting
- [ ] Client registry database (SQLite)
- [ ] Signal replay on client reconnect
- [ ] Monitoring & alerting (Prometheus/Grafana)
- [ ] Extensive forward testing suite

### Phase 3: Signal provider service
- [ ] Subscription billing integration
- [ ] Web portal for subscribers (signup, API keys, stats)
- [ ] Multi-strategy support (add ORB, Mean Reversion strategies)
- [ ] Per-subscriber custom risk
- [ ] Copy trade regulation compliance

## License

MIT — same as upstream Sunrise Ogle strategy.

## Disclaimer

⚠️ **Trading financial instruments involves substantial risk of loss.**

- Past performance does NOT guarantee future results.
- This software is for EDUCATIONAL and RESEARCH purposes.
- Test thoroughly on demo accounts before live trading.
- Start with very small position sizes.
- The authors are not responsible for any financial losses.

Trade at your own risk.
