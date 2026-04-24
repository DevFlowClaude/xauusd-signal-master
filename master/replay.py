"""
Signal Replayer

Replays historical XAUUSD bars through the master at configurable speed.
Useful for:
  - Pre-deploy testing without VPS / MT5 terminal
  - Validating the full signal pipeline (strategy → guards → broadcast → client)
  - Demonstrating the system to potential subscribers

Usage:
  # 10x speed (6 min real = 1 hour simulated)
  python -m master.replay --speed 10 --start 2024-08-01 --end 2024-09-01

  # Real-time replay
  python -m master.replay --speed 1

  # Fast (60x = 1 day per 24 min)
  python -m master.replay --speed 60
"""

from __future__ import annotations
import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Sunrise Ogle repo
SUNRISE_PATH = ROOT.parent / "sunrise_ogle"
if str(SUNRISE_PATH) not in sys.path:
    sys.path.insert(0, str(SUNRISE_PATH))

import backtrader as bt

from shared.logger import setup_logger
from shared.protocol import Signal
from master.signal_broadcaster import SignalBroadcaster
from master.http_api import HTTPSignalAPI
from master.sunrise_ogle_wrapper import SunriseOgleSignalMode
from master.mt5_feed import MockMT5Feed
from master.guards.fundingpips import FundingPipsGuards, GuardConfig

log = setup_logger("replayer", log_dir=str(ROOT / "logs"))


