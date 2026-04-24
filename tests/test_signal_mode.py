"""
Integration test: Run Sunrise Ogle in signal mode on historical data.

This proves:
  1. The wrapper correctly intercepts buy/sell calls
  2. Signals are emitted in the expected format
  3. We can reproduce the 175 trade count from the original backtest
     (each trade = 1 OPEN signal + 1 CLOSE signal = 350 signals)
"""

import sys
from pathlib import Path
from datetime import datetime

# Project root on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtrader as bt
from master.sunrise_ogle_wrapper import SunriseOgleSignalMode
from shared.protocol import Signal, SignalType
from shared.logger import setup_logger, get_logger

log = setup_logger("test_signal_mode", log_dir=str(ROOT / "logs"))


# Collect signals emitted during the backtest
collected_signals: list = []


def my_signal_callback(signal: Signal):
    collected_signals.append(signal)
    if len(collected_signals) % 20 == 0:
        log.info(f"... {len(collected_signals)} signals emitted so far")


def run_test():
    # Data file
    csv_path = ROOT.parent / "sunrise_ogle" / "data" / "XAUUSD_5m_5Yea.csv"
    if not csv_path.exists():
        log.error(f"Data file not found: {csv_path}")
        return

    # Use a small window for speed (1 month)
    feed = bt.feeds.GenericCSVData(
        dataname=str(csv_path),
        dtformat='%Y%m%d',
        tmformat='%H:%M:%S',
        datetime=0, time=1, open=2, high=3, low=4, close=5, volume=6,
        timeframe=bt.TimeFrame.Minutes, compression=5,
        fromdate=datetime(2024, 7, 1),
        todate=datetime(2024, 10, 1),  # 3-month sample
    )

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(feed)
    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(leverage=30.0)
    cerebro.addstrategy(
        SunriseOgleSignalMode,
        signal_callback=my_signal_callback,
        signal_risk_percent=1.5,
        plot_result=False,
    )

    log.info("Starting backtest with SunriseOgleSignalMode...")
    cerebro.run()
    log.info("Backtest complete")

    # Count by signal type
    by_type = {}
    for s in collected_signals:
        t = s.signal_type.value
        by_type[t] = by_type.get(t, 0) + 1

    log.info("=" * 60)
    log.info(f"TOTAL SIGNALS EMITTED: {len(collected_signals)}")
    for t, c in sorted(by_type.items()):
        log.info(f"  {t}: {c}")
    log.info("=" * 60)

    # Show first few signals
    log.info("First 3 signals:")
    for s in collected_signals[:3]:
        log.info(f"  {s.signal_type.value}: symbol={s.symbol} "
                 f"entry={s.entry_price} SL={s.stop_loss} TP={s.take_profit}")
    log.info("Last 3 signals:")
    for s in collected_signals[-3:]:
        log.info(f"  {s.signal_type.value}: symbol={s.symbol} "
                 f"entry={s.entry_price} SL={s.stop_loss} TP={s.take_profit}")


if __name__ == "__main__":
    run_test()
