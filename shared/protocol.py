"""
XAUUSD Signal Provider - Protocol Definition

Defines the signal format used between the Master process and Clients.
Any client implementation (MT5 EA, Python test client, cTrader bridge) must
conform to this protocol.

Signal lifecycle:
  1. Master generates signal (from strategy)
  2. Master broadcasts via WebSocket + writes to JSON file
  3. Clients receive signal, verify auth, execute trade
  4. Clients report back execution status (ACK)
  5. Master logs everything for audit
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any
import json
import time
import hashlib


class SignalType(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_ALL = "CLOSE_ALL"
    MODIFY_SL = "MODIFY_SL"
    MODIFY_TP = "MODIFY_TP"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    HEARTBEAT = "HEARTBEAT"
    STATUS = "STATUS"


class SignalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Signal:
    """
    A trading signal broadcast from Master to Clients.

    Note on risk: the Master sends a *risk percentage*, not absolute lot size.
    Each client calculates its own lot size based on its account balance.
    This ensures every client takes the same proportional risk regardless of
    account size.
    """
    # Identity
    signal_id: str                          # Unique UUID
    timestamp: float                        # Unix epoch seconds
    master_version: str                     # e.g. "1.0.0"

    # Trade instructions
    signal_type: SignalType                 # OPEN_LONG, OPEN_SHORT, etc.
    symbol: str                             # "XAUUSD"
    entry_price: Optional[float] = None     # Suggested entry (may be market)
    stop_loss: Optional[float] = None       # SL price (absolute)
    take_profit: Optional[float] = None     # TP price (absolute)
    risk_percent: float = 1.5               # % of account to risk (1.5 default)

    # Strategy context
    strategy_name: str = "sunrise_ogle"
    timeframe: str = "M5"
    atr_value: Optional[float] = None       # For client-side sanity check
    confidence: float = 1.0                 # 0.0 - 1.0

    # Expiry
    valid_until: Optional[float] = None     # Unix epoch, None = 5 min default

    # Metadata
    reason: str = ""                        # Human-readable reason
    tags: Dict[str, Any] = field(default_factory=dict)

    # Authentication
    signature: str = ""                     # HMAC signature for verification

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['signal_type'] = self.signal_type.value if isinstance(self.signal_type, SignalType) else self.signal_type
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(',', ':'), sort_keys=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Signal':
        if isinstance(d.get('signal_type'), str):
            d['signal_type'] = SignalType(d['signal_type'])
        return cls(**d)

    @classmethod
    def from_json(cls, js: str) -> 'Signal':
        return cls.from_dict(json.loads(js))

    def sign(self, secret_key: str) -> None:
        """Sign the signal with HMAC for client verification."""
        import hmac
        payload = self._signing_payload()
        self.signature = hmac.new(
            secret_key.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def verify(self, secret_key: str) -> bool:
        """Verify signal authenticity."""
        import hmac
        if not self.signature:
            return False
        expected_signature = self.signature
        payload = self._signing_payload()
        actual = hmac.new(
            secret_key.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_signature, actual)

    def _signing_payload(self) -> str:
        """Canonical string used for HMAC signing (excludes signature field)."""
        d = self.to_dict()
        d.pop('signature', None)
        return json.dumps(d, separators=(',', ':'), sort_keys=True)

    def is_expired(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        if self.valid_until is None:
            return (now - self.timestamp) > 300  # 5 min default
        return now > self.valid_until


@dataclass
class ClientAck:
    """Acknowledgment from a client after receiving/executing a signal."""
    signal_id: str
    client_id: str
    timestamp: float
    status: SignalStatus
    executed_price: Optional[float] = None
    executed_lots: Optional[float] = None
    ticket: Optional[int] = None           # MT5 order ticket
    error_message: str = ""
    account_balance: Optional[float] = None
    account_equity: Optional[float] = None

    def to_json(self) -> str:
        d = asdict(self)
        d['status'] = self.status.value if isinstance(self.status, SignalStatus) else self.status
        return json.dumps(d, separators=(',', ':'), sort_keys=True)

    @classmethod
    def from_json(cls, js: str) -> 'ClientAck':
        d = json.loads(js)
        if isinstance(d.get('status'), str):
            d['status'] = SignalStatus(d['status'])
        return cls(**d)


@dataclass
class ClientInfo:
    """Info registered by client on connect."""
    client_id: str
    account_number: int
    broker: str
    account_balance: float
    account_currency: str
    leverage: int
    risk_override: Optional[float] = None   # Client can cap risk% lower
    max_lot: Optional[float] = None
    directions_allowed: str = "BOTH"        # "LONG", "SHORT", or "BOTH"
    prop_firm: Optional[str] = None         # "FundingPips", "FTMO", "demo", etc.

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(',', ':'))
