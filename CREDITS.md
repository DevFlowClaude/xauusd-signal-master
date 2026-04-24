# Credits

This project builds on the work of others. Credit where credit is due.

## Strategy

The Sunrise Ogle XAU/USD pullback strategy was developed by
**[ilahuerta-IA](https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd)**.

This project does NOT include the strategy code itself — it imports it from
the original repository as an external dependency. To use this project, you
must clone the strategy repo alongside:

```bash
git clone https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd sunrise_ogle
```

The strategy is licensed under MIT — see the original repo for details.

## What This Project Adds

- Live trading wrapper around the Backtrader strategy (signal-mode)
- MT5 data feed integration via the official MetaTrader5 Python package
- HTTP REST + WebSocket signal broadcaster
- MT5 Expert Advisor (MQL5) signal subscriber
- FundingPips prop firm compliance guards
- ForexFactory news filter
- Telegram bot notifications
- Streamlit real-time dashboard
- Paper trading mode for safe forward testing

## Other Dependencies

- **MetaTrader5** Python package (MetaQuotes Software Corp.)
- **Backtrader** framework (https://www.backtrader.com/)
- **FastAPI** for HTTP API (https://fastapi.tiangolo.com/)
- **Streamlit** for dashboard (https://streamlit.io/)
- **websockets** for real-time push
- **pyyaml**, **pandas**, **numpy** standard data tools

## Disclaimer

Trading financial instruments involves substantial risk of loss. Past
performance does not guarantee future results. This software is for
educational and research purposes. Test thoroughly before live trading.
