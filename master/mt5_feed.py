"""
MT5 Data Feed Module

Connects to MetaTrader 5 terminal via the official MetaQuotes Python package.
Provides:
  - Real-time bar data (live quotes)
  - Historical bar data (for indicator pre-loading)
  - Account info
  - Symbol resolution (broker-specific naming)

The MT5 terminal must be running on the same machine (Windows or Linux+Wine).
Install: pip install MetaTrader5

Docs: https://www.mql5.com/en/docs/integration/python_metatrader5
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    mt5 = None  # type: ignore

from shared.logger import get_logger

log = get_logger("mt5_feed")


TIMEFRAME_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  60,
    "H4":  240,
    "D1":  1440,
}


@dataclass
class Bar:
    """Single OHLCV bar."""
    time: datetime          # UTC bar open time
    open: float
    high: float
    low: float
    close: float
    volume: int
    spread: int = 0

    @classmethod
    def from_mt5_rate(cls, rate) -> 'Bar':
        # MT5 returns numpy.void structured records, not dicts.
        # They support [key] indexing but not .get() — use dtype.names.
        try:
            spread_val = int(rate['spread']) if 'spread' in rate.dtype.names else 0
        except Exception:
            spread_val = 0
        return cls(
            time=datetime.fromtimestamp(rate['time'], tz=timezone.utc),
            open=float(rate['open']),
            high=float(rate['high']),
            low=float(rate['low']),
            close=float(rate['close']),
            volume=int(rate['tick_volume']),
            spread=spread_val,
        )


@dataclass
class AccountInfo:
    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    profit: float
    leverage: int
    currency: str
    broker: str
    trade_mode: int           # 0=demo, 2=real


class MT5Feed:
    """
    Wrapper around MetaTrader5 Python API.
    Handles connection, symbol resolution, and data fetching.
    """

    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        symbol_candidates: Optional[List[str]] = None,
    ):
        if not MT5_AVAILABLE:
            raise RuntimeError(
                "MetaTrader5 package not installed. "
                "Run: pip install MetaTrader5"
            )
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.symbol_candidates = symbol_candidates or [
            "XAUUSD", "XAUUSD.r", "XAUUSDm", "GOLD", "GOLDm"
        ]
        self.resolved_symbol: Optional[str] = None
        self._initialized = False

    # ---------------------------------------------------------------
    # Connection lifecycle
    # ---------------------------------------------------------------

    def connect(self) -> bool:
        """Initialize MT5 terminal connection. Returns True if successful."""
        if self._initialized:
            return True

        init_kwargs = {}
        if self.path:
            init_kwargs['path'] = self.path
        if self.login is not None:
            init_kwargs['login'] = self.login
        if self.password:
            init_kwargs['password'] = self.password
        if self.server:
            init_kwargs['server'] = self.server

        if not mt5.initialize(**init_kwargs):
            err = mt5.last_error()
            log.error(f"MT5 initialize() failed: {err}")
            return False

        self._initialized = True
        term = mt5.terminal_info()
        acct = mt5.account_info()
        if term:
            log.info(
                f"MT5 connected: terminal={term.name} "
                f"company={term.company} build={term.build}"
            )
        if acct:
            log.info(
                f"Account: #{acct.login} {acct.name} "
                f"balance={acct.balance:.2f} {acct.currency} "
                f"server={acct.server}"
            )
        return True

    def disconnect(self) -> None:
        if self._initialized and mt5:
            mt5.shutdown()
            self._initialized = False
            log.info("MT5 disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ---------------------------------------------------------------
    # Symbol resolution (broker-specific naming)
    # ---------------------------------------------------------------

    def resolve_symbol(self) -> Optional[str]:
        """
        Find which variant of XAUUSD this broker uses.
        Some brokers: XAUUSD, XAUUSD.r, XAUUSDm, GOLD, GOLDm, etc.
        """
        if self.resolved_symbol:
            return self.resolved_symbol

        for candidate in self.symbol_candidates:
            info = mt5.symbol_info(candidate)
            if info is not None:
                # Make sure symbol is selected (visible in Market Watch)
                if not info.visible:
                    if not mt5.symbol_select(candidate, True):
                        log.warning(
                            f"Symbol {candidate} found but could not be "
                            f"selected"
                        )
                        continue
                self.resolved_symbol = candidate
                log.info(f"Symbol resolved: {candidate} "
                         f"(digits={info.digits}, point={info.point}, "
                         f"contract_size={info.trade_contract_size})")
                return candidate

        log.error(
            f"No XAUUSD variant found. Tried: {self.symbol_candidates}. "
            f"Check your broker's symbol list in MarketWatch."
        )
        return None

    def get_symbol_info(self) -> Optional[Dict]:
        sym = self.resolve_symbol()
        if not sym:
            return None
        info = mt5.symbol_info(sym)
        if not info:
            return None
        return {
            'name': info.name,
            'digits': info.digits,
            'point': info.point,
            'tick_size': info.trade_tick_size,
            'tick_value': info.trade_tick_value,
            'contract_size': info.trade_contract_size,
            'volume_min': info.volume_min,
            'volume_max': info.volume_max,
            'volume_step': info.volume_step,
            'trade_mode': info.trade_mode,
            'spread': info.spread,
        }

    # ---------------------------------------------------------------
    # Bar data fetching
    # ---------------------------------------------------------------

    def get_bars(
        self,
        count: int = 500,
        timeframe: str = "M5",
    ) -> List[Bar]:
        """
        Fetch N most recent closed bars.
        Note: the *current* bar (still forming) is always included as the
        last one. For strategy logic, consider using only closed bars, i.e.
        exclude the last element.
        """
        sym = self.resolve_symbol()
        if not sym:
            return []

        tf_mt5 = self._tf_to_mt5(timeframe)
        if tf_mt5 is None:
            log.error(f"Unknown timeframe: {timeframe}")
            return []

        rates = mt5.copy_rates_from_pos(sym, tf_mt5, 0, count)
        if rates is None or len(rates) == 0:
            log.warning(
                f"No bars returned for {sym} {timeframe}. "
                f"Error: {mt5.last_error()}"
            )
            return []

        return [Bar.from_mt5_rate(r) for r in rates]

    def get_bars_range(
        self,
        date_from: datetime,
        date_to: datetime,
        timeframe: str = "M5",
    ) -> List[Bar]:
        """Fetch all bars in a date range (for backtesting with live MT5 data)."""
        sym = self.resolve_symbol()
        if not sym:
            return []
        tf_mt5 = self._tf_to_mt5(timeframe)
        if tf_mt5 is None:
            return []
        rates = mt5.copy_rates_range(sym, tf_mt5, date_from, date_to)
        if rates is None:
            return []
        return [Bar.from_mt5_rate(r) for r in rates]

    def get_current_tick(self) -> Optional[Tuple[float, float]]:
        """Returns (bid, ask) of the current tick."""
        sym = self.resolve_symbol()
        if not sym:
            return None
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return None
        return (float(tick.bid), float(tick.ask))

    def _tf_to_mt5(self, timeframe: str):
        tf_map = {
            "M1":  mt5.TIMEFRAME_M1,
            "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1":  mt5.TIMEFRAME_H1,
            "H4":  mt5.TIMEFRAME_H4,
            "D1":  mt5.TIMEFRAME_D1,
        }
        return tf_map.get(timeframe.upper())

    # ---------------------------------------------------------------
    # Account info
    # ---------------------------------------------------------------

    def get_account(self) -> Optional[AccountInfo]:
        acct = mt5.account_info()
        if not acct:
            return None
        return AccountInfo(
            login=acct.login,
            balance=acct.balance,
            equity=acct.equity,
            margin=acct.margin,
            free_margin=acct.margin_free,
            profit=acct.profit,
            leverage=acct.leverage,
            currency=acct.currency,
            broker=acct.company,
            trade_mode=acct.trade_mode,
        )

    # ---------------------------------------------------------------
    # Waiting for a new bar (poll-based, for MVP)
    # ---------------------------------------------------------------

    def wait_for_new_bar(
        self,
        last_bar_time: datetime,
        timeframe: str = "M5",
        poll_interval: float = 2.0,
        max_wait_seconds: int = 360,
    ) -> Optional[Bar]:
        """
        Blocks until a new bar (later than last_bar_time) appears.
        Returns the new bar, or None on timeout.
        Use this in your strategy's main loop.
        """
        start = time.time()
        while time.time() - start < max_wait_seconds:
            bars = self.get_bars(count=2, timeframe=timeframe)
            if len(bars) >= 2:
                # bars[-1] is the current forming bar
                # bars[-2] is the last closed bar
                last_closed = bars[-2]
                if last_closed.time > last_bar_time:
                    return last_closed
            time.sleep(poll_interval)
        return None


# ---------------------------------------------------------------
# Mock MT5Feed for testing without MT5 (uses CSV)
# ---------------------------------------------------------------

class MockMT5Feed:
    """
    Offline mock using the XAUUSD CSV from the Sunrise Ogle repo.
    Useful for integration tests and development without MT5 installed.
    """
    def __init__(self, csv_path: str):
        import csv as csvmod
        self.bars: List[Bar] = []
        with open(csv_path) as f:
            reader = csvmod.DictReader(f)
            for row in reader:
                try:
                    dt_str = f"{row['Date']} {row['Time']}"
                    dt = datetime.strptime(dt_str, '%Y%m%d %H:%M:%S').replace(
                        tzinfo=timezone.utc
                    )
                    self.bars.append(Bar(
                        time=dt,
                        open=float(row['Open']),
                        high=float(row['High']),
                        low=float(row['Low']),
                        close=float(row['Close']),
                        volume=int(row['Volume']),
                    ))
                except Exception:
                    continue
        self._cursor = len(self.bars) - 500  # Start 500 bars from end
        self.resolved_symbol = "XAUUSD"

    def connect(self) -> bool:
        log.info(f"MockMT5Feed: loaded {len(self.bars)} bars from CSV")
        return True

    def disconnect(self) -> None:
        pass

    def resolve_symbol(self) -> Optional[str]:
        return "XAUUSD"

    def get_symbol_info(self) -> Optional[Dict]:
        return {
            'name': 'XAUUSD',
            'digits': 2,
            'point': 0.01,
            'tick_size': 0.01,
            'tick_value': 1.0,    # $1 per 0.01 per 1 lot (100 oz)
            'contract_size': 100.0,
            'volume_min': 0.01,
            'volume_max': 100.0,
            'volume_step': 0.01,
            'trade_mode': 0,      # demo
            'spread': 3,
        }

    def get_bars(self, count: int = 500, timeframe: str = "M5") -> List[Bar]:
        end = min(self._cursor, len(self.bars))
        start = max(0, end - count)
        return self.bars[start:end]

    def get_current_tick(self):
        if self._cursor < len(self.bars):
            c = self.bars[self._cursor].close
            return (c - 0.02, c + 0.02)  # Mock bid/ask
        return None

    def get_account(self) -> Optional[AccountInfo]:
        return AccountInfo(
            login=0, balance=10000.0, equity=10000.0, margin=0.0,
            free_margin=10000.0, profit=0.0, leverage=30,
            currency="USD", broker="MockBroker", trade_mode=0,
        )

    def wait_for_new_bar(self, last_bar_time, timeframe="M5",
                          poll_interval=2.0, max_wait_seconds=360):
        """Advance cursor (for simulation)."""
        if self._cursor < len(self.bars):
            self._cursor += 1
            return self.bars[self._cursor - 1] if self._cursor > 0 else None
        return None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


if __name__ == "__main__":
    # Smoke test: connect, print symbol info, last 5 bars
    from shared.logger import setup_logger
    setup_logger("mt5_feed")
    log.info("MT5 feed smoke test")

    if MT5_AVAILABLE:
        feed = MT5Feed()
        if feed.connect():
            info = feed.get_symbol_info()
            log.info(f"Symbol info: {info}")
            acct = feed.get_account()
            log.info(f"Account: {acct}")
            bars = feed.get_bars(count=5)
            for b in bars:
                log.info(f"  {b.time}  O={b.open} H={b.high} L={b.low} C={b.close}")
            feed.disconnect()
        else:
            log.error("Could not connect to MT5")
    else:
        log.info("MT5 not available, using mock feed")
        import sys
        from pathlib import Path
        csv_path = Path(__file__).resolve().parent.parent.parent / \
                   "sunrise_ogle" / "data" / "XAUUSD_5m_5Yea.csv"
        if csv_path.exists():
            mock = MockMT5Feed(str(csv_path))
            mock.connect()
            bars = mock.get_bars(count=5)
            for b in bars:
                log.info(f"  MOCK: {b.time}  O={b.open} H={b.high} "
                         f"L={b.low} C={b.close}")