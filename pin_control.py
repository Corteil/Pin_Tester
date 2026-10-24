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

from system.hexpansion.config import HexpansionConfig, _pin_mapping
from machine import mem32, I2C
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
    ("LS_A", "", "ls", 0),
    ("LS_B", "", "ls", 1),
    ("LS_C", "", "ls", 2),
    ("LS_D", "", "ls", 3),
    ("LS_E", "", "ls", 4),
    ("HS_F", "MOSI", "hs", 0),
    ("HS_G", "SCK", "hs", 1),
    ("HS_H", "CS", "hs", 2),
    ("HS_I", "DC", "hs", 3),  # hazanjon's driver's DC line; also this project's own PIN_MISO on the RP2350 side
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


def led(port, name, duration_ms=4000, period_ms=2000):
    """Fade one LS pin up and down in the AW9523B's LED (PWM) mode for a
    fixed duration, blocking, then release it back to input. LS pins only
    -- HS pins have no LED driver. USB/REPL equivalent of the app's
    LED: BREATHE mode."""
    primary = next((p for p, s, _k, _i in PIN_INFO if name.upper() in (p, s)), None)
    kind = next((k for p, _s, k, _i in PIN_INFO if p == primary), None)
    if kind != "ls":
        raise ValueError("LED mode is LS pins only, got {!r}".format(name))
    pin = _resolve(port, name)
    pin.init(pin.PWM)
    half = period_ms // 2
    end = time.ticks_add(time.ticks_ms(), duration_ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        t = time.ticks_ms() % period_ms
        d = t if t < half else period_ms - t
        pin.duty(d * 255 // half)
        time.sleep_ms(20)
    pin.duty(0)
    pin.init(pin.IN)


# --- Read-only inspection ---------------------------------------------------
# Report how pins are CURRENTLY configured without touching that
# configuration -- nothing below calls .init(), so it is safe to run against
# a port another app/driver is already using.
#
# HS pins are plain ESP32-S3 GPIOs, so their state is read straight out of
# the GPIO / IO_MUX registers with mem32 (addresses from the ESP32-S3
# technical reference manual, confirmed against known pin states on a real
# badge). LS pins sit on AW9523B I2C expanders; their config registers are
# read over the badge's system I2C bus (bus 7). The expanders' brightness
# (dim) registers read back as 0 even while an LED is lit, so LED
# brightness can't be inspected.
#
# The only side effect is for LS pins configured as inputs: the live level
# comes from the same driver read pin.value() does (the AW9523B input
# register), which is what the rest of this module already uses.

_GPIO_OUT = (0x60004004, 0x60004010)      # GPIO 0-31, 32-53
_GPIO_ENABLE = (0x60004020, 0x6000402C)
_GPIO_IN = (0x6000403C, 0x60004040)
_GPIO_FUNC_OUT_SEL = 0x60004554           # + 4*gpio; 256 = plain GPIO
_IO_MUX = 0x60009004                      # + 4*gpio
_SIG_GPIO = 256
_LEDC_SIGS = range(73, 81)                # LEDC_LS_SIG_OUT0..7 (machine.PWM)
_SYSTEM_I2C_BUS = 7
_i2c_sys = None


def _gpio_bit(regs, gpio):
    return (mem32[regs[1 if gpio >= 32 else 0]] >> (gpio & 31)) & 1


def _inspect_hs(port, idx):
    gpio = _pin_mapping[port]["hs"][idx]
    sel = mem32[_GPIO_FUNC_OUT_SEL + 4 * gpio] & 0x1FF
    mux = mem32[_IO_MUX + 4 * gpio]
    if mux >> 8 & 1:
        pull = "up"
    elif mux >> 7 & 1:
        pull = "down"
    else:
        pull = "none"
    is_out = bool(_gpio_bit(_GPIO_ENABLE, gpio))
    # The output routing register keeps pointing at a peripheral after it
    # is released (e.g. machine.PWM.deinit()), so it only means anything
    # while the output is actually enabled. Peripheral INPUT routing isn't
    # detected.
    if sel == _SIG_GPIO or not is_out:
        func = "gpio"
    elif sel in _LEDC_SIGS:
        func = "pwm"
    else:
        func = "periph"
    return {
        "kind": "hs", "gpio": gpio, "func": func, "sel": sel,
        "dir": "out" if is_out else "in",
        "level": _gpio_bit(_GPIO_IN, gpio),
        "latch": _gpio_bit(_GPIO_OUT, gpio),
        "pull": pull,
    }


def _inspect_ls(cfg, idx, chip_regs):
    pin = cfg.ls_pin[idx]
    addr, port, mask = pin.pin
    regs = chip_regs.get(addr)
    if regs is None:
        global _i2c_sys
        if _i2c_sys is None:
            _i2c_sys = I2C(_SYSTEM_I2C_BUS)
        regs = (_i2c_sys.readfrom_mem(addr, 0x02, 4), _i2c_sys.readfrom_mem(addr, 0x12, 2))
        chip_regs[addr] = regs
    latch_regs, mode_regs = regs
    is_in = bool(latch_regs[2 + port] & mask)
    latch = 1 if latch_regs[port] & mask else 0
    is_gpio = bool(mode_regs[port] & mask)
    return {
        "kind": "ls", "addr": addr,
        "func": "gpio" if is_gpio else "led",
        "dir": "in" if is_in else "out",
        # level of an output is just what it was told to drive; only
        # actual inputs need a live read.
        "level": (1 if pin.value() else 0) if (is_in and is_gpio) else latch,
        "latch": latch,
    }


def inspect_pins(port, names=None):
    """Read-only snapshot of how pins on a port are CURRENTLY set, without
    changing anything. names: iterable of pin names (defaults to all 9).
    Returns {name: info dict} -- see _inspect_hs/_inspect_ls for fields:
    func is "gpio" / "pwm" / "periph" (HS) or "gpio" / "led" (LS); dir is
    "in"/"out"; level is the live level; HS also has pull, LS has latch."""
    cfg = HexpansionConfig(port)
    chip_regs = {}
    wanted = None if names is None else set(n.upper() for n in names)
    result = {}
    for primary, secondary, kind, idx in PIN_INFO:
        if wanted is not None and primary not in wanted and secondary.upper() not in wanted:
            continue
        result[primary] = _inspect_hs(port, idx) if kind == "hs" else _inspect_ls(cfg, idx, chip_regs)
    return result
