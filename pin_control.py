# Shared, USB/REPL-callable pin control logic for the Hexi-GFX
# wiring-continuity tests. Import this directly from a raw `mpremote exec`
# call to drive/read hexpansion port pins WITHOUT needing the Pin Tester
# app's own UI, a running scheduler, or any physical button presses, e.g.:
#
#   mpremote connect /dev/ttyACM0 exec \
#       "import sys; sys.path.insert(0, '/apps/pin_tester'); \
#        import pin_control; pin_control.blink_all(1, 4000)"
#
# Import it via sys.path like that, NOT as `apps.pin_tester.pin_control` --
# that dotted form runs apps/pin_tester/__init__.py first (package import
# semantics), which pulls in app.py's own `import app` -> eventbus ->
# scheduler chain and fails with "can't import name eventbus" unless
# main.py's scheduler is already running. Importing straight off sys.path
# skips __init__.py entirely and works standalone, scheduler or no.
#
# The on-badge app.py UI is just a thin wrapper around these same
# functions, so both paths exercise identical logic.

from machine import Pin
from system.hexpansion.config import HexpansionConfig
import time

# hs[] index -> (common name, physical hexpansion label) -- confirmed
# consistent across all 6 ports against both this badge's own
# _pin_mapping (system/hexpansion/config.py) and hazanjon's independent
# PORT_PINS table (components/flow3r_bsp/flow3r_bsp_display_mirror.c,
# also used by his display_manager app). Note this project originally
# assumed the WRONG order (MOSI, CS, SCK, MISO), copied from an older,
# unrelated sender -- the real, consistent order is (MOSI, SCK, CS, DC);
# hazanjon's driver has no MISO line at all.
PIN_INFO = (
    ("MOSI", "HS_F"),
    ("SCK", "HS_G"),
    ("CS", "HS_H"),
    ("DC", "HS_I"),  # hazanjon's driver's DC line; also this project's own PIN_MISO on the RP2350 side
)


def _resolve(port, name):
    name = name.upper()
    for i, (common, label) in enumerate(PIN_INFO):
        if name == common or name == label:
            cfg = HexpansionConfig(port)
            return cfg.pin[i]
    raise ValueError("unknown pin name {!r}, expected one of {}".format(
        name, [c for c, _ in PIN_INFO] + [l for _, l in PIN_INFO]))


def read(port, name):
    """Read the current level of one pin (configures it as input first)."""
    pin = _resolve(port, name)
    pin.init(Pin.IN)
    return pin.value()


def write(port, name, value):
    """Drive one pin to a fixed level (configures it as output first)."""
    pin = _resolve(port, name)
    pin.init(Pin.OUT, value=1 if value else 0)
    return pin.value()


def blink(port, name, duration_ms=3000, period_ms=250):
    """Auto-toggle one pin for a fixed duration, blocking. Call directly
    from a REPL/exec context for an unattended continuity test.
    period_ms is the full on+off cycle length (250 -> ~2Hz)."""
    pin = _resolve(port, name)
    pin.init(Pin.OUT, value=0)
    state = False
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    half = period_ms // 2
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        state = not state
        pin.value(1 if state else 0)
        time.sleep_ms(half)
    pin.init(Pin.IN)


def blink_all(port, duration_ms=3000, period_ms=250):
    """Blink all four HS pins on a port simultaneously for a fixed
    duration -- one-call smoke test of full continuity, no need to invoke
    blink() four times separately."""
    pins = [_resolve(port, name) for name, _ in PIN_INFO]
    for p in pins:
        p.init(Pin.OUT, value=0)
    state = False
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    half = period_ms // 2
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        state = not state
        for p in pins:
            p.value(1 if state else 0)
        time.sleep_ms(half)
    for p in pins:
        p.init(Pin.IN)


def read_all(port):
    """Read all four HS pins on a port at once, as a dict of name -> level."""
    cfg = HexpansionConfig(port)
    result = {}
    for i, (name, _label) in enumerate(PIN_INFO):
        cfg.pin[i].init(Pin.IN)
        result[name] = cfg.pin[i].value()
    return result


def latch(port, name, duration_ms=3000, poll_ms=2):
    """Watch one pin for a fixed duration and report whether it was ever
    seen HIGH and/or ever seen LOW, plus its final level -- catches a
    brief pulse you'd otherwise have to be watching at exactly the right
    moment to see. Returns a dict: {"ever_high", "ever_low", "final"}.
    USB/REPL equivalent of the app's own INPUT-mode latch indicator."""
    pin = _resolve(port, name)
    pin.init(Pin.IN)
    ever_high = False
    ever_low = False
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        v = pin.value()
        if v:
            ever_high = True
        else:
            ever_low = True
        time.sleep_ms(poll_ms)
    return {"ever_high": ever_high, "ever_low": ever_low, "final": pin.value()}
