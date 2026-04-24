"""
HTTP Signal API (FastAPI)

Parallel to the WebSocket broadcaster, exposes HTTP GET endpoints for clients
that prefer simple polling (e.g. MQL5 EA using native WebRequest).

Endpoints:
  GET /health                    -- health check
  GET /signals/latest            -- most recent non-expired signal
  GET /signals/history?n=10      -- last N signals
  GET /status                    -- master status, connected clients, guards
  POST /ack                      -- client acks a signal (execution status)

Auth: X-API-Key header for clients.
"""

from __future__ import annotations
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict

try:
    from fastapi import FastAPI, HTTPException, Header, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore

from shared.protocol import Signal, ClientAck, SignalStatus
from shared.auth import AuthStore
from shared.logger import get_logger

log = get_logger("http_api")


class HTTPSignalAPI:
    """
    Shared-state HTTP server. Instantiated once by the master engine.
    The broadcaster references it to make signals queryable over HTTP.
    """

    def __init__(
        self,
        signal_source,             # Reference to SignalBroadcaster
        auth_store: Optional[AuthStore] = None,
        require_auth: bool = False,
        host: str = "0.0.0.0",
        port: int = 8766,
    ):
        if not FASTAPI_AVAILABLE:
            raise RuntimeError(
                "FastAPI not installed. Run: pip install fastapi uvicorn"
            )
        self.signal_source = signal_source
        self.auth_store = auth_store or AuthStore()
        self.require_auth = require_auth
        self.host = host
        self.port = port
        self.app = FastAPI(
            title="XAUUSD Signal Master API",
            version="1.0.0",
            description="HTTP endpoint for signal subscribers",
        )
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._register_routes()

    def _check_auth(self, api_key: Optional[str], client_id: Optional[str]) -> None:
        if not self.require_auth:
            return
        if not api_key or not client_id:
            raise HTTPException(
                status_code=401,
                detail="X-API-Key and X-Client-ID headers required"
            )
        if not self.auth_store.verify_client(client_id, api_key):
            raise HTTPException(status_code=403, detail="Invalid credentials")

    def _register_routes(self):
        app = self.app

        @app.get("/health")
        async def health():
            return {"status": "ok", "timestamp": time.time()}

        @app.get("/signals/latest")
        async def signals_latest(
            x_api_key: Optional[str] = Header(None),
            x_client_id: Optional[str] = Header(None),
        ):
            self._check_auth(x_api_key, x_client_id)
            history = self.signal_source.signal_history
            # Find the most recent non-expired signal
            for sig in reversed(history):
                if not sig.is_expired():
                    return {"signal": sig.to_dict()}
            return {"signal": None}

        @app.get("/signals/history")
        async def signals_history(
            n: int = 10,
            x_api_key: Optional[str] = Header(None),
            x_client_id: Optional[str] = Header(None),
        ):
            self._check_auth(x_api_key, x_client_id)
            n = max(1, min(n, 100))
            recent = self.signal_source.signal_history[-n:]
            return {"signals": [s.to_dict() for s in recent]}

        @app.post("/ack")
        async def post_ack(
            request: Request,
            x_api_key: Optional[str] = Header(None),
            x_client_id: Optional[str] = Header(None),
        ):
            self._check_auth(x_api_key, x_client_id)
            raw = await request.body()
            try:
                ack = ClientAck.from_json(raw.decode('utf-8'))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid ACK: {e}")
            self.signal_source.acks.setdefault(ack.signal_id, []).append(ack)
            log.info(
                f"HTTP ACK received from {ack.client_id}: "
                f"signal={ack.signal_id} status={ack.status.value}"
            )
            return {"accepted": True}

        @app.get("/status")
        async def status():
            return {
                "master_version": "1.0.0",
                "connected_ws_clients": len(self.signal_source.clients),
                "signal_history_size": len(self.signal_source.signal_history),
                "server_time": time.time(),
            }

    async def start(self) -> None:
        if not FASTAPI_AVAILABLE:
            return
        import uvicorn
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",   # Don't spam our logs
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        # Give it a moment to start
        await asyncio.sleep(0.5)
        log.info(f"HTTP API listening on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
                except asyncio.TimeoutError:
                    pass
        log.info("HTTP API stopped")


if __name__ == "__main__":
    # Self-test
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))
    from shared.logger import setup_logger
    from master.signal_broadcaster import SignalBroadcaster
    from shared.protocol import Signal, SignalType

    setup_logger("http_api")

    async def main():
        broadcaster = SignalBroadcaster(
            ws_port=8765,
            json_path="/tmp/test_signals.json",
            require_auth=False,
        )
        await broadcaster.start()

        api = HTTPSignalAPI(
            signal_source=broadcaster,
            port=8766,
            require_auth=False,
        )
        await api.start()

        # Broadcast a test signal
        sig = Signal(
            signal_id="http-test-001",
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.OPEN_LONG,
            symbol="XAUUSD",
            entry_price=3300.50,
            stop_loss=3295.00,
            take_profit=3320.00,
            risk_percent=1.5,
        )
        await broadcaster.broadcast(sig)

        # Keep serving for 8 seconds so we can test with curl
        log.info("Test: curl http://127.0.0.1:8766/signals/latest")
        log.info("Test: curl http://127.0.0.1:8766/health")
        await asyncio.sleep(8)

        await api.stop()
        await broadcaster.stop()

    asyncio.run(main())
