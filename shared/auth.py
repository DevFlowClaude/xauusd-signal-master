"""
Authentication module.

Each client has an API key. Signals are HMAC-signed so clients can verify
they come from the legitimate master (no MITM attacks, no forgeries).

For now this is a simple file-based key store. In production with multiple
subscribers we'll migrate to a database.
"""

import json
import secrets
import time
from pathlib import Path
from typing import Dict, Optional
import hashlib
import hmac


class AuthStore:
    """
    File-backed registry of authorized clients.
    Each client has: client_id, api_key (HMAC secret), registered_at, last_seen.
    """
    def __init__(self, path: str = "config/auth.json"):
        self.path = Path(path)
        self.clients: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.clients = json.loads(self.path.read_text())
            except Exception:
                self.clients = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.clients, indent=2))

    def register_client(self, client_id: str, label: str = "") -> str:
        """Register a new client and return their API key (shown once!)."""
        if client_id in self.clients:
            raise ValueError(f"Client {client_id} already registered")
        api_key = secrets.token_urlsafe(32)
        self.clients[client_id] = {
            'label': label,
            'api_key_hash': hashlib.sha256(api_key.encode()).hexdigest(),
            'registered_at': time.time(),
            'last_seen': None,
            'enabled': True,
            'total_signals_received': 0,
            'total_trades_executed': 0,
        }
        self._save()
        return api_key

    def verify_client(self, client_id: str, api_key: str) -> bool:
        if client_id not in self.clients:
            return False
        client = self.clients[client_id]
        if not client.get('enabled'):
            return False
        expected_hash = client['api_key_hash']
        actual_hash = hashlib.sha256(api_key.encode()).hexdigest()
        if hmac.compare_digest(expected_hash, actual_hash):
            client['last_seen'] = time.time()
            self._save()
            return True
        return False

    def get_shared_secret(self, client_id: str, api_key: str) -> Optional[str]:
        """
        Returns a per-client shared secret used for signal HMAC signing.
        Derived from api_key + client_id so server doesn't need to store it.
        """
        if not self.verify_client(client_id, api_key):
            return None
        return hmac.new(
            api_key.encode(),
            client_id.encode(),
            hashlib.sha256
        ).hexdigest()

    def disable_client(self, client_id: str) -> None:
        if client_id in self.clients:
            self.clients[client_id]['enabled'] = False
            self._save()

    def list_clients(self) -> Dict[str, Dict]:
        return {cid: {k: v for k, v in info.items() if k != 'api_key_hash'}
                for cid, info in self.clients.items()}


# Global master secret (for signing signals to all clients)
# In production this should be an env var
def get_master_secret() -> str:
    """Single broadcast secret used to sign all signals.
    In the simplest mode, all clients share the same verification key.
    """
    import os
    secret = os.environ.get('XAUUSD_MASTER_SECRET')
    if not secret:
        # Default dev secret - MUST be changed in production!
        return "dev_master_secret_DO_NOT_USE_IN_PRODUCTION_0123456789abcdef"
    return secret
