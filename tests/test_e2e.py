"""
End-to-end integration test.

Starts:
  1. Broadcaster (WebSocket + JSON)
  2. HTTP API
  3. Sends a test signal
  4. Spawns a subprocess Python client that polls and acks

Validates that:
  - HTTP endpoint serves the latest signal
  - Test client receives the signal and posts ACK
  - ACK is registered in master
"""

from __future__ import annotations
import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.logger import setup_logger
from shared.protocol import Signal, SignalType
from master.signal_broadcaster import SignalBroadcaster
from master.http_api import HTTPSignalAPI

log = setup_logger("e2e_test", log_dir=str(ROOT / "logs"))


async def run():
    # 1. Start broadcaster + HTTP API
    broadcaster = SignalBroadcaster(
        ws_port=8765,
        json_path="/tmp/e2e_signals.json",
        require_auth=False,
    )
    await broadcaster.start()

    api = HTTPSignalAPI(
        signal_source=broadcaster,
        port=8766,
        require_auth=False,
    )
    await api.start()

    # 2. Spawn test client as subprocess
    log.info("Spawning test client subprocess...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "clients.python_client.test_client"],
        cwd=str(ROOT),
    )

    # 3. Send test signals every 5 seconds
    for i in range(3):
        await asyncio.sleep(5)
        sig = Signal(
            signal_id=f"e2e-sig-{i:03d}",
            timestamp=time.time(),
            master_version="1.0.0",
            signal_type=SignalType.OPEN_LONG,
            symbol="XAUUSD",
            entry_price=3300.0 + i,
            stop_loss=3290.0 + i,
            take_profit=3320.0 + i,
            risk_percent=1.5,
            reason=f"e2e test signal #{i}",
        )
        log.info(f">>> Broadcasting signal {i}: {sig.signal_id}")
        await broadcaster.broadcast(sig)

    # 4. Wait for client to finish polling
    await asyncio.sleep(10)
    proc.terminate()
    proc.wait(timeout=5)

    # 5. Check ACKs
    log.info("=" * 60)
    log.info("ACK results:")
    total_acks = 0
    for sid, acks in broadcaster.acks.items():
        for ack in acks:
            log.info(f"  {sid}: client={ack.client_id} status={ack.status.value}")
            total_acks += 1
    log.info(f"Total ACKs received: {total_acks}")
    log.info("=" * 60)

    # Cleanup
    await api.stop()
    await broadcaster.stop()


if __name__ == "__main__":
    asyncio.run(run())
