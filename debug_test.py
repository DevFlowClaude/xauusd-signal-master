"""Simple debug test: starts just the broadcaster + HTTP API, nothing else."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from master.signal_broadcaster import SignalBroadcaster
from master.http_api import HTTPSignalAPI


async def main():
    print("[1] Starting broadcaster on port 8765...")
    b = SignalBroadcaster(
        ws_host="0.0.0.0",
        ws_port=8765,
        json_path="data/test_debug.json",
        require_auth=False,
    )
    await b.start()
    print("[2] Broadcaster started OK")

    print("[3] Starting HTTP API on port 8766...")
    h = HTTPSignalAPI(
        signal_source=b,
        host="0.0.0.0",
        port=8766,
        require_auth=False,
    )
    await h.start()
    print("[4] HTTP API started OK")

    print("[5] BOTH RUNNING. Open http://127.0.0.1:8766/health in browser.")
    print("[5] Press Ctrl+C to stop.")
    print("")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("[6] Stopping...")

    await h.stop()
    await b.stop()
    print("[7] Stopped")


if __name__ == "__main__":
    asyncio.run(main())
