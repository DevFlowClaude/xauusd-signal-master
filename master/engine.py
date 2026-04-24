"""
Master Engine

The central orchestrator that:
  1. Connects to MT5 and pulls live XAUUSD data
  2. Feeds the data into the Sunrise Ogle strategy
  3. When strategy emits signals, passes them through FundingPips guards
  4. Broadcasts approved signals via the SignalBroadcaster + HTTP API

Architecture:
  MT5 -> Bar data -> Strategy -> Signal candidate -> Guards -> Broadcast
                                                        |
                                                        v
                                               Telegram + Dashboard

Usage:
  python -m master.engine
"""

from __future__ import annotations
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
import threading
import queue

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# Ensure sunrise_ogle repo is importable
ROOT = Path(__file__).resolve().parent.parent
SUNRISE_PATH = ROOT.parent / "sunrise_ogle"
if str(SUNRISE_PATH) not in sys.path:
    sys.path.insert(0, str(SUNRISE_PATH))

import backtrader as bt

from shared.logger import setup_logger, get_logger
from shared.protocol import Signal, SignalType
from master.mt5_feed import MT5Feed, MockMT5Feed, MT5_AVAILABLE
from master.signal_broadcaster import SignalBroadcaster
from master.http_api import HTTPSignalAPI
from master.guards.fundingpips import FundingPipsGuards, GuardConfig
from master.guards.news_filter import NewsFilter
from master.sunrise_ogle_wrapper import SunriseOgleSignalMode


log = setup_logger("master_engine", log_dir=str(ROOT / "logs"))


