"""
Telegram Notification Module

Sends trade signals and status updates to a Telegram chat.
Reuses the bot token from the NAS100 project via .env file.

Expected .env format:
  TELEGRAM_BOT_TOKEN=your_bot_token_here
  TELEGRAM_CHAT_ID=your_chat_id_here
"""

from __future__ import annotations
import os
import asyncio
import urllib.request
import urllib.parse
import urllib.error
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shared.logger import get_logger
from shared.protocol import Signal, SignalType

log = get_logger("telegram")


def load_env(env_path: str = ".env") -> dict:
    """Simple .env loader (no dependency on python-dotenv)."""
    path = Path(env_path)
    result = {}
    if not path.exists():
        return result
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        log.warning(f"Could not load .env: {e}")
    return result


class TelegramNotifier:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        env_path: str = ".env",
    ):
        env = load_env(env_path)
        self.bot_token = bot_token or env.get('TELEGRAM_BOT_TOKEN') or \
                         os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = chat_id or env.get('TELEGRAM_CHAT_ID') or \
                       os.environ.get('TELEGRAM_CHAT_ID', '')

        if not self.bot_token or not self.chat_id:
            log.warning(
                "Telegram not configured. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID in .env to enable notifications."
            )
            self.enabled = False
        else:
            self.enabled = True
            log.info(f"Telegram notifier initialized "
                     f"(chat_id={self.chat_id[:6]}...)")

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': 'true',
        }
        data = urllib.parse.urlencode(payload).encode('utf-8')
        try:
            with urllib.request.urlopen(url, data=data, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if not result.get('ok'):
                    log.warning(f"Telegram API error: {result}")
                    return False
                return True
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            log.warning(f"Telegram send failed: {e}")
            return False
        except Exception as e:
            log.error(f"Telegram unexpected error: {e}")
            return False

    # -----------------------------------------------------------------
    # High-level messaging helpers
    # -----------------------------------------------------------------

    def notify_signal(self, signal: Signal) -> None:
        """Send a formatted signal notification."""
        if not self.enabled:
            return

        ts = datetime.fromtimestamp(signal.timestamp, tz=timezone.utc)

        if signal.signal_type in (SignalType.OPEN_LONG, SignalType.OPEN_SHORT):
            direction = "🟢 LONG" if signal.signal_type == SignalType.OPEN_LONG else "🔴 SHORT"
            sl_str = f"{signal.stop_loss:.2f}" if signal.stop_loss else "N/A"
            tp_str = f"{signal.take_profit:.2f}" if signal.take_profit else "N/A"
            rr_str = ""
            if signal.stop_loss and signal.take_profit and signal.entry_price:
                risk = abs(signal.entry_price - signal.stop_loss)
                reward = abs(signal.take_profit - signal.entry_price)
                if risk > 0:
                    rr_str = f"1:{reward/risk:.2f}"

            text = (
                f"*{direction} {signal.symbol}*\n"
                f"`Entry : {signal.entry_price:.2f}`\n"
                f"`SL    : {sl_str}`\n"
                f"`TP    : {tp_str}`\n"
                f"`RR    : {rr_str}`\n"
                f"`Risk  : {signal.risk_percent:.1f}%`\n"
                f"`Time  : {ts.strftime('%Y-%m-%d %H:%M UTC')}`\n"
                f"_Strategy: {signal.strategy_name} {signal.timeframe}_\n"
                f"_Signal ID: {signal.signal_id[:8]}_"
            )
        elif signal.signal_type == SignalType.CLOSE_ALL:
            text = (
                f"*🔒 CLOSE ALL {signal.symbol}*\n"
                f"`Time: {ts.strftime('%Y-%m-%d %H:%M UTC')}`\n"
                f"_Reason: {signal.reason}_"
            )
        else:
            text = f"*Signal: {signal.signal_type.value}*\n`{signal.signal_id}`"

        self.send_message(text)

    def notify_trade_closed(
        self,
        signal_id: str,
        pnl: float,
        pnl_pct: Optional[float] = None,
        close_price: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        emoji = "✅" if pnl >= 0 else "❌"
        pct_str = f" ({pnl_pct:+.2f}%)" if pnl_pct is not None else ""
        price_str = f"\n`Close: {close_price:.2f}`" if close_price else ""
        text = (
            f"*{emoji} Trade Closed*\n"
            f"`PnL  : ${pnl:+.2f}{pct_str}`{price_str}\n"
            f"_Signal: {signal_id[:8]}_"
        )
        self.send_message(text)

    def notify_guard_block(self, signal_type: str, reason: str) -> None:
        if not self.enabled:
            return
        text = (
            f"*⚠️ Signal Blocked*\n"
            f"`Type  : {signal_type}`\n"
            f"`Reason: {reason}`"
        )
        self.send_message(text)

    def notify_startup(self, version: str, account_info: str = "") -> None:
        if not self.enabled:
            return
        text = (
            f"*🚀 XAUUSD Master Started*\n"
            f"`Version: {version}`\n"
            f"{account_info}"
        )
        self.send_message(text)

    def notify_shutdown(self) -> None:
        if not self.enabled:
            return
        self.send_message("*🛑 XAUUSD Master Stopped*")

    def notify_daily_summary(
        self,
        trades_today: int,
        wins: int,
        losses: int,
        pnl_today: float,
        pnl_pct_today: float,
    ) -> None:
        if not self.enabled:
            return
        wr = (wins / trades_today * 100) if trades_today else 0
        emoji = "📈" if pnl_today >= 0 else "📉"
        text = (
            f"*{emoji} Daily Summary*\n"
            f"`Trades: {trades_today} ({wins}W / {losses}L, WR {wr:.1f}%)`\n"
            f"`P&L   : ${pnl_today:+.2f} ({pnl_pct_today:+.2f}%)`"
        )
        self.send_message(text)


if __name__ == "__main__":
    from shared.logger import setup_logger
    setup_logger("telegram")

    notifier = TelegramNotifier()
    if notifier.enabled:
        # Test message
        log.info("Sending test message...")
        ok = notifier.send_message(
            "*🧪 Test from XAUUSD Master*\n"
            "_If you see this, Telegram integration works!_"
        )
        log.info(f"Test message sent: {ok}")

        # Test signal notification
        from shared.protocol import Signal, SignalType
        import time, uuid
        sig = Signal(
            signal_id=str(uuid.uuid4()),
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.OPEN_LONG,
            symbol="XAUUSD",
            entry_price=3305.50,
            stop_loss=3298.00,
            take_profit=3330.00,
            risk_percent=1.5,
            reason="Test signal",
        )
        notifier.notify_signal(sig)
    else:
        log.info("Telegram not configured - skipping test")
