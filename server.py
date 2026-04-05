"""
server.py - WebSocket server.
Receives commands from the app, validates them, delegates to stepper.
Pushes state updates back on change and on a heartbeat schedule.
"""

import asyncio
import json
import time
import websockets
import state
import stepper

HOST = "0.0.0.0"
PORT = 8765

HEARTBEAT_MOVING_S  = 0.1    # Push state every 100ms while moving
HEARTBEAT_IDLE_S    = 0.5    # Push state every 500ms while idle

# ── Connected clients ─────────────────────────────────────────────────────────

_clients: set = set()
_clients_lock = asyncio.Lock()


async def _broadcast(message: dict):
    """Send a message to all connected clients."""
    data = json.dumps(message)
    async with _clients_lock:
        if not _clients:
            return
        disconnected = set()
        for ws in _clients:
            try:
                await ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(ws)
        _clients.difference_update(disconnected)


async def _send_state():
    await _broadcast({"type": "state", **state.get()})


async def _send_error(ws, message: str):
    try:
        await ws.send(json.dumps({"type": "error", "message": message}))
    except websockets.exceptions.ConnectionClosed:
        pass


# ── Command handlers ──────────────────────────────────────────────────────────

async def _handle_command(ws, data: dict):
    cmd = data.get("type")

    # If panel is active, app commands are ignored (read-only mode)
    if state.get()["panel_active"] and cmd != "estop":
        await _send_error(ws, "Panel is active — app is in read-only mode")
        return

    if cmd == "jog_start":
        direction = data.get("direction")
        if direction not in ("left", "right"):
            await _send_error(ws, "jog_start requires direction: left or right")
            return
        if state.get()["mode"] != "jog":
            await _send_error(ws, "Not in jog mode")
            return
        stepper.jog_start(direction)

    elif cmd == "jog_stop":
        stepper.jog_stop()

    elif cmd == "distance_move":
        direction = data.get("direction")
        if direction not in ("left", "right"):
            await _send_error(ws, "distance_move requires direction: left or right")
            return
        if state.get()["mode"] != "distance":
            await _send_error(ws, "Not in distance mode")
            return
        if state.get()["moving"]:
            await _send_error(ws, "Move already in progress")
            return
        stepper.distance_move(direction)

    elif cmd == "set_speed":
        speed = data.get("speed")
        if not isinstance(speed, (int, float)) or not (0 <= speed <= 100):
            await _send_error(ws, "set_speed requires speed: 0–100")
            return
        state.set(speed_pct=float(speed))

    elif cmd == "set_distance":
        value = data.get("distance")
        units = data.get("units", "mm")
        if not isinstance(value, (int, float)) or value <= 0:
            await _send_error(ws, "set_distance requires distance > 0")
            return
        if units not in ("mm", "inch"):
            await _send_error(ws, "set_distance units must be mm or inch")
            return
        state.set_distance(float(value), units)

    elif cmd == "set_mode":
        mode = data.get("mode")
        if mode not in ("jog", "distance"):
            await _send_error(ws, "set_mode requires mode: jog or distance")
            return
        state.set(mode=mode)

    elif cmd == "position_zero":
        state.zero_position()

    elif cmd == "position_set":
        value = data.get("position")
        units = data.get("units", "mm")
        if not isinstance(value, (int, float)):
            await _send_error(ws, "position_set requires a numeric position value")
            return
        if units not in ("mm", "inch"):
            await _send_error(ws, "position_set units must be mm or inch")
            return
        state.set_position(float(value), units)

    elif cmd == "estop":
        stepper.estop()

    else:
        await _send_error(ws, f"Unknown command: {cmd}")
        return

    # Push updated state immediately after any command
    await _send_state()


# ── Connection handler ────────────────────────────────────────────────────────

async def _connection_handler(ws):
    async with _clients_lock:
        _clients.add(ws)
    print(f"[server] Client connected: {ws.remote_address}")

    # Send full state immediately on connect
    await _send_state()

    try:
        async for message in ws:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await _send_error(ws, "Invalid JSON")
                continue
            await _handle_command(ws, data)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        async with _clients_lock:
            _clients.discard(ws)
        print(f"[server] Client disconnected: {ws.remote_address}")


# ── Heartbeat / state push loop ───────────────────────────────────────────────

async def _heartbeat_loop():
    """Periodically push state to all connected clients."""
    last_state = None
    while True:
        current = state.get()
        moving = current.get("moving", False)

        # Always push if state changed
        if current != last_state:
            await _send_state()
            last_state = dict(current)

        await asyncio.sleep(HEARTBEAT_MOVING_S if moving else HEARTBEAT_IDLE_S)


# ── Panel active monitor ──────────────────────────────────────────────────────

async def _panel_monitor_loop():
    """
    Polls the panel_active GPIO pin and updates state accordingly.
    For now this is a placeholder — GPIO polling will be wired in
    once the physical panel is built.
    """
    while True:
        # TODO: read physical panel_active switch GPIO pin here
        await asyncio.sleep(0.1)


# ── Start server ──────────────────────────────────────────────────────────────

async def start():
    print(f"[server] Starting WebSocket server on ws://{HOST}:{PORT}")
    async with websockets.serve(_connection_handler, HOST, PORT):
        await asyncio.gather(
            _heartbeat_loop(),
            _panel_monitor_loop(),
            asyncio.get_event_loop().create_future()   # Run forever
        )
