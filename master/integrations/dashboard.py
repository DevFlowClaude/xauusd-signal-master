"""
XAUUSD Signal Master - Streamlit Dashboard

Displays real-time master status:
  - Active signal
  - Connected clients
  - FundingPips guards status
  - Recent trade history
  - News calendar
  - Log viewer

Usage:
  streamlit run master/integrations/dashboard.py

The dashboard reads from the master's JSON output files (not the HTTP API,
to avoid auth complications). The master must be running first.
"""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    HAS_TZ = True
except ImportError:
    HAS_TZ = False

# This file may run outside the normal import context (via streamlit)
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import streamlit as st
except ImportError:
    print("Streamlit not installed. Run: pip install streamlit")
    sys.exit(1)


SIGNALS_JSON = ROOT / "data" / "signals_live.json"
GUARDS_STATE_JSON = ROOT / "data" / "guards_state.json"


# ==========================================================================
# Streamlit app
# ==========================================================================

st.set_page_config(
    page_title="XAUUSD Signal Master",
    page_icon="📊",
    layout="wide",
)


# Dark terminal style (matching your existing project)
st.markdown("""
<style>
    .main .block-container {
        padding-top: 2rem;
        background-color: #0a0e0a;
    }
    .stApp {
        background-color: #0a0e0a;
    }
    h1, h2, h3 {
        color: #39ff7a !important;
        font-family: 'Syne', sans-serif;
    }
    .status-good { color: #39ff7a; }
    .status-warn { color: #ffb84d; }
    .status-bad { color: #ff4d4d; }
</style>
""", unsafe_allow_html=True)


st.title("📊 XAUUSD Signal Master")
st.caption("Sunrise Ogle Strategy • FundingPips 2-Step • 1.5% Risk")


# ==========================================================================
# Sidebar - controls
# ==========================================================================

with st.sidebar:
    st.header("Configuration")
    refresh_interval = st.select_slider(
        "Auto-refresh (seconds)",
        options=[0, 5, 10, 30, 60],
        value=10,
    )
    st.caption("Set to 0 to disable auto-refresh")

    st.divider()
    st.subheader("Timezone")
    if HAS_TZ:
        tz_choice = st.selectbox(
            "Display times in",
            options=["UTC", "Europe/Berlin", "Europe/London", "America/New_York"],
            index=1,  # Default: Berlin (user is in Germany)
        )
    else:
        tz_choice = "UTC"
        st.caption("(zoneinfo unavailable, UTC only)")

    st.divider()
    st.caption(f"Data dir: `{ROOT / 'data'}`")
    st.caption(f"Logs dir: `{ROOT / 'logs'}`")


