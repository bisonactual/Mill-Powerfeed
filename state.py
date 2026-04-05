"""
state.py - Single source of truth for power feed state.
All values are read and written through this module.
"""

import threading

# ── Machine constants ────────────────────────────────────────────────────────

STEPS_PER_REV       = 200          # Native motor steps per revolution
MICROSTEPS          = 8            # DM542T microstepping setting
LEADSCREW_PITCH_MM  = 3.0          # mm travelled per full leadscrew revolution
MM_PER_INCH         = 25.4

STEPS_PER_MM = (STEPS_PER_REV * MICROSTEPS) / LEADSCREW_PITCH_MM   # 533.333...

MAX_SPEED_MM_MIN    = 2000.0       # Maximum feed rate (mm/min)
ACCELERATION_MM_S2  = 200.0        # Acceleration (mm/s²)

# GPIO pin assignments (BCM numbering)
PIN_STEP = 17
PIN_DIR  = 27
PIN_ENA  = 22

# DIR pin logic levels - adjust if motor runs backwards
DIR_LEFT  = 0
DIR_RIGHT = 1

# ── State ─────────────────────────────────────────────────────────────────────

_lock = threading.Lock()

_state = {
    # Position tracked in steps; converted to mm/inch for display
    "position_steps": 0,

    # Display units: "mm" or "inch"
    "units": "mm",

    # Speed as a percentage of MAX_SPEED_MM_MIN (0–100)
    "speed_pct": 50,

    # Operating mode: "jog" or "distance"
    "mode": "jog",

    # Whether the machine is currently moving
    "moving": False,

    # Current direction: "left", "right", or None
    "direction": None,

    # Distance to travel in distance mode (stored internally in mm)
    "distance_mm": 10.0,

    # Whether the physical panel has taken control (app becomes read-only)
    "panel_active": False,

    # Whether the motor is energised
    "energised": True,
}


# ── Accessors ─────────────────────────────────────────────────────────────────

def get():
    """Return a copy of the full state dict, with position converted for display."""
    with _lock:
        s = dict(_state)

    pos_mm = s["position_steps"] / STEPS_PER_MM
    if s["units"] == "inch":
        s["position"] = round(pos_mm / MM_PER_INCH, 4)
    else:
        s["position"] = round(pos_mm, 3)

    s["max_speed"]    = MAX_SPEED_MM_MIN
    s["acceleration"] = ACCELERATION_MM_S2

    return s


def set(**kwargs):
    """Update one or more state values."""
    with _lock:
        for key, value in kwargs.items():
            if key in _state:
                _state[key] = value


# ── Position helpers ──────────────────────────────────────────────────────────

def get_position_steps():
    with _lock:
        return _state["position_steps"]


def increment_position(steps: int):
    """Add steps to position (positive = right, negative = left)."""
    with _lock:
        _state["position_steps"] += steps


def zero_position():
    with _lock:
        _state["position_steps"] = 0


def set_position(value: float, units: str = "mm"):
    """Set position from a user-supplied value in mm or inch."""
    mm = value if units == "mm" else value * MM_PER_INCH
    with _lock:
        _state["position_steps"] = int(round(mm * STEPS_PER_MM))


# ── Speed helper ──────────────────────────────────────────────────────────────

def speed_mm_per_sec() -> float:
    """Return current speed in mm/s based on speed_pct."""
    with _lock:
        pct = _state["speed_pct"]
    return (pct / 100.0) * (MAX_SPEED_MM_MIN / 60.0)


# ── Distance helper ───────────────────────────────────────────────────────────

def set_distance(value: float, units: str = "mm"):
    """Store distance in mm regardless of input units."""
    mm = value if units == "mm" else value * MM_PER_INCH
    with _lock:
        _state["distance_mm"] = mm


def get_distance_mm() -> float:
    with _lock:
        return _state["distance_mm"]


def mm_to_steps(mm: float) -> int:
    """Convert mm to nearest whole number of steps."""
    return int(round(mm * STEPS_PER_MM))
