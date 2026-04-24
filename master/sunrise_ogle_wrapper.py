"""
Sunrise Ogle Strategy Wrapper (Live Signal Mode)

Intercepts the Backtrader strategy's order-placement calls and converts
them into signals broadcast to clients instead of executing locally.

The original strategy (sunrise_ogle_xauusd.py) remains UNTOUCHED. We import
its SunriseOgle class, then subclass it to override buy(), sell(), and
close() methods.

This approach has major benefits:
  - Original code is the tested, validated version from GitHub
  - Our backtest numbers remain reproducible
  - If the original repo updates, we pull and our wrapper still works
"""

from __future__ import annotations
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Add the sunrise_ogle repo to Python path
SUNRISE_REPO_PATH = Path(__file__).resolve().parent.parent.parent / "sunrise_ogle"
if str(SUNRISE_REPO_PATH) not in sys.path:
    sys.path.insert(0, str(SUNRISE_REPO_PATH))

import backtrader as bt  # noqa: E402

# Import the original strategy (untouched)
from src.strategy.sunrise_ogle_xauusd import SunriseOgle  # noqa: E402

from shared.protocol import Signal, SignalType  # noqa: E402
from shared.logger import get_logger  # noqa: E402

log = get_logger("sunrise_wrapper")


class SunriseOgleSignalMode(SunriseOgle):
    """
    Live-trading wrapper: instead of placing orders on the simulated broker,
    emits Signal objects via a callback.

    Usage:
      callback = lambda signal: asyncio.run_coroutine_threadsafe(
          broadcaster.broadcast(signal), loop
      )
      cerebro.addstrategy(SunriseOgleSignalMode, signal_callback=callback, ...)
    """

    params = dict(
        # Parent params inherited - we just add our own
        signal_callback=None,         # Callable[[Signal], None]
        signal_symbol="XAUUSD",       # What symbol to put in the Signal
        signal_risk_percent=1.5,      # Client-side risk% (not strategy risk)
        signal_strategy_name="sunrise_ogle",
        signal_valid_seconds=300,     # Signal expires after N seconds
        # Disable actual order sizing / execution side effects when in signal mode
        disable_live_orders=True,
        # Live mode: only emit signals for trades decided on the LATEST bar,
        # not on historical bars processed during warmup.
        # When True, we check the bar timestamp against live_start_time.
        live_mode=False,
        live_start_time=None,         # datetime; signals before this are filtered
    )

    def __init__(self):
        # Keep track of our "virtual" position so we behave correctly
        # even though we never actually place a real order locally.
        self._virtual_position = 0           # +1 long, -1 short, 0 flat
        self._virtual_entry_price: Optional[float] = None
        self._virtual_sl: Optional[float] = None
        self._virtual_tp: Optional[float] = None
        self._last_signal_id: Optional[str] = None

        super().__init__()

        # Parent's __init__ overwrites _data_filename to the DataFrame object
        # when data is PandasData. Fix it here unconditionally so that
        # subsequent code (validation, reports) works correctly.
        self._data_filename = "XAUUSD_live_feed.csv"

        if self.p.signal_callback is None:
            log.warning(
                "No signal_callback provided! Signals will be logged only."
            )

    def _apply_forex_config(self):
        """
        Override parent's _apply_forex_config to avoid the DataFrame.upper()
        crash when data is fed via PandasData. We know we're trading XAUUSD,
        so we hardcode the filename first, then call parent.
        """
        # Force string before parent reads it
        current = getattr(self, '_data_filename', None)
        if not isinstance(current, str):
            self._data_filename = "XAUUSD_live_feed.csv"
        return super()._apply_forex_config()

    def _get_forex_instrument_config(self, instrument_name=None):
        """Override to handle non-string _data_filename safely."""
        current = getattr(self, '_data_filename', None)
        if not isinstance(current, str):
            self._data_filename = "XAUUSD_live_feed.csv"
        return super()._get_forex_instrument_config(instrument_name)

    def _validate_forex_setup(self):
        """Override to handle non-string _data_filename safely."""
        current = getattr(self, '_data_filename', None)
        if not isinstance(current, str):
            self._data_filename = "XAUUSD_live_feed.csv"
        return super()._validate_forex_setup()

    def start(self):
        """Called by Backtrader after init, before processing starts."""
        super().start()
        # Override _data_filename if parent set it to a non-string (e.g. DataFrame)
        if not isinstance(getattr(self, '_data_filename', None), str):
            self._data_filename = "XAUUSD_live_feed.csv"

    # -----------------------------------------------------------------
    # Override order placement methods
    # -----------------------------------------------------------------

    def buy(self, **kwargs):
        """Intercept buy() - emit OPEN_LONG signal instead of actual order."""
        if self.p.disable_live_orders:
            self._emit_signal_open(True, **kwargs)
            # Fake order object for strategy internal tracking
            return self._create_fake_order("buy")
        return super().buy(**kwargs)

    def sell(self, **kwargs):
        """Intercept sell() - emit OPEN_SHORT signal."""
        if self.p.disable_live_orders:
            self._emit_signal_open(False, **kwargs)
            return self._create_fake_order("sell")
        return super().sell(**kwargs)

    def close(self, **kwargs):
        """Intercept close() - emit CLOSE_ALL signal."""
        if self.p.disable_live_orders:
            self._emit_signal_close(**kwargs)
            self._virtual_position = 0
            return self._create_fake_order("close")
        return super().close(**kwargs)

    # Backtrader uses buy_bracket() sometimes - intercept that too
    def buy_bracket(self, **kwargs):
        if self.p.disable_live_orders:
            sl = kwargs.get('stopprice') or kwargs.get('limitprice')
            tp = kwargs.get('limitprice')
            self._emit_signal_open(True, sl=sl, tp=tp, **kwargs)
            return (self._create_fake_order("buy"),
                    self._create_fake_order("stop"),
                    self._create_fake_order("limit"))
        return super().buy_bracket(**kwargs)

    def sell_bracket(self, **kwargs):
        if self.p.disable_live_orders:
            sl = kwargs.get('stopprice') or kwargs.get('limitprice')
            tp = kwargs.get('limitprice')
            self._emit_signal_open(False, sl=sl, tp=tp, **kwargs)
            return (self._create_fake_order("sell"),
                    self._create_fake_order("stop"),
                    self._create_fake_order("limit"))
        return super().sell_bracket(**kwargs)

    # -----------------------------------------------------------------
    # Signal emission
    # -----------------------------------------------------------------

    def _is_live_bar(self) -> bool:
        """
        In live_mode, only emit signals if the bar we're processing is
        newer than live_start_time. This prevents duplicate historical
        signals during warmup of each strategy run.
        """
        if not self.p.live_mode:
            return True   # Not in live mode -> emit everything (for backtest)
        if self.p.live_start_time is None:
            return True
        try:
            bar_time = self.data.datetime.datetime(0)
            # Make comparable (both naive or both aware)
            if bar_time.tzinfo is None and self.p.live_start_time.tzinfo is not None:
                from datetime import timezone
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            return bar_time >= self.p.live_start_time
        except Exception:
            return True

    def _emit_signal_open(self, is_long: bool, **kwargs):
        """Build and emit an OPEN_LONG or OPEN_SHORT signal."""
        # Figure out entry price, SL, TP from the strategy state
        current_close = float(self.data.close[0])
        # The strategy tracks stop_level and take_level attributes
        sl = kwargs.get('sl') or kwargs.get('stopprice')
        tp = kwargs.get('tp') or kwargs.get('limitprice')
        if sl is None:
            sl = getattr(self, 'stop_level', None)
        if tp is None:
            tp = getattr(self, 'take_level', None)

        atr = None
        try:
            atr = float(self.atr[0]) if hasattr(self, 'atr') else None
        except Exception:
            pass

        sig = Signal(
            signal_id=str(uuid.uuid4()),
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.OPEN_LONG if is_long else SignalType.OPEN_SHORT,
            symbol=self.p.signal_symbol,
            entry_price=current_close,
            stop_loss=float(sl) if sl else None,
            take_profit=float(tp) if tp else None,
            risk_percent=self.p.signal_risk_percent,
            strategy_name=self.p.signal_strategy_name,
            timeframe="M5",
            atr_value=atr,
            valid_until=time.time() + self.p.signal_valid_seconds,
            reason=f"Sunrise Ogle {'LONG' if is_long else 'SHORT'} entry at bar {len(self)}",
            tags={
                'bar_index': len(self),
                'bar_time': self.data.datetime.datetime(0).isoformat(),
            }
        )

        self._virtual_position = 1 if is_long else -1
        self._virtual_entry_price = current_close
        self._virtual_sl = float(sl) if sl else None
        self._virtual_tp = float(tp) if tp else None
        self._last_signal_id = sig.signal_id

        # In live_mode, only forward signals from recent/live bars
        if not self._is_live_bar():
            log.debug(
                f"Historical signal filtered (not emitted): "
                f"{sig.signal_type.value} @ bar {len(self)}"
            )
            return

        log.info(
            f"SIGNAL EMIT: {sig.signal_type.value} @ {current_close:.2f} "
            f"SL={sig.stop_loss} TP={sig.take_profit}"
        )

        if self.p.signal_callback:
            try:
                self.p.signal_callback(sig)
            except Exception as e:
                log.error(f"signal_callback failed: {e}")

    def _emit_signal_close(self, **kwargs):
        sig = Signal(
            signal_id=str(uuid.uuid4()),
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.CLOSE_ALL,
            symbol=self.p.signal_symbol,
            risk_percent=0.0,
            strategy_name=self.p.signal_strategy_name,
            timeframe="M5",
            valid_until=time.time() + self.p.signal_valid_seconds,
            reason=f"Sunrise Ogle close at bar {len(self)}",
            tags={
                'bar_index': len(self),
                'bar_time': self.data.datetime.datetime(0).isoformat(),
                'related_open_signal': self._last_signal_id,
            }
        )

        if not self._is_live_bar():
            log.debug(f"Historical CLOSE filtered (not emitted) @ bar {len(self)}")
            return

        log.info(f"SIGNAL EMIT: CLOSE_ALL @ {float(self.data.close[0]):.2f}")

        if self.p.signal_callback:
            try:
                self.p.signal_callback(sig)
            except Exception as e:
                log.error(f"signal_callback failed: {e}")

    def _create_fake_order(self, side: str):
        """
        Create a minimal fake order object so the parent strategy's
        logic (which sometimes inspects order.ref, order.status) doesn't crash.
        """
        class FakeOrder:
            ref = -1
            status = 0
            executed = type('obj', (), {'price': 0.0, 'size': 0, 'value': 0.0})()
            info = {'fake': True, 'side': side}
        return FakeOrder()

    # -----------------------------------------------------------------
    # Override position tracking so the strategy believes its trades worked
    # -----------------------------------------------------------------

    def getposition(self, data=None, broker=None):
        """
        Backtrader uses self.position to check if we have a position.
        We override to return a fake position matching _virtual_position.
        """
        # In signal mode, we don't want to interfere with the strategy's
        # decision process, so we let it believe each entry is accepted.
        # The strategy will call buy() and expect a position to form.
        # We fake this.
        real_pos = super().getposition(data, broker) if data else super().position
        if self.p.disable_live_orders and self._virtual_position != 0:
            # Return a fake position object
            class FakePosition:
                size = 1 * self._virtual_position
                price = self._virtual_entry_price or 0.0
                upclosed = 0
                upopened = 0
                def __bool__(self): return self.size != 0
            return FakePosition()
        return real_pos


if __name__ == "__main__":
    from shared.logger import setup_logger
    setup_logger("sunrise_wrapper")

    log.info("Sunrise Ogle wrapper import test")

    # Verify we can import the original strategy
    log.info(f"SunriseOgle class: {SunriseOgle}")
    log.info(f"SunriseOgleSignalMode class: {SunriseOgleSignalMode}")

    # Verify params inheritance
    params = SunriseOgleSignalMode.params._getpairs()
    our_params = [p for p in params if p[0].startswith('signal_')]
    log.info(f"Our signal params: {len(our_params)}")
    for name, default in our_params:
        log.info(f"  {name} = {default}")

    log.info("Import successful!")
