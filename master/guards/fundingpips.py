"""
FundingPips Guards Module

Enforces FundingPips prop firm rules to avoid blowing the challenge.
Rules implemented:
  1. Daily loss limit (stops trading for the day if P&L <= -X%)
  2. Overall loss limit (stops bot permanently if account down >= X%)
  3. Consistency rule (max % of total profit from a single trade)
  4. Minimum trading days counter
  5. News 5-hour exclusion rule (FundingPips-specific)

These guards run BEFORE any signal is broadcast. If a guard fails,
the signal is blocked and the reason is logged.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import json

from shared.logger import get_logger

log = get_logger("fundingpips_guards")


@dataclass
class GuardConfig:
    enabled: bool = True
    max_daily_loss_percent: float = 4.5      # stop at 4.5 (actual limit 5%)
    max_overall_loss_percent: float = 9.0    # stop at 9.0 (actual limit 10%)
    consistency_max_single_trade_pct: float = 30.0
    minimum_trading_days: int = 4
    news_filter_enabled: bool = True
    news_pause_minutes_before: int = 10
    news_pause_minutes_after: int = 5
    # FundingPips rule: profits from trades opened/closed within 5 hours
    # of high-impact news are not counted towards profit target.
    # If enabled, we block NEW trades within 5 hours AFTER news event as well.
    five_hour_news_rule_enabled: bool = True
    # Friday close: no new trades after this UTC time
    friday_close_utc_hour: int = 16


@dataclass
class GuardState:
    """Persistent state across bot restarts."""
    initial_balance: Optional[float] = None
    daily_starting_balance: Optional[float] = None
    daily_starting_day: Optional[str] = None   # ISO date
    trading_days_count: int = 0
    trading_days: List[str] = field(default_factory=list)  # ISO dates
    realized_trades: List[Dict] = field(default_factory=list)
    daily_trade_blocked_until: Optional[float] = None
    permanently_disabled: bool = False
    permanent_disable_reason: str = ""


class FundingPipsGuards:
    """
    Main guard class. Each signal goes through check_signal() before broadcast.
    """

    def __init__(
        self,
        config: Optional[GuardConfig] = None,
        state_file: str = "data/guards_state.json",
    ):
        self.config = config or GuardConfig()
        self.state_file = Path(state_file)
        self.state = self._load_state()

    # ---------------------------------------------------------------
    # State persistence
    # ---------------------------------------------------------------

    def _load_state(self) -> GuardState:
        if self.state_file.exists():
            try:
                d = json.loads(self.state_file.read_text())
                return GuardState(**d)
            except Exception as e:
                log.warning(f"Could not load guard state: {e}. Starting fresh.")
        return GuardState()

    def _save_state(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.__state_to_dict(), indent=2))
        except Exception as e:
            log.error(f"Could not save guard state: {e}")

    def __state_to_dict(self) -> Dict:
        return {
            'initial_balance': self.state.initial_balance,
            'daily_starting_balance': self.state.daily_starting_balance,
            'daily_starting_day': self.state.daily_starting_day,
            'trading_days_count': self.state.trading_days_count,
            'trading_days': self.state.trading_days,
            'realized_trades': self.state.realized_trades,
            'daily_trade_blocked_until': self.state.daily_trade_blocked_until,
            'permanently_disabled': self.state.permanently_disabled,
            'permanent_disable_reason': self.state.permanent_disable_reason,
        }

    # ---------------------------------------------------------------
    # Session initialization (called on bot start with current balance)
    # ---------------------------------------------------------------

    def initialize(self, current_balance: float) -> None:
        """Called once per bot start with the current account balance."""
        if self.state.initial_balance is None:
            self.state.initial_balance = current_balance
            log.info(f"Initial balance set: ${current_balance:.2f}")

        # Use UTC date for daily reset to avoid timezone-dependent behavior.
        # FundingPips resets daily limits at 00:00 server time (UTC).
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state.daily_starting_day != today:
            # New day - reset daily baseline
            self.state.daily_starting_balance = current_balance
            self.state.daily_starting_day = today
            self.state.daily_trade_blocked_until = None
            log.info(
                f"New trading day UTC ({today}): "
                f"daily baseline ${current_balance:.2f}"
            )

        self._save_state()

    # ---------------------------------------------------------------
    # Main check - called for every signal before broadcast
    # ---------------------------------------------------------------

    def check_signal(
        self,
        current_balance: float,
        current_equity: float,
        news_events: Optional[List[Dict]] = None,
        now_utc: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Check whether a new signal can be broadcast.
        Returns (allowed, reason).
        """
        if not self.config.enabled:
            return (True, "guards_disabled")

        now = now_utc or datetime.now(timezone.utc)

        # Refresh daily baseline if day changed
        self.initialize(current_balance)

        # 1. Permanently disabled (overall DD hit)
        if self.state.permanently_disabled:
            return (False, f"permanently_disabled: {self.state.permanent_disable_reason}")

        # 2. Overall loss check
        if self.state.initial_balance:
            overall_loss_pct = (
                (self.state.initial_balance - current_equity)
                / self.state.initial_balance * 100.0
            )
            if overall_loss_pct >= self.config.max_overall_loss_percent:
                self.state.permanently_disabled = True
                self.state.permanent_disable_reason = (
                    f"overall_loss_hit: -{overall_loss_pct:.2f}% "
                    f"(limit {self.config.max_overall_loss_percent}%)"
                )
                self._save_state()
                log.critical(f"PERMANENT DISABLE: {self.state.permanent_disable_reason}")
                return (False, self.state.permanent_disable_reason)

        # 3. Daily loss check
        if self.state.daily_starting_balance:
            daily_loss_pct = (
                (self.state.daily_starting_balance - current_equity)
                / self.state.daily_starting_balance * 100.0
            )
            if daily_loss_pct >= self.config.max_daily_loss_percent:
                # Block for the rest of the day (until next UTC day)
                tomorrow = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                self.state.daily_trade_blocked_until = tomorrow.timestamp()
                self._save_state()
                return (
                    False,
                    f"daily_loss_hit: -{daily_loss_pct:.2f}% "
                    f"(limit {self.config.max_daily_loss_percent}%)"
                )

            if self.state.daily_trade_blocked_until:
                if now.timestamp() < self.state.daily_trade_blocked_until:
                    return (False, "daily_loss_cooldown_active")

        # 4. News filter
        if self.config.news_filter_enabled and news_events:
            blocked, reason = self._check_news_window(news_events, now)
            if blocked:
                return (False, reason)

        # 5. Friday late - no new trades
        # weekday() returns 4 for Friday
        if now.weekday() == 4 and now.hour >= self.config.friday_close_utc_hour:
            return (False, f"friday_late: after {self.config.friday_close_utc_hour}:00 UTC")

        # 6. Weekend check (Saturday/Sunday)
        if now.weekday() >= 5:
            return (False, "weekend: market likely closed")

        return (True, "ok")

    # ---------------------------------------------------------------
    # News filter (uses events from news_filter.py / hirek.py format)
    # ---------------------------------------------------------------

    def _check_news_window(
        self,
        news_events: List[Dict],
        now: datetime,
    ) -> Tuple[bool, str]:
        """
        news_events: list of dicts with keys: 'time' (datetime UTC),
            'currency' (str), 'impact' (str: 'High'/'Medium'/'Low'),
            'title' (str).
        Returns (blocked, reason).
        """
        pause_before = timedelta(minutes=self.config.news_pause_minutes_before)
        pause_after = timedelta(minutes=self.config.news_pause_minutes_after)
        five_hours = timedelta(hours=5)

        for ev in news_events:
            impact = str(ev.get('impact', '')).lower()
            if impact not in ('high',):
                continue
            ev_time = ev.get('time')
            if isinstance(ev_time, str):
                try:
                    ev_time = datetime.fromisoformat(ev_time.replace('Z', '+00:00'))
                except Exception:
                    continue
            if not ev_time or ev_time.tzinfo is None:
                continue

            # Short pause window around news
            if ev_time - pause_before <= now <= ev_time + pause_after:
                return (
                    True,
                    f"news_window: {ev.get('title', 'unknown')} "
                    f"at {ev_time.isoformat()}"
                )

            # FundingPips 5-hour rule: profits within 5 hours of news don't count
            # If we open a trade now, and it closes within 5 hours after news,
            # the profit won't be counted. So we block new trades for 5 hours
            # after high-impact news.
            if self.config.five_hour_news_rule_enabled:
                if ev_time <= now <= ev_time + five_hours:
                    return (
                        True,
                        f"news_5hr_rule: {ev.get('title', 'unknown')} "
                        f"(profits won't count until {(ev_time + five_hours).isoformat()})"
                    )

        return (False, "")

    # ---------------------------------------------------------------
    # Trade recording (called when a trade closes)
    # ---------------------------------------------------------------

    def record_trade_closed(
        self,
        signal_id: str,
        pnl: float,
        close_time: Optional[datetime] = None,
    ) -> None:
        close_time = close_time or datetime.now(timezone.utc)
        self.state.realized_trades.append({
            'signal_id': signal_id,
            'pnl': pnl,
            'close_time': close_time.isoformat(),
        })

        # Record trading day
        day = close_time.date().isoformat()
        if day not in self.state.trading_days:
            self.state.trading_days.append(day)
            self.state.trading_days_count = len(self.state.trading_days)

        self._save_state()

    # ---------------------------------------------------------------
    # Consistency rule check (informational - you check this manually)
    # ---------------------------------------------------------------

    def consistency_status(self) -> Dict:
        trades = self.state.realized_trades
        profits = [t['pnl'] for t in trades if t['pnl'] > 0]
        if not profits:
            return {'total_profit': 0.0, 'max_trade_pct': 0.0, 'compliant': True}
        total = sum(profits)
        max_trade = max(profits)
        max_pct = (max_trade / total * 100.0) if total > 0 else 0.0
        return {
            'total_profit': total,
            'largest_winning_trade': max_trade,
            'max_trade_pct': max_pct,
            'limit_pct': self.config.consistency_max_single_trade_pct,
            'compliant': max_pct <= self.config.consistency_max_single_trade_pct,
        }

    # ---------------------------------------------------------------
    # Status summary (for dashboard/Telegram)
    # ---------------------------------------------------------------

    def status_summary(self, current_balance: float, current_equity: float) -> Dict:
        overall_pct = 0.0
        daily_pct = 0.0
        if self.state.initial_balance:
            overall_pct = (
                (current_equity - self.state.initial_balance)
                / self.state.initial_balance * 100.0
            )
        if self.state.daily_starting_balance:
            daily_pct = (
                (current_equity - self.state.daily_starting_balance)
                / self.state.daily_starting_balance * 100.0
            )
        return {
            'permanently_disabled': self.state.permanently_disabled,
            'disable_reason': self.state.permanent_disable_reason,
            'initial_balance': self.state.initial_balance,
            'daily_starting_balance': self.state.daily_starting_balance,
            'current_equity': current_equity,
            'overall_pnl_pct': round(overall_pct, 3),
            'daily_pnl_pct': round(daily_pct, 3),
            'overall_limit_pct': -self.config.max_overall_loss_percent,
            'daily_limit_pct': -self.config.max_daily_loss_percent,
            'trading_days_count': self.state.trading_days_count,
            'min_trading_days': self.config.minimum_trading_days,
            'consistency': self.consistency_status(),
        }