class MasterEngine:
    """
    Main loop. Polls for new 5-minute bars, runs the strategy on them,
    and broadcasts approved signals via WebSocket + HTTP API + JSON file.
    """

    def __init__(self, config: dict):
        self.config = config
        self.feed: Optional[MT5Feed] = None
        self.broadcaster: Optional[SignalBroadcaster] = None
        self.http_api: Optional[HTTPSignalAPI] = None
        self.guards: Optional[FundingPipsGuards] = None
        self.news_filter: Optional[NewsFilter] = None
        self._running = False
        self._last_bar_time: Optional[datetime] = None
        self._signal_queue: queue.Queue = queue.Queue()
        self._strategy_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -----------------------------------------------------------------
    # Signal callback - runs on the strategy thread.
    # We put signals into a queue, the async loop picks them up.
    # -----------------------------------------------------------------
    def _strategy_signal_callback(self, signal: Signal) -> None:
        """Called by the strategy when it emits a signal (buy/sell)."""
        self._signal_queue.put(signal)

    async def _process_signal_queue(self) -> None:
        """Async task: drain the queue and broadcast approved signals."""
        while self._running:
            try:
                signal = self._signal_queue.get_nowait()
                await self._handle_signal(signal)
            except queue.Empty:
                await asyncio.sleep(0.1)

    async def _handle_signal(self, signal: Signal) -> None:
        """Run guards, then broadcast if approved."""
        # Fetch current account info
        account = self.feed.get_account() if self.feed else None
        balance = account.balance if account else 10000.0
        equity = account.equity if account else 10000.0

        # Refresh news filter if needed
        news_events = []
        if self.news_filter:
            self.news_filter.refresh()  # Uses cache mostly
            news_events = self.news_filter.events_for_guard_check()

        # Guard check
        allowed, reason = self.guards.check_signal(
            balance, equity, news_events=news_events
        )

        if not allowed:
            log.warning(
                f"Signal BLOCKED: {signal.signal_type.value} "
                f"reason={reason}"
            )
            signal.tags['guard_blocked'] = reason
            # Still log to JSON for audit, but don't broadcast
            self.broadcaster._write_json_backup(signal)
            return

        log.info(
            f"Signal APPROVED: {signal.signal_type.value} @ {signal.entry_price} "
            f"SL={signal.stop_loss} TP={signal.take_profit}"
        )
        await self.broadcaster.broadcast(signal)

    # -----------------------------------------------------------------
    # Strategy evaluation (runs in a separate thread to avoid blocking)
    # -----------------------------------------------------------------
    def _run_strategy_on_bars(self, bars: List) -> None:
        """
        Execute the Sunrise Ogle strategy on the given bars.
        Signals produced will be captured via _strategy_signal_callback.
        """
        if len(bars) < 200:
            log.warning(f"Not enough bars ({len(bars)}) to run strategy")
            return

        # Build an in-memory PandasData feed from our bars
        import pandas as pd
        df = pd.DataFrame([
            {
                'datetime': b.time,
                'open': b.open,
                'high': b.high,
                'low': b.low,
                'close': b.close,
                'volume': b.volume,
                'openinterest': 0,
            }
            for b in bars
        ])
        df = df.set_index('datetime')
        # The parent strategy reads self.data._dataname.name for filename
        # detection. Monkey-patch 'name' onto the DataFrame so the detection
        # finds a string (contains 'XAUUSD') instead of crashing on DataFrame.
        df.name = "XAUUSD_live_feed.csv"

        cerebro = bt.Cerebro(stdstats=False, quicknotify=True)
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data)
        cerebro.broker.setcash(100000.0)
        cerebro.broker.setcommission(leverage=30.0)
        cerebro.addstrategy(
            SunriseOgleSignalMode,
            signal_callback=self._strategy_signal_callback,
            signal_risk_percent=self.config['strategy']['default_risk_percent'],
            plot_result=False,
            # Live mode: only emit signals for the most recent bar.
            live_mode=True,
            live_start_time=self._last_bar_time,
        )
        try:
            cerebro.run()
        except Exception as e:
            log.error(f"Strategy run error: {e}")

    def _strategy_main_loop(self) -> None:
        """
        Runs in its own thread. Polls for new bars and runs the strategy.
        """
        log.info("Strategy thread started")
        preload_count = self.config['mt5']['history_preload_bars']
        poll_interval = self.config['mt5']['poll_interval_seconds']

        # Initial load
        bars = self.feed.get_bars(count=preload_count)
        if not bars:
            log.error("Could not load initial bars")
            return
        self._last_bar_time = bars[-2].time  # Last closed bar
        log.info(f"Preloaded {len(bars)} bars. Last closed: {self._last_bar_time}")

        # Main poll loop
        while self._running:
            try:
                # Wait for a new closed bar
                new_bar = self.feed.wait_for_new_bar(
                    self._last_bar_time,
                    timeframe=self.config['strategy']['timeframe'],
                    poll_interval=poll_interval,
                    max_wait_seconds=600,
                )
                if new_bar is None:
                    log.warning("Timeout waiting for new bar, will retry")
                    continue

                log.info(
                    f"New bar closed: {new_bar.time.isoformat()} "
                    f"O={new_bar.open:.2f} H={new_bar.high:.2f} "
                    f"L={new_bar.low:.2f} C={new_bar.close:.2f}"
                )
                self._last_bar_time = new_bar.time

                # Reload full window and re-run strategy
                bars = self.feed.get_bars(count=preload_count)
                self._run_strategy_on_bars(bars)

            except Exception as e:
                log.error(f"Strategy loop error: {e}")
                time.sleep(poll_interval)

        log.info("Strategy thread stopped")

    # -----------------------------------------------------------------
    # Main start/stop
    # -----------------------------------------------------------------
    async def start(self) -> None:
        log.info("=" * 60)
        log.info(f"{self.config['master']['name']} v{self.config['master']['version']}")
        log.info("=" * 60)

        # 1. Connect to MT5 (or mock)
        if MT5_AVAILABLE:
            self.feed = MT5Feed(
                symbol_candidates=self.config['mt5']['symbol_candidates']
            )
        else:
            log.warning("MT5 not available - using MOCK feed (development mode)")
            csv_path = SUNRISE_PATH / "data" / "XAUUSD_5m_5Yea.csv"
            if not csv_path.exists():
                log.error(f"Mock CSV not found: {csv_path}")
                return
            self.feed = MockMT5Feed(str(csv_path))

        if not self.feed.connect():
            log.error("Feed connection failed")
            return

        symbol_info = self.feed.get_symbol_info()
        log.info(f"Symbol info: {symbol_info}")

        account = self.feed.get_account()
        log.info(f"Account: {account}")

        # 2. Initialize guards
        self.guards = FundingPipsGuards(
            config=GuardConfig(**self.config['fundingpips_guards'])
        )
        if account:
            self.guards.initialize(account.balance)

        # 3. Initialize news filter
        nf_cfg = self.config.get('news_filter', {})
        self.news_filter = NewsFilter(
            currencies=nf_cfg.get('currencies', ['USD']),
            impact_levels=nf_cfg.get('impact_levels', ['High']),
        )
        self.news_filter.refresh()

        # 4. Start broadcaster (WebSocket + JSON file)
        self.broadcaster = SignalBroadcaster(
            ws_host=self.config['broadcaster']['websocket_host'],
            ws_port=self.config['broadcaster']['websocket_port'],
            json_path=self.config['broadcaster']['json_output_path'],
            require_auth=False,
        )
        await self.broadcaster.start()

        # 5. Start HTTP API (for MT5 EA polling)
        http_port = self.config['broadcaster'].get('http_port', 8766)
        http_host = self.config['broadcaster'].get('http_host', '0.0.0.0')
        self.http_api = HTTPSignalAPI(
            signal_source=self.broadcaster,
            host=http_host,
            port=http_port,
            require_auth=False,
        )
        await self.http_api.start()

        # 6. Start strategy thread
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._strategy_thread = threading.Thread(
            target=self._strategy_main_loop,
            daemon=True,
        )
        self._strategy_thread.start()

        # 7. Start signal queue processor
        queue_task = asyncio.create_task(self._process_signal_queue())

        log.info("Master engine fully started. Press Ctrl+C to stop.")

        # Wait for stop signal
        try:
            while self._running:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

        await self.stop()
        queue_task.cancel()

    async def stop(self) -> None:
        log.info("Stopping master engine...")
        self._running = False
        if self.http_api:
            await self.http_api.stop()
        if self.broadcaster:
            await self.broadcaster.stop()
        if self.feed:
            self.feed.disconnect()
        log.info("Master engine stopped")


def load_config(path: Optional[str] = None) -> dict:
    if path is None:
        path = str(Path(__file__).resolve().parent / "config.yaml")
    if yaml is None:
        raise RuntimeError("pyyaml not installed. Run: pip install pyyaml")
    with open(path) as f:
        return yaml.safe_load(f)


async def amain():
    config = load_config()
    engine = MasterEngine(config)
    await engine.start()


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("Interrupted by user")


if __name__ == "__main__":
    main()
