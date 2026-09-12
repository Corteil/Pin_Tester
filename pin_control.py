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
# skips __init__.py entirely and works standalone.
#
# The on-badge app.py UI is just a thin wrapper around these same
# functions, so both paths exercise identical logic.

from system.hexpansion.config import HexpansionConfig
import time

# (primary name, secondary/functional name or "", pin list to index into,
# index) -- covers all 9 pins a hexpansion port actually exposes: the 4
# high-speed ones (HexpansionConfig.pin, plain machine.Pin objects) and the
# 5 low-speed/EGPIO ones (HexpansionConfig.ls_pin, a different `ePin` type
# from the `tildagon` C module -- confirmed via the pre-installed
# "Breadboard Tester" app's own source that it shares the same
# .init(pin.OUT/IN) / .value() API as machine.Pin, just a different
# concrete type, so pin.IN/pin.OUT (read off the object itself) rather than
# a hardcoded machine.Pin.IN/OUT is used everywhere below to stay generic
# across both).
#
# Naming (unified 2026-09-12): the primary name for every pin is now its
# physical hexpansion label -- HS_F..HS_I and LS_A..LS_E, one contiguous
# A-I lettering across low- and high-speed pins -- matching how the 5 LS
# pins were already named. The HS pins additionally carry their functional
# role (MOSI/SCK/CS/DC) as a secondary name, both still resolvable by
# _resolve() below. This order (MOSI, SCK, CS, DC) was cross-checked
# against hazanjon's independent PORT_PINS table (badge-2024-software's
# components/flow3r_bsp/flow3r_bsp_display_mirror.c, also used by his
# display_manager app) across all 6 ports -- his driver has no MISO line
# at all, unlike an earlier, incorrect assumption this project made.
PIN_INFO = (
    ("HS_F", "MOSI", "hs", 0),
    ("HS_G", "SCK", "hs", 1),
    ("HS_H", "CS", "hs", 2),
    ("HS_I", "DC", "hs", 3),  # hazanjon's driver's DC line; also this project's own PIN_MISO on the RP2350 side
    ("LS_A", "", "ls", 0),
    ("LS_B", "", "ls", 1),
    ("LS_C", "", "ls", 2),
    ("LS_D", "", "ls", 3),
    ("LS_E", "", "ls", 4),
)


def _resolve(port, name):
    name = name.upper()
    cfg = HexpansionConfig(port)
    for primary, secondary, kind, idx in PIN_INFO:
        if name == primary or (secondary and name == secondary):
            return cfg.ls_pin[idx] if kind == "ls" else cfg.pin[idx]
    raise ValueError("unknown pin name {!r}, expected one of {}".format(
        name, [p for p, _s, _k, _i in PIN_INFO] + [s for _p, s, _k, _i in PIN_INFO if s]))


def read(port, name):
    """Read the current level of one pin (configures it as input first)."""
    pin = _resolve(port, name)
    pin.init(pin.IN)
    return pin.value()


def write(port, name, value):
    """Drive one pin to a fixed level (configures it as output first)."""
    pin = _resolve(port, name)
    pin.init(pin.OUT)
    pin.value(1 if value else 0)
    return pin.value()


def blink(port, name, duration_ms=3000, period_ms=250):
    """Auto-toggle one pin for a fixed duration, blocking. Call directly
    from a REPL/exec context for an unattended continuity test.
    period_ms is the full on+off cycle length (250 -> ~2Hz)."""
    pin = _resolve(port, name)
    pin.init(pin.OUT)
    pin.value(0)
    state = False
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    half = period_ms // 2
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        state = not state
        pin.value(1 if state else 0)
        time.sleep_ms(half)
    pin.init(pin.IN)


def blink_all(port, duration_ms=3000, period_ms=250):
    """Blink every pin on a port (all 4 HS + all 5 LS) simultaneously for a
    fixed duration -- one-call smoke test of full continuity, no need to
    invoke blink() nine times separately."""
    pins = [_resolve(port, name) for name, _s, _k, _i in PIN_INFO]
    for p in pins:
        p.init(p.OUT)
        p.value(0)
    state = False
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    half = period_ms // 2
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        state = not state
        for p in pins:
            p.value(1 if state else 0)
        time.sleep_ms(half)
    for p in pins:
        p.init(p.IN)


def read_all(port):
    """Read every pin on a port at once, as a dict of name -> level."""
    result = {}
    for name, _s, _k, _i in PIN_INFO:
        pin = _resolve(port, name)
        pin.init(pin.IN)
        result[name] = pin.value()
    return result


def latch(port, name, duration_ms=3000, poll_ms=2):
    """Watch one pin for a fixed duration and report whether it was ever
    seen HIGH and/or ever seen LOW, plus its final level -- catches a
    brief pulse you'd otherwise have to be watching at exactly the right
    moment to see. Returns a dict: {"ever_high", "ever_low", "final"}.
    USB/REPL equivalent of the app's own LATCH-mode indicator."""
    pin = _resolve(port, name)
    pin.init(pin.IN)
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
