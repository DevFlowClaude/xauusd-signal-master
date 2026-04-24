"""
Python Test Client

Simulates an MT5 EA client for end-to-end testing.
Polls the master's HTTP endpoint, prints received signals, reports fake ACKs.

Usage:
  python -m clients.python_client.test_client
"""

from __future__ import annotations
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from shared.logger import setup_logger


log = setup_logger("test_client", log_dir=str(ROOT / "logs"))


class TestClient:
    def __init__(
        self,
        master_url: str = "http://192.168.178.130:8766",
        client_id: str = "py_test_client",
        api_key: str = "",
        poll_interval: float = 2.0,
    ):
        self.master_url = master_url.rstrip('/')
        self.client_id = client_id
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.last_signal_id = None

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h['X-API-Key'] = self.api_key
            h['X-Client-ID'] = self.client_id
        return h

    def fetch_latest(self) -> dict:
        url = self.master_url + "/signals/latest"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            log.warning(f"Fetch failed: {e}")
            return {}

    def post_ack(self, signal_id: str, status: str = "FILLED") -> bool:
        url = self.master_url + "/ack"
        body = json.dumps({
            'signal_id': signal_id,
            'client_id': self.client_id,
            'timestamp': time.time(),
            'status': status,
            'executed_price': 0.0,
            'executed_lots': 0.0,
            'ticket': 0,
            'error_message': '',
            'account_balance': 10000.0,
            'account_equity': 10000.0,
        }).encode('utf-8')
        headers = dict(self._headers())
        headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            log.warning(f"ACK post failed: {e}")
            return False

    def run(self, duration_seconds: int = 60) -> None:
        log.info(f"Test client starting. Polling {self.master_url} every "
                 f"{self.poll_interval}s for {duration_seconds}s...")
        start = time.time()
        polls = 0
        while time.time() - start < duration_seconds:
            polls += 1
            data = self.fetch_latest()
            sig = data.get('signal')
            if sig:
                sid = sig.get('signal_id')
                if sid and sid != self.last_signal_id:
                    log.info(f"NEW SIGNAL: {sig.get('signal_type')} "
                             f"entry={sig.get('entry_price')} "
                             f"SL={sig.get('stop_loss')} TP={sig.get('take_profit')} "
                             f"risk={sig.get('risk_percent')}%")
                    log.info(f"  ... (faking execution, sending ACK)")
                    self.post_ack(sid, "FILLED")
                    self.last_signal_id = sid
            time.sleep(self.poll_interval)
        log.info(f"Test client finished ({polls} polls)")


if __name__ == "__main__":
    c = TestClient()
    c.run(duration_seconds=30)