if __name__ == "__main__":
    # Self-test
    from shared.logger import setup_logger
    setup_logger("fundingpips_guards")

    g = FundingPipsGuards(state_file="/tmp/guards_state_test.json")
    g.initialize(10000.0)

    # Normal signal - should pass
    ok, reason = g.check_signal(10000.0, 10000.0)
    log.info(f"Normal: allowed={ok} reason={reason}")

    # Daily loss at 3% - should pass
    ok, reason = g.check_signal(10000.0, 9700.0)
    log.info(f"-3% daily: allowed={ok} reason={reason}")

    # Daily loss at 5% - should block
    ok, reason = g.check_signal(10000.0, 9500.0)
    log.info(f"-5% daily: allowed={ok} reason={reason}")

    # Overall loss at 10% - should permanently block
    g2 = FundingPipsGuards(state_file="/tmp/guards_state_test2.json")
    g2.initialize(10000.0)
    ok, reason = g2.check_signal(10000.0, 9000.0)
    log.info(f"-10% overall: allowed={ok} reason={reason}")

    # After permanent block, subsequent checks should also fail
    ok, reason = g2.check_signal(10000.0, 10000.0)
    log.info(f"After permanent block (recovered): allowed={ok} reason={reason}")

    # Cleanup
    import os
    os.remove("/tmp/guards_state_test.json")
    os.remove("/tmp/guards_state_test2.json")