def display_time(utc_ts: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Convert UTC timestamp to selected display timezone."""
    utc_dt = datetime.fromtimestamp(utc_ts, tz=timezone.utc)
    if tz_choice == "UTC" or not HAS_TZ:
        return utc_dt.strftime(fmt) + " UTC"
    try:
        local_dt = utc_dt.astimezone(ZoneInfo(tz_choice))
        tz_abbrev = local_dt.strftime("%Z")
        return local_dt.strftime(fmt) + " " + tz_abbrev
    except Exception:
        return utc_dt.strftime(fmt) + " UTC"


# ==========================================================================
# Load data
# ==========================================================================

def load_signals() -> list:
    if not SIGNALS_JSON.exists():
        return []
    try:
        return json.loads(SIGNALS_JSON.read_text())
    except Exception:
        return []


def load_guards_state() -> dict:
    if not GUARDS_STATE_JSON.exists():
        return {}
    try:
        return json.loads(GUARDS_STATE_JSON.read_text())
    except Exception:
        return {}


signals = load_signals()
guards = load_guards_state()


# ==========================================================================
# Top row - KPIs
# ==========================================================================

col1, col2, col3, col4 = st.columns(4)

with col1:
    if signals:
        last = signals[-1]
        st.metric(
            "Last Signal",
            last['signal_type'],
            display_time(last['timestamp'], '%H:%M')
        )
    else:
        st.metric("Last Signal", "None", "")

with col2:
    total_signals = len(signals)
    st.metric("Signals (last 100)", total_signals)

with col3:
    initial = guards.get('initial_balance', 0)
    st.metric(
        "Initial Balance",
        f"${initial:.2f}" if initial else "N/A",
    )

with col4:
    trading_days = guards.get('trading_days_count', 0)
    st.metric("Trading Days", trading_days)


# ==========================================================================
# Guards status
# ==========================================================================

st.header("🛡️ FundingPips Guards")

if guards:
    gcol1, gcol2, gcol3 = st.columns(3)

    with gcol1:
        disabled = guards.get('permanently_disabled', False)
        if disabled:
            st.error(f"🚨 PERMANENTLY DISABLED")
            st.caption(guards.get('permanent_disable_reason', ''))
        else:
            st.success("✅ Guards Active")

    with gcol2:
        daily_baseline = guards.get('daily_starting_balance', 0)
        daily_day = guards.get('daily_starting_day', 'N/A')
        st.info(f"**Daily Baseline (UTC day)**  \n"
                f"${daily_baseline:.2f}  \n"
                f"_{daily_day}_")

    with gcol3:
        blocked_until = guards.get('daily_trade_blocked_until')
        if blocked_until:
            blocked_dt = datetime.fromtimestamp(blocked_until, tz=timezone.utc)
            if blocked_dt > datetime.now(timezone.utc):
                st.warning(
                    "⏸️ Daily cooldown until\n" +
                    display_time(blocked_until, '%Y-%m-%d %H:%M')
                )
            else:
                st.success("✅ No daily cooldown")
        else:
            st.success("✅ No daily cooldown")
else:
    st.warning("Guards state not yet initialized. Start the master to begin tracking.")


# ==========================================================================
# Recent signals table
# ==========================================================================

st.header("📡 Recent Signals")

if signals:
    table_data = []
    for s in reversed(signals[-20:]):
        table_data.append({
            "Time": display_time(s['timestamp']),
            "Type": s.get('signal_type'),
            "Symbol": s.get('symbol'),
            "Entry": s.get('entry_price'),
            "SL": s.get('stop_loss'),
            "TP": s.get('take_profit'),
            "Risk%": s.get('risk_percent'),
            "Reason": s.get('reason', '')[:40],
        })
    st.dataframe(table_data, use_container_width=True, hide_index=True)
else:
    st.info("No signals yet. The master has not emitted any signals.")


# ==========================================================================
# Trade history
# ==========================================================================

st.header("💼 Realized Trades")

trades = guards.get('realized_trades', [])
if trades:
    table = []
    for t in reversed(trades[-30:]):
        # close_time is stored as ISO datetime string (UTC)
        close_str = t.get('close_time', '')
        formatted_time = close_str[:19].replace('T', ' ')  # fallback
        try:
            close_dt = datetime.fromisoformat(close_str)
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
            formatted_time = display_time(close_dt.timestamp())
        except Exception:
            pass
        table.append({
            "Close Time": formatted_time,
            "Signal ID": t.get('signal_id', '')[:8],
            "P&L ($)": t.get('pnl', 0),
        })
    st.dataframe(table, use_container_width=True, hide_index=True)

    # Stats
    pnls = [t.get('pnl', 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Trades", len(trades))
    with c2:
        wr = (len(wins) / len(trades) * 100) if trades else 0
        st.metric("Win Rate", f"{wr:.1f}%")
    with c3:
        st.metric("Total P&L", f"${total_pnl:+.2f}")
    with c4:
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        rr = abs(avg_win / avg_loss) if avg_loss else 0
        st.metric("Avg Win / Avg Loss", f"1:{rr:.2f}")
else:
    st.info("No trades realized yet.")


# ==========================================================================
# Auto refresh
# ==========================================================================

if refresh_interval > 0:
    import time
    time.sleep(refresh_interval)
    st.rerun()