class SignalReplayer:
    """
    Reads the Sunrise Ogle CSV, processes bars one at a time through
    the strategy, and broadcasts signals as if they were live.
    """

    def __init__(
        self,
        csv_path: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        speed: float = 60.0,           # 60 = 1 min simulated per 1 sec real
        broadcast_port_http: int = 8766,
        broadcast_port_ws: int = 8765,
        json_path: str = "data/signals_live.json",
    ):
        self.csv_path = csv_path
        self.start_date = start_date
        self.end_date = end_date
        self.speed = speed
        self.broadcast_port_http = broadcast_port_http
        self.broadcast_port_ws = broadcast_port_ws
        self.json_path = json_path

        self.feed = MockMT5Feed(csv_path)
        self.broadcaster: Optional[SignalBroadcaster] = None
        self.http_api: Optional[HTTPSignalAPI] = None
        self.guards: Optional[FundingPipsGuards] = None
        self._signal_count = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _filter_bars(self) -> List:
        """Filter bars by date range."""
        bars = self.feed.bars
        if self.start_date:
            bars = [b for b in bars if b.time >= self.start_date]
        if self.end_date:
            bars = [b for b in bars if b.time <= self.end_date]
        return bars

    def _on_signal(self, sig: Signal) -> None:
        """Callback from strategy when a signal is emitted."""
        self._signal_count += 1
        log.info(
            f">>> [Replay Signal #{self._signal_count}] "
            f"{sig.signal_type.value} @ {sig.entry_price:.2f} "
            f"SL={sig.stop_loss} TP={sig.take_profit} "
            f"(bar_time={sig.tags.get('bar_time', 'N/A')})"
        )
        # Broadcast through normal pipeline (guards + HTTP + WS + JSON)
        if self._loop and self.broadcaster:
            asyncio.run_coroutine_threadsafe(
                self._process_signal(sig), self._loop
            )

    async def _process_signal(self, sig: Signal) -> None:
        """Run guards then broadcast."""
        if self.guards:
            account = self.feed.get_account()
            balance = account.balance if account else 10000.0
            equity = balance
            allowed, reason = self.guards.check_signal(balance, equity)
            if not allowed:
                log.warning(f"Signal blocked by guards: {reason}")
                return
        await self.broadcaster.broadcast(sig)

    async def start(self) -> None:
        self._loop = asyncio.get_event_loop()

        # Start broadcaster + HTTP API
        self.broadcaster = SignalBroadcaster(
            ws_port=self.broadcast_port_ws,
            json_path=self.json_path,
            require_auth=False,
        )
        await self.broadcaster.start()

        self.http_api = HTTPSignalAPI(
            signal_source=self.broadcaster,
            port=self.broadcast_port_http,
            require_auth=False,
        )
        await self.http_api.start()

        # Initialize guards (replay mode: disable weekend/news so we can test freely)
        self.guards = FundingPipsGuards(
            config=GuardConfig(
                enabled=False  # Disabled for replay - we want to see everything
            ),
            state_file="/tmp/replay_guards.json",
        )

        bars = self._filter_bars()
        log.info(f"Replayer starting: {len(bars)} bars")
        if not bars:
            log.error("No bars in specified date range")
            return

        log.info(f"  From: {bars[0].time}")
        log.info(f"  To:   {bars[-1].time}")
        log.info(f"  Speed: {self.speed}x realtime")
        log.info(f"  HTTP:  http://127.0.0.1:{self.broadcast_port_http}")
        log.info(f"  WS:    ws://127.0.0.1:{self.broadcast_port_ws}")
        log.info("")
        log.info("Clients can connect NOW to receive signals as they fire.")
        log.info("")

        # Run strategy ONCE on all bars, but intercept signals via callback
        # and dispatch them at simulated-realtime pace.
        # The signal's tag 'bar_time' tells us when it would have fired in
        # real time.
        collected: List = []

        def collect(sig: Signal):
            collected.append(sig)

        # Run strategy in a thread to not block asyncio
        log.info("Running strategy on all bars (this may take a moment)...")
        await asyncio.get_event_loop().run_in_executor(
            None, self._run_strategy_all_bars, bars, collect
        )
        log.info(f"Strategy finished. {len(collected)} signals to dispatch.")

        if not collected:
            log.warning("No signals generated in this range.")
            await self.stop()
            return

        # Dispatch signals at simulated-realtime pace
        # sleep_per_bar = 300 / speed
        sleep_per_bar = 300.0 / self.speed

        first_bar_time = bars[0].time
        last_bar_time = bars[-1].time
        total_sim_minutes = (last_bar_time - first_bar_time).total_seconds() / 60
        real_seconds = (total_sim_minutes * 60) / self.speed
        log.info(
            f"Total simulated time: {total_sim_minutes:.0f} min "
            f"-> real time: {real_seconds:.0f} sec at {self.speed}x"
        )

        replay_start_real = time.time()

        for i, sig in enumerate(collected):
            # Extract bar_time from signal tags
            bar_time_str = sig.tags.get('bar_time', '')
            if bar_time_str:
                try:
                    bar_time = datetime.fromisoformat(bar_time_str)
                    if bar_time.tzinfo is None:
                        bar_time = bar_time.replace(tzinfo=timezone.utc)
                except Exception:
                    bar_time = first_bar_time
            else:
                bar_time = first_bar_time

            # When should this signal fire in real time?
            sim_seconds = (bar_time - first_bar_time).total_seconds()
            real_seconds_target = sim_seconds / self.speed

            # Wait until that time
            elapsed = time.time() - replay_start_real
            to_wait = real_seconds_target - elapsed
            if to_wait > 0:
                await asyncio.sleep(to_wait)

            # Update the signal's timestamp to now (so clients don't see it as expired)
            sig.timestamp = time.time()
            sig.valid_until = time.time() + 300

            log.info(
                f">>> [Replay #{i+1}/{len(collected)}] "
                f"{sig.signal_type.value} @ {sig.entry_price:.2f} "
                f"SL={sig.stop_loss} TP={sig.take_profit} "
                f"(sim_time={bar_time_str})"
            )
            self._signal_count += 1
            await self.broadcaster.broadcast(sig)

        log.info("")
        log.info("=" * 60)
        log.info(f"Replay complete. Total signals emitted: {self._signal_count}")
        log.info("=" * 60)

        # Keep server alive for 30 more sec so clients can see final state
        log.info("Keeping server up for 30 more seconds...")
        await asyncio.sleep(30)

        await self.stop()

    def _run_strategy_all_bars(self, bars: List, callback) -> None:
        """Run the Sunrise Ogle strategy on all bars at once, collecting signals."""
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
        df.name = "XAUUSD_live_feed.csv"

        cerebro = bt.Cerebro(stdstats=False, quicknotify=True)
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data)
        cerebro.broker.setcash(100000.0)
        cerebro.broker.setcommission(leverage=30.0)
        cerebro.addstrategy(
            SunriseOgleSignalMode,
            signal_callback=callback,
            signal_risk_percent=1.5,
            plot_result=False,
            # live_mode=False: emit all signals (no filtering), we handle
            # timing ourselves in the async dispatcher above.
            live_mode=False,
        )
        try:
            cerebro.run()
        except Exception as e:
            import traceback
            log.error(f"Strategy error: {e}")
            log.error(f"Traceback:\n{traceback.format_exc()}")

    async def stop(self) -> None:
        if self.http_api:
            await self.http_api.stop()
        if self.broadcaster:
            await self.broadcaster.stop()


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser(description="XAUUSD Signal Replayer")
    ap.add_argument('--csv', default=str(SUNRISE_PATH / 'data' / 'XAUUSD_5m_5Yea.csv'),
                    help='Path to XAUUSD CSV')
    ap.add_argument('--start', type=parse_date, default=None,
                    help='Start date YYYY-MM-DD')
    ap.add_argument('--end', type=parse_date, default=None,
                    help='End date YYYY-MM-DD')
    ap.add_argument('--speed', type=float, default=60.0,
                    help='Replay speed (60 = 60x realtime = 1 bar per 5s)')
    ap.add_argument('--http-port', type=int, default=8766)
    ap.add_argument('--ws-port', type=int, default=8765)
    args = ap.parse_args()

    replayer = SignalReplayer(
        csv_path=args.csv,
        start_date=args.start,
        end_date=args.end,
        speed=args.speed,
        broadcast_port_http=args.http_port,
        broadcast_port_ws=args.ws_port,
    )

    try:
        asyncio.run(replayer.start())
    except KeyboardInterrupt:
        log.info("Replayer interrupted")


if __name__ == "__main__":
    main()
