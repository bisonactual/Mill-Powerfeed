"""
main.py - Entry point for the power feed controller.
Sets up GPIO, starts the WebSocket server, handles clean shutdown.
"""

import asyncio
import signal
import sys
import stepper
import server


def _shutdown(sig, frame):
    print(f"\n[main] Caught signal {sig} — shutting down cleanly")
    stepper.cleanup()
    sys.exit(0)


async def main():
    # Register shutdown handlers
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("[main] Power feed controller starting")
    print("[main] Initialising stepper motor")
    stepper.setup()

    print("[main] Starting WebSocket server")
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
