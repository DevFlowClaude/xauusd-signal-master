"""
Signal Broadcaster

Broadcasts signals to clients via two channels:
  1. WebSocket server (real-time push to connected clients)
  2. JSON file (backup - clients can poll this)

Clients subscribe to the WebSocket server and authenticate on connect.
Every signal is HMAC-signed so clients can verify authenticity.
"""

from __future__ import annotations
import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Set, Optional, List, Dict

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    websockets = None  # type: ignore

from shared.protocol import Signal, ClientAck, ClientInfo, SignalType, SignalStatus
from shared.auth import AuthStore, get_master_secret
from shared.logger import get_logger

log = get_logger("broadcaster")


class SignalBroadcaster:
    """
    Central hub. Holds the list of connected clients and broadcasts signals.
    Also writes each signal to a JSON file as a backup channel.
    """

    def __init__(
        self,
        ws_host: str = "0.0.0.0",
        ws_port: int = 8765,
        json_path: str = "data/signals_live.json",
        auth_store: Optional[AuthStore] = None,
        require_auth: bool = True,
    ):
        self.ws_host = ws_host
        self.ws_port = ws_port
        self.json_path = Path(json_path)
        self.auth_store = auth_store or AuthStore()
        self.require_auth = require_auth

        # Connected clients: websocket -> {client_id, client_info}
        self.clients: Dict = {}
        # Signal history (last 100 signals, for new-client replay)
        self.signal_history: List[Signal] = []
        # ACK tracking: signal_id -> list of ACKs
        self.acks: Dict[str, List[ClientAck]] = {}

        self.master_secret = get_master_secret()
        self._server = None
        self._running = False

    # ---------------------------------------------------------------
    # Public API - called from the master engine
    # ---------------------------------------------------------------

    async def broadcast(self, signal: Signal) -> None:
        """Send signal to all connected clients + write JSON file."""
        # Sign the signal
        signal.sign(self.master_secret)

        # Keep in history (ring buffer of 100)
        self.signal_history.append(signal)
        if len(self.signal_history) > 100:
            self.signal_history = self.signal_history[-100:]

        # JSON file (always write, as backup)
        self._write_json_backup(signal)

        # WebSocket broadcast
        if self.clients:
            msg = json.dumps({
                "type": "signal",
                "data": signal.to_dict(),
            })
            # Copy to avoid mutation during iteration
            to_send = list(self.clients.keys())
            log.info(
                f"Broadcasting signal {signal.signal_id} "
                f"({signal.signal_type.value}) to {len(to_send)} clients"
            )
            # Send concurrently
            results = await asyncio.gather(
                *[self._safe_send(ws, msg) for ws in to_send],
                return_exceptions=True
            )
            failed = sum(1 for r in results if isinstance(r, Exception))
            if failed:
                log.warning(f"{failed}/{len(to_send)} sends failed")
        else:
            log.info(
                f"Signal {signal.signal_id} written to JSON only "
                f"(no WebSocket clients connected)"
            )

    async def _safe_send(self, ws, msg: str) -> None:
        try:
            await ws.send(msg)
        except Exception as e:
            log.warning(f"Send failed to {self.clients.get(ws, {}).get('client_id', 'unknown')}: {e}")
            # Remove broken connection
            self.clients.pop(ws, None)

    def _write_json_backup(self, signal: Signal) -> None:
        """Append signal to the JSON backup file (as an array of recent signals)."""
        try:
            self.json_path.parent.mkdir(parents=True, exist_ok=True)
            # Load existing
            existing = []
            if self.json_path.exists():
                try:
                    existing = json.loads(self.json_path.read_text())
                    if not isinstance(existing, list):
                        existing = []
                except Exception:
                    existing = []
            # Append and cap at 100
            existing.append(signal.to_dict())
            existing = existing[-100:]
            self.json_path.write_text(json.dumps(existing, indent=2))
        except Exception as e:
            log.error(f"JSON backup write failed: {e}")

    # ---------------------------------------------------------------
    # WebSocket server
    # ---------------------------------------------------------------

    async def _handle_client(self, websocket):
        """Handle a single client connection lifecycle."""
        client_id = None
        try:
            # 1. Wait for auth message (first message)
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            except asyncio.TimeoutError:
                await websocket.close(code=1008, reason="Auth timeout")
                return

            try:
                auth_msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=1008, reason="Invalid auth JSON")
                return

            if auth_msg.get('type') != 'auth':
                await websocket.close(code=1008, reason="First message must be auth")
                return

            client_id = auth_msg.get('client_id', 'unknown')
            api_key = auth_msg.get('api_key', '')

            if self.require_auth:
                if not self.auth_store.verify_client(client_id, api_key):
                    await websocket.send(json.dumps({
                        'type': 'auth_failed',
                        'message': 'Invalid client_id or api_key'
                    }))
                    await websocket.close(code=1008, reason="Auth failed")
                    log.warning(f"Auth failed for client {client_id}")
                    return

            # Parse client info (optional, sent with auth)
            client_info_raw = auth_msg.get('client_info')
            client_info = None
            if client_info_raw:
                try:
                    client_info = ClientInfo(**client_info_raw)
                except Exception as e:
                    log.warning(f"Client info parse failed: {e}")

            # 2. Register client
            self.clients[websocket] = {
                'client_id': client_id,
                'info': client_info,
                'connected_at': time.time(),
            }
            log.info(
                f"Client connected: {client_id} "
                f"({client_info.broker if client_info else 'no info'})"
            )

            # 3. Send auth success + master secret for signal verification
            await websocket.send(json.dumps({
                'type': 'auth_success',
                'master_version': '1.0.0',
                'master_secret': self.master_secret,  # So client can verify signatures
                'server_time': datetime.now(timezone.utc).isoformat(),
            }))

            # 4. Optionally replay recent non-expired signals
            recent = [s for s in self.signal_history[-10:] if not s.is_expired()]
            if recent:
                await websocket.send(json.dumps({
                    'type': 'signal_history',
                    'data': [s.to_dict() for s in recent],
                }))

            # 5. Message handling loop
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get('type')
                if mtype == 'ack':
                    ack_data = msg.get('data', {})
                    try:
                        ack = ClientAck.from_json(json.dumps(ack_data))
                        self.acks.setdefault(ack.signal_id, []).append(ack)
                        log.info(
                            f"ACK from {client_id}: signal={ack.signal_id} "
                            f"status={ack.status.value}"
                        )
                    except Exception as e:
                        log.warning(f"ACK parse error from {client_id}: {e}")
                elif mtype == 'heartbeat':
                    await websocket.send(json.dumps({'type': 'heartbeat_ack'}))
                elif mtype == 'status':
                    await websocket.send(json.dumps({
                        'type': 'status_response',
                        'master_version': '1.0.0',
                        'connected_clients': len(self.clients),
                        'server_time': datetime.now(timezone.utc).isoformat(),
                    }))

        except Exception as e:
            log.error(f"Client handler error ({client_id}): {e}")
        finally:
            self.clients.pop(websocket, None)
            if client_id:
                log.info(f"Client disconnected: {client_id}")

    async def start(self) -> None:
        if not WEBSOCKETS_AVAILABLE:
            log.error("websockets package not installed. Run: pip install websockets")
            log.info("Broadcaster running in JSON-only mode")
            self._running = True
            return

        self._server = await websockets.serve(
            self._handle_client,
            self.ws_host,
            self.ws_port,
        )
        self._running = True
        log.info(f"WebSocket server listening on ws://{self.ws_host}:{self.ws_port}")

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            log.info("WebSocket server stopped")

    # ---------------------------------------------------------------
    # Status / stats (for dashboard)
    # ---------------------------------------------------------------

    def connected_clients(self) -> List[Dict]:
        result = []
        for ws, meta in self.clients.items():
            info = meta.get('info')
            result.append({
                'client_id': meta['client_id'],
                'broker': info.broker if info else None,
                'prop_firm': info.prop_firm if info else None,
                'account_balance': info.account_balance if info else None,
                'connected_since': datetime.fromtimestamp(
                    meta['connected_at'], tz=timezone.utc
                ).isoformat(),
            })
        return result


if __name__ == "__main__":
    # Self-test: start broadcaster and send a test signal
    from shared.logger import setup_logger
    setup_logger("broadcaster")

    async def main():
        b = SignalBroadcaster(
            ws_port=8765,
            json_path="/tmp/test_signals.json",
            require_auth=False,  # Easier for testing
        )
        await b.start()

        # Send a test signal
        sig = Signal(
            signal_id="test-001",
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.OPEN_LONG,
            symbol="XAUUSD",
            entry_price=3300.50,
            stop_loss=3295.00,
            take_profit=3320.00,
            risk_percent=1.5,
            reason="test signal",
        )
        await b.broadcast(sig)
        log.info(f"Test signal written. Check /tmp/test_signals.json")
        log.info(f"Listening for 5 seconds...")
        await asyncio.sleep(5)
        await b.stop()

    asyncio.run(main())
