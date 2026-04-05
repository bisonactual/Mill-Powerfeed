"""
stepper.py - Motor control layer.
Talks to the DM542T via GPIO. Handles jog, distance moves,
acceleration ramping, position tracking and de-energising.
Knows nothing about WebSockets or the app.
"""

import time
import threading
import math

try:
    import RPi.GPIO as GPIO
    SIMULATION = False
except ImportError:
    # Running on a non-Pi (development / testing)
    print("[stepper] RPi.GPIO not found — running in simulation mode")
    SIMULATION = True

import state

# ── GPIO setup ────────────────────────────────────────────────────────────────

def setup():
    if SIMULATION:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(state.PIN_STEP, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(state.PIN_DIR,  GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(state.PIN_ENA,  GPIO.OUT, initial=GPIO.LOW)   # LOW = enabled on DM542T
    energise()


def cleanup():
    de_energise()
    if not SIMULATION:
        GPIO.cleanup()


# ── Energise / de-energise ────────────────────────────────────────────────────

def energise():
    state.set(energised=True)
    if not SIMULATION:
        GPIO.output(state.PIN_ENA, GPIO.LOW)    # LOW enables the DM542T
    print("[stepper] Motor energised")


def de_energise():
    state.set(energised=False, moving=False, direction=None)
    if not SIMULATION:
        GPIO.output(state.PIN_ENA, GPIO.HIGH)   # HIGH disables the DM542T
    print("[stepper] Motor de-energised")


# ── Internal move engine ──────────────────────────────────────────────────────

# A single background thread runs the step loop.
# _stop_event is set to request a stop (jog release, distance complete, estop).

_move_thread = None
_stop_event  = threading.Event()
_move_lock   = threading.Lock()     # Prevents two moves starting simultaneously


def _set_direction(direction: str):
    level = state.DIR_LEFT if direction == "left" else state.DIR_RIGHT
    if not SIMULATION:
        GPIO.output(state.PIN_DIR, level)
    state.set(direction=direction)


def _pulse_step():
    """Emit a single step pulse to the DM542T."""
    if not SIMULATION:
        GPIO.output(state.PIN_STEP, GPIO.HIGH)
        time.sleep(0.000002)        # 2µs pulse width — well within DM542T spec
        GPIO.output(state.PIN_STEP, GPIO.LOW)


def _step_sign(direction: str) -> int:
    return -1 if direction == "left" else 1


def _run_move(direction: str, total_steps: int | None, stop_event: threading.Event):
    """
    Core step loop — used for both jog and distance moves.

    direction   : "left" or "right"
    total_steps : number of steps to travel, or None for unlimited jog
    stop_event  : set this to stop the move early

    Uses a simple trapezoidal velocity profile:
      - Accelerate from min_speed to target_speed over accel_steps
      - Hold at target_speed
      - Decelerate back to min_speed over accel_steps
    """
    target_speed_mm_s = state.speed_mm_per_sec()
    accel_mm_s2       = state.ACCELERATION_MM_S2
    steps_per_mm      = state.STEPS_PER_MM

    target_steps_per_sec = target_speed_mm_s * steps_per_mm
    min_steps_per_sec    = 50.0     # Low enough to always start cleanly

    # Steps needed to ramp from min to target speed
    # v² = u² + 2as  →  s = (v²-u²) / 2a
    accel_mm  = (target_speed_mm_s**2 - (min_steps_per_sec / steps_per_mm)**2) / (2 * accel_mm_s2)
    accel_steps = int(accel_mm * steps_per_mm)

    if total_steps is not None and total_steps < accel_steps * 2:
        # Move too short to fully ramp — clamp accel phase
        accel_steps = total_steps // 2

    _set_direction(direction)
    sign = _step_sign(direction)

    current_speed = min_steps_per_sec
    steps_taken   = 0

    state.set(moving=True)

    while not stop_event.is_set():
        if total_steps is not None and steps_taken >= total_steps:
            break

        # Determine phase
        if total_steps is not None:
            remaining = total_steps - steps_taken
            if remaining <= accel_steps:
                # Deceleration phase
                target = max(
                    min_steps_per_sec,
                    min_steps_per_sec + (remaining / accel_steps) * (target_steps_per_sec - min_steps_per_sec)
                )
            elif steps_taken < accel_steps:
                # Acceleration phase
                target = min_steps_per_sec + (steps_taken / accel_steps) * (target_steps_per_sec - min_steps_per_sec)
            else:
                target = target_steps_per_sec
        else:
            # Jog — accelerate to target then hold; deceleration on stop handled separately
            if steps_taken < accel_steps:
                target = min_steps_per_sec + (steps_taken / accel_steps) * (target_steps_per_sec - min_steps_per_sec)
            else:
                target = target_steps_per_sec

        # Smoothly slew current_speed toward target
        current_speed = min(target, max(min_steps_per_sec, current_speed + (accel_mm_s2 * steps_per_mm) / current_speed))
        current_speed = min(current_speed, target_steps_per_sec)

        delay = 1.0 / current_speed

        _pulse_step()
        state.increment_position(sign)
        steps_taken += 1

        # Sleep for the remainder of the step period
        time.sleep(max(0, delay - 0.000002))

    # Deceleration tail for jog stop
    if stop_event.is_set() and total_steps is None:
        _decel_to_stop(direction, current_speed, min_steps_per_sec, accel_mm_s2, steps_per_mm, sign)

    state.set(moving=False, direction=None)
    print(f"[stepper] Move complete — steps taken: {steps_taken}")


def _decel_to_stop(direction, current_speed, min_speed, accel, steps_per_mm, sign):
    """Ramp down to min speed after jog_stop is received."""
    speed = current_speed
    while speed > min_speed:
        speed = max(min_speed, speed - (accel * steps_per_mm) / speed)
        delay = 1.0 / speed
        _pulse_step()
        state.increment_position(sign)
        time.sleep(max(0, delay - 0.000002))


# ── Public movement API ───────────────────────────────────────────────────────

def jog_start(direction: str):
    """Begin jogging in direction until jog_stop() is called."""
    with _move_lock:
        _abort_current_move()
        _stop_event.clear()
        t = threading.Thread(
            target=_run_move,
            args=(direction, None, _stop_event),
            daemon=True
        )
        global _move_thread
        _move_thread = t
        t.start()
    print(f"[stepper] Jog start — {direction}")


def jog_stop():
    """Signal the jog move to decelerate and stop."""
    _stop_event.set()
    print("[stepper] Jog stop requested")


def distance_move(direction: str):
    """Move the set distance in the given direction."""
    steps = state.mm_to_steps(state.get_distance_mm())
    if steps <= 0:
        return
    with _move_lock:
        _abort_current_move()
        _stop_event.clear()
        t = threading.Thread(
            target=_run_move,
            args=(direction, steps, _stop_event),
            daemon=True
        )
        global _move_thread
        _move_thread = t
        t.start()
    print(f"[stepper] Distance move — {direction}, {steps} steps ({state.get_distance_mm():.3f}mm)")


def estop():
    """Software e-stop — stop immediately and de-energise."""
    _stop_event.set()
    de_energise()
    print("[stepper] Software e-stop")


def is_moving() -> bool:
    return _move_thread is not None and _move_thread.is_alive()


def _abort_current_move():
    """Stop any current move and wait for it to finish before starting a new one."""
    global _move_thread
    if _move_thread and _move_thread.is_alive():
        _stop_event.set()
        _move_thread.join(timeout=2.0)
    _stop_event.clear()
