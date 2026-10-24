# Hexpansion Pin Tester -- a small diagnostic app for checking wiring
# continuity on a badge hexpansion port, independent of any SPI/I2C driver
# or protocol. Built for the Hexi-GFX HDMI mirror project after a
# protocol-agnostic RP2350-side GPIO snoop needed to confirm continuity on
# each of a port's pins. Covers all 9 pins a hexpansion port exposes: the
# 4 high-speed ones (HS_F..HS_I, aka MOSI/SCK/CS/DC) and the 5 low-speed/
# EGPIO ones (LS_A..LS_E) -- see pin_control.py for the pin resolution
# logic and naming scheme.
#
# This is a thin UI wrapper over pin_control.py -- see that module for the
# actual pin logic, and for how to drive/read pins directly over
# USB/REPL (no button presses, no need for this app to even be running)
# for unattended/automatable continuity testing.
#
# LIVE remote control (while this app is actually running and on screen):
# enable "Remote: ON" from the STATUS tile (CONFIRM), then send plain text
# commands to the SAME USB serial port WITHOUT going through mpremote's
# exec/cp (which always sends Ctrl-C first and would kill this app) --
# e.g. from a shell: `stty -F /dev/ttyACM0 raw -echo && printf 'GET HS_F\n'
# > /dev/ttyACM0`, with a separate `cat /dev/ttyACM0` (or similar) reading
# the response back. Commands, one per line:
#   STATUS                          -- every pin's name=level
#   GET <pin>                       -- one pin's current level
#   SET <pin> IN|LATCH|OFF|ON|AUTO      -- change a pin's mode
#   SET <pin> PWM [hz [duty]]           -- HS pins only: hardware PWM
#   SET <pin> LEDOFF|LEDON|BREATHE [n]  -- LED modes, LS_A..LS_E only; n is
#                                          brightness 0-100 (LEDON) or
#                                          cycle time in 0.1s units, 2-100
#                                          (BREATHE)
# This only has any effect while "Remote: ON" is showing on the STATUS
# tile -- otherwise incoming commands are ignored entirely, so pins can
# never change remotely by surprise.
#
# Every pin's mode is tracked and kept running INDEPENDENTLY per pin --
# e.g. set one pin auto-blinking, then navigate away to check a different
# pin's input reading, and the first pin keeps blinking in the background
# the whole time. STATUS shows every pin's live value at once, colour-
# coded by whether that pin is currently an input (cyan) or an output
# (orange), so you can watch several pins' real states simultaneously.
#
# Tiles, reached via LEFT/RIGHT (wrapping): PORT, STATUS, then each pin.
#
# Controls (work on any badge/frontboard):
#   LEFT/RIGHT  -- move between tiles: PORT, STATUS, pin 1, pin 2, ...
#   On the PORT tile:
#     UP/DOWN   -- change the hexpansion port (1-6) -- resets every pin
#                  back to a safe INPUT default, since it's a different
#                  physical connector.
#   On the STATUS tile:
#     CONFIRM   -- toggle "remote control active" (a pure informational
#                  flag shown on STATUS, for noting you intend to drive/
#                  read pins externally -- e.g. via another mpremote/
#                  pin_control call -- has no other effect on its own).
#   On a pin tile:
#     CONFIRM   -- switch the pin between its INPUT family (FOLLOW/
#                  LATCHED), its OUTPUT family (OFF/ON/AUTO) and, on LS
#                  pins, its LED family.
#     UP/DOWN   -- within INPUT family: toggle FOLLOW <-> LATCHED.
#                  within OUTPUT family: cycle OFF -> ON -> AUTO -> OFF.
#                  within LED family: change brightness (LED OFF/ON) or
#                  breathe cycle time (BREATHE).
#     LS pins also have a third family, LED (the AW9523B's constant-current
#     LED/PWM mode, for testing an LED wired to the pin): CONFIRM cycles
#     INPUT -> OUTPUT -> LED -> INPUT, and F (short press) cycles
#     LED OFF -> LED ON -> BREATHE. In the LED modes UP/DOWN auto-repeats
#     while held (after a short delay, faster the longer it's held).
#   HS pins have a third family too, PWM (real ESP32 hardware PWM, 10Hz to
#   100kHz): CONFIRM cycles INPUT -> OUTPUT -> PWM -> INPUT, F (short
#   press) switches between editing frequency and duty, and UP/DOWN
#   changes the field being edited (auto-repeating while held). Each pin
#   keeps its own PWM running independently, like every other mode.
#   CANCEL (F)  -- short press: on the STATUS tile, reset (press twice to
#                  confirm -- the first press only arms it) every pin on the
#                  port to its safe INPUT default (also clears LED
#                  brightness/cycle-time settings); on a pin tile, reset the
#                  current pin's LATCHED indicator
#                  (or, in an LED mode, cycle LED OFF/ON/BREATHE).
#                  Long press (>= LONG_PRESS_MS): exit the app.
#                  (F is CANCEL's physical button on this badge's own hex
#                  layout -- see frontboards/twentysix.py's own BUTTONS
#                  table -- reused here for latch-reset since a plain
#                  short press was otherwise unused in LATCHED mode.)
# Keyboard hexpansion bonus (harmless no-op without one attached):
#   R           -- also resets the current pin's latch (mnemonic: Reset).
# A keyboard's arrow keys/ENTER already alias to the controls above
# automatically (see events/keyboard.py's own parent-button declarations),
# so no extra code is needed for those.
# Joystick bonus (2026 frontboard only -- harmless no-ops elsewhere):
#   X           -- also resets the current pin's latch
#   A           -- jump straight to the STATUS tile

import app
import os
import sys
import time
try:
    import uselect as select
except ImportError:
    import select
try:
    from machine import PWM
except ImportError:
    PWM = None
from events.input import Buttons, BUTTON_TYPES
from app_components.tokens import clear_background, small_font_size, label_font_size
from system.hexpansion.config import HexpansionConfig
try:
    from system.hexpansion.util import read_hexpansion_header, detect_eeprom_addr
except ImportError:
    read_hexpansion_header = None
    detect_eeprom_addr = None
try:
    from events.joystick import JOYSTICK_BUTTON_TYPES
except ImportError:
    JOYSTICK_BUTTON_TYPES = {}
try:
    from events.keyboard import KEYBOARD_BUTTONS
except ImportError:
    KEYBOARD_BUTTONS = {}
try:
    # Relative import: correct regardless of what folder this app is
    # installed under (sideloading uses "pin_tester", but the app store
    # installs to "<owner>_<repo>", e.g. "Corteil_Pin_Tester" -- a
    # hardcoded absolute path here broke store installs).
    from .pin_control import PIN_INFO, _resolve
except ImportError:
    # Falls back to a bare import for standalone script execution (no
    # package context), e.g. testing app.py directly rather than through
    # the launcher.
    from pin_control import PIN_INFO, _resolve

NUM_PINS = len(PIN_INFO)
NAV_STATUS = -3
NAV_PORT = -2
NAV_I2C = -1
# pin tiles: 0 .. NUM_PINS-1

PIN_MODE_INPUT = 0    # INPUT
PIN_MODE_LATCH = 1    # LATCHED
PIN_MODE_OUTPUT_OFF = 2
PIN_MODE_OUTPUT_ON = 3
PIN_MODE_OUTPUT_AUTO = 4
PIN_MODE_LED_OFF = 5  # LS pins only: AW9523B LED (PWM) mode
PIN_MODE_LED_ON = 6
PIN_MODE_LED_BREATHE = 7
PIN_MODE_PWM = 8      # HS pins only: ESP32 hardware PWM
PIN_MODE_LABELS = ("INPUT", "LATCHED", "OUTPUT: OFF", "OUTPUT: ON", "OUTPUT: AUTO",
                   "LED: OFF", "LED: ON", "LED: BREATHE", "PWM")
INPUT_FAMILY = (PIN_MODE_INPUT, PIN_MODE_LATCH)
OUTPUT_FAMILY = (PIN_MODE_OUTPUT_OFF, PIN_MODE_OUTPUT_ON, PIN_MODE_OUTPUT_AUTO)
LED_FAMILY = (PIN_MODE_LED_OFF, PIN_MODE_LED_ON, PIN_MODE_LED_BREATHE)

COLOR_INPUT = (0, 1, 1)      # cyan
COLOR_OUTPUT = (1, 0.55, 0)  # orange
COLOR_LED = (1, 1, 0.3)      # yellow

BLINK_PERIOD_MS = 250  # ~2Hz full cycle
LONG_PRESS_MS = 600
# PWM mode: HS pins only (real ESP32 LEDC hardware PWM). The firmware
# can't go below 10Hz (ESP_ERR_INVALID_ARG / EPERM from 8Hz down), and
# duty_u16()'s getter always reads back 0, so the app tracks the values itself.
PWM_FREQS = (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)
PWM_DEFAULT_FREQ_IDX = 6   # 1kHz
PWM_DEFAULT_DUTY = 50      # percent
PWM_FIELD_FREQ = 0
PWM_FIELD_DUTY = 1
# Hold-to-repeat for UP/DOWN in the LED modes.
RESET_CONFIRM_MS = 3000  # window for the second F press that confirms a pin reset
HINT_FONT_SCALE = 0.8  # bottom hint lines are drawn smaller than normal text
HOLD_DELAY_MS = 400    # held this long before repeating starts
HOLD_REPEAT_MS = 100   # step interval while repeating
HOLD_ACCEL_MS = 1500   # held this long, repeat speeds up
HOLD_REPEAT_FAST_MS = 40
LED_DEFAULT_LEVEL = 50   # brightness setting, percent (0-100), 1% per UP/DOWN
# Breathe cycle time (one full fade up + down), 0.1s per UP/DOWN.
LED_PERIOD_MIN_MS = 200
LED_PERIOD_MAX_MS = 10000
LED_PERIOD_STEP_MS = 100
LED_DEFAULT_PERIOD_MS = 2000
LED_WRITE_QUANT = 8    # quantise breathe duty so we don't flood the I2C expander


class PinTesterApp(app.App):
    def __init__(self):
        super().__init__()
        self.buttons = Buttons(self)
        self.port = 1
        self.nav = NAV_STATUS
        self.remote_active = False
        self.status = ""
        self.cancel_press_start = None
        self.cancel_exit_triggered = False
        self.reset_msg_until = None
        self.reset_armed_until = None
        self.hold_dir = 0
        self.hold_key = None
        self.hold_start = 0
        self.hold_last = 0
        self.blink_state = False
        self.last_blink_ms = time.ticks_ms()
        self.i2c_scan_result = None
        self.i2c_scan_error = None
        self.last_i2c_scan_ms = 0
        self.i2c_show_all = False
        self.hexpansion_name = None
        self.hexpansion_vid = None
        self.hexpansion_pid = None
        self.last_hexpansion_present = False
        self.last_presence_check_ms = 0
        # Non-blocking remote command channel -- lets an external tool
        # write plain text lines to this same USB serial port (raw, NOT
        # via mpremote's exec/cp, which always sends Ctrl-C first and would
        # interrupt this app) and have them acted on right here in
        # update(), with zero interruption. See the module docstring above
        # for the command protocol. Degrades silently (remote_poll stays
        # None) if this MicroPython build doesn't support polling stdin.
        self.remote_poll = None
        try:
            p = select.poll()
            p.register(sys.stdin, select.POLLIN)
            self.remote_poll = p
        except Exception:
            self.remote_poll = None
        self._reset_all_pins()
        self._scan_i2c()

    def _reset_all_pins(self):
        # Release anything currently touched back to INPUT before dropping
        # the references -- used both at startup and on port change (a
        # different physical connector's 9 pins, so nothing should keep
        # driving from the old port).
        self._pwm_stop_all()
        for p in getattr(self, "pin_objs", []):
            if p is not None:
                try:
                    p.init(p.IN)
                except Exception:
                    pass
        self.pin_objs = [None] * NUM_PINS
        self.pin_modes = [PIN_MODE_INPUT] * NUM_PINS
        self.latch_high = [False] * NUM_PINS
        self.latch_low = [False] * NUM_PINS
        self.live_levels = [None] * NUM_PINS
        self.pin_auto_detected = [False] * NUM_PINS
        self.led_level = [LED_DEFAULT_LEVEL] * NUM_PINS
        self.led_period = [LED_DEFAULT_PERIOD_MS] * NUM_PINS
        self.pwm_objs = [None] * NUM_PINS
        self.pwm_freq_idx = [PWM_DEFAULT_FREQ_IDX] * NUM_PINS
        self.pwm_duty = [PWM_DEFAULT_DUTY] * NUM_PINS
        self.pwm_field = [PWM_FIELD_FREQ] * NUM_PINS

    def _scan_i2c(self):
        # The hexpansion I2C bus isn't a per-port dedicated GPIO pair the
        # way the HS/LS pins are -- it's a single shared physical bus
        # (see tildagon/mpconfigboard.h's one fixed MICROPY_HW_I2C0_SDA/SCL)
        # routed out to each port, most likely via an I2C GPIO expander
        # (drivers/tildagon_pin/aw9523b.c) rather than distinct wires per
        # port. So a raw GPIO toggle test (like the HS/LS pins get) isn't
        # meaningful here -- an actual bus scan for a real device (the
        # hexpansion's identification EEPROM, if present) is the
        # equivalent check.
        self.last_i2c_scan_ms = time.ticks_ms()
        self.hexpansion_name = None
        self.hexpansion_vid = None
        self.hexpansion_pid = None
        try:
            cfg = HexpansionConfig(self.port)
            self.i2c_scan_result = cfg.i2c.scan()
            self.i2c_scan_error = None
        except Exception as e:
            self.i2c_scan_result = None
            self.i2c_scan_error = "{!r}".format(e)
            return

        # If there's a real hexpansion identification EEPROM present, grab
        # its friendly name/VID/PID too (system.hexpansion.util's own
        # reader, the same one the badge's real hexpansion detection uses
        # -- just called directly here rather than through the running
        # HexpansionManagerApp instance). detect_eeprom_addr() first, since
        # some (larger) EEPROMs live at 0x57 instead of the default 0x50 --
        # assuming 0x50 always silently missed those (found testing against
        # a real keyboard hexpansion).
        if read_hexpansion_header is not None and detect_eeprom_addr is not None:
            try:
                addr, addr_len = detect_eeprom_addr(cfg.i2c)
                if addr is not None:
                    header = read_hexpansion_header(cfg.i2c, eeprom_addr=addr, addr_len=addr_len)
                    if header is not None:
                        self.hexpansion_name = header.friendly_name
                        self.hexpansion_vid = header.vid
                        self.hexpansion_pid = header.pid
            except Exception:
                pass

    def _check_eeprom(self):
        # Peeks at whatever the badge's own HexpansionManagerApp has
        # already mounted for this port (system/hexpansion/app.py's
        # _mount_eeprom uses this exact "/hexpansion_{port}" convention) --
        # doesn't try to mount anything itself, just reports what the
        # normal system hexpansion-detection already found, if anything.
        mountpoint = "/hexpansion_{}".format(self.port)
        try:
            files = os.listdir(mountpoint)
            has_app = "app.py" in files or "app.mpy" in files
            return {"mounted": True, "count": len(files), "has_app": has_app}
        except OSError:
            return {"mounted": False, "count": 0, "has_app": False}

    def _hexpansion_present(self):
        # A real device answering on the I2C bus, or an already-mounted
        # EEPROM filesystem, both indicate something is actually plugged
        # into this port right now -- used to warn before driving OUTPUT
        # onto pins a live device might also be driving.
        if self.i2c_scan_result:
            return True
        return self._check_eeprom()["mounted"]

    def _passive_probe_pin(self, pin, duration_ms=60, poll_ms=2):
        # Purely passive: just watches the pin's own natural value over a
        # short window, no pull resistor bias at all -- never risks even
        # the mild "spurious edge on a sensitive far-end input" concern an
        # active pull-up/down probe would. Flags "active" only if the pin
        # is actually seen to toggle during the window (the same ever-
        # high/ever-low idea LATCH mode already uses) -- a pin that just
        # sits constant is left ambiguous, not assumed to be either type.
        pin.init(pin.IN)
        seen_high = False
        seen_low = False
        end = time.ticks_add(time.ticks_ms(), duration_ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            if pin.value():
                seen_high = True
            else:
                seen_low = True
            time.sleep_ms(poll_ms)
        return seen_high and seen_low

    def _auto_detect_pin_types(self):
        # Runs once when a hexpansion is freshly detected on this port
        # (see the presence-transition check in update()). Every pin
        # always lands on INPUT regardless of the result -- passive
        # monitoring only ever confirms activity, it's never grounds to
        # pick OUTPUT for you. pin_auto_detected just drives the "auto"
        # label shown on STATUS/pin tiles.
        for i, (name, _secondary, _kind, _idx) in enumerate(PIN_INFO):
            try:
                pin = _resolve(self.port, name)
                active = self._passive_probe_pin(pin)
            except Exception:
                active = False
            self.pin_auto_detected[i] = active
            self._apply_mode(i, PIN_MODE_INPUT)
        self.status = "ready"

    def _apply_mode(self, i, mode):
        self._pwm_stop(i)  # release any running PWM before reconfiguring
        self.pin_modes[i] = mode
        self.latch_high[i] = False
        self.latch_low[i] = False
        try:
            pin = _resolve(self.port, PIN_INFO[i][0])
            if mode == PIN_MODE_OUTPUT_OFF:
                pin.init(pin.OUT)
                pin.value(0)
            elif mode == PIN_MODE_OUTPUT_ON:
                pin.init(pin.OUT)
                pin.value(1)
            elif mode == PIN_MODE_OUTPUT_AUTO:
                pin.init(pin.OUT)
                pin.value(1 if self.blink_state else 0)
            elif mode == PIN_MODE_PWM:
                if PIN_INFO[i][2] != "hs" or PWM is None:
                    raise ValueError("PWM mode is HS pins only")
                self.pwm_objs[i] = PWM(
                    pin, freq=PWM_FREQS[self.pwm_freq_idx[i]],
                    duty_u16=self.pwm_duty[i] * 65535 // 100)
                self.live_levels[i] = self.pwm_duty[i]
            elif mode in LED_FAMILY:
                if PIN_INFO[i][2] != "ls":
                    raise ValueError("LED mode is LS pins only")
                pin.init(pin.PWM)
                pin.duty(0)
                self.live_levels[i] = 0
            else:  # INPUT (FOLLOW) or LATCH (LATCHED)
                pin.init(pin.IN)
            self.pin_objs[i] = pin
            self.status = "ready"
        except Exception as e:
            self.status = "pin error: {!r}".format(e)
            self.pin_objs[i] = None

    def _led_breathe_duty(self, i):
        # Triangle wave 0..led_level..0 over the pin's cycle time.
        period = self.led_period[i]
        half = period // 2
        t = time.ticks_ms() % period
        d = t if t < half else period - t
        peak = self._duty_from_pct(self.led_level[i])
        return (d * peak // half) // LED_WRITE_QUANT * LED_WRITE_QUANT

    @staticmethod
    def _duty_from_pct(pct):
        return pct * 255 // 100

    @staticmethod
    def _pct_from_duty(duty):
        return (duty * 100 + 127) // 255

    @staticmethod
    def _fmt_secs(ms):
        return "{}.{}s".format(ms // 1000, ms % 1000 // 100)

    def _led_target(self, i):
        mode = self.pin_modes[i]
        if mode == PIN_MODE_LED_OFF:
            return 0
        if mode == PIN_MODE_LED_ON:
            return self._duty_from_pct(self.led_level[i])
        return self._led_breathe_duty(i)

    def _led_adjust(self, i, direction):
        # UP/DOWN in an LED mode: cycle time while breathing, brightness otherwise.
        if self.pin_modes[i] == PIN_MODE_LED_BREATHE:
            self.led_period[i] = max(LED_PERIOD_MIN_MS, min(
                LED_PERIOD_MAX_MS, self.led_period[i] + direction * LED_PERIOD_STEP_MS))
        else:
            self.led_level[i] = max(0, min(100, self.led_level[i] + direction))

    def _hold_repeat(self, key, up_pressed, down_pressed, adjust):
        # Buttons.pressed() only fires once per press (the first step is
        # taken by the normal UP/DOWN handler), so repeating while held
        # polls Buttons.get() for the held state instead. key identifies
        # what's being adjusted (pin index) so switching tiles
        # mid-hold cancels the repeat.
        now = time.ticks_ms()
        if up_pressed or down_pressed:
            self.hold_dir = 1 if up_pressed else -1
            self.hold_key = key
            self.hold_start = now
            self.hold_last = now
            return
        if self.hold_dir == 0:
            return
        button = "UP" if self.hold_dir > 0 else "DOWN"
        if self.hold_key != key or not self.buttons.get(BUTTON_TYPES[button]):
            self.hold_dir = 0
            return
        held = time.ticks_diff(now, self.hold_start)
        interval = HOLD_REPEAT_FAST_MS if held >= HOLD_ACCEL_MS else HOLD_REPEAT_MS
        if held >= HOLD_DELAY_MS and time.ticks_diff(now, self.hold_last) >= interval:
            self.hold_last = now
            adjust(self.hold_dir)

    # --- PWM mode (HS pins) ---------------------------------------------

    @staticmethod
    def _fmt_freq(hz):
        if hz >= 1000:
            return "{} kHz".format(hz // 1000)
        return "{} Hz".format(hz)

    def _pwm_stop(self, i):
        # Deinit pin i's PWM, if running. Callers (_apply_mode) re-init the
        # pin for whatever mode comes next.
        if self.pwm_objs[i] is not None:
            try:
                self.pwm_objs[i].deinit()
            except Exception:
                pass
            self.pwm_objs[i] = None

    def _pwm_stop_all(self):
        for p in getattr(self, "pwm_objs", []):
            if p is not None:
                try:
                    p.deinit()
                except Exception:
                    pass

    def _pwm_adjust(self, i, direction):
        if self.pwm_field[i] == PWM_FIELD_FREQ:
            self.pwm_freq_idx[i] = max(0, min(len(PWM_FREQS) - 1, self.pwm_freq_idx[i] + direction))
        else:
            self.pwm_duty[i] = max(0, min(100, self.pwm_duty[i] + direction))
        try:
            self.pwm_objs[i].freq(PWM_FREQS[self.pwm_freq_idx[i]])
            self.pwm_objs[i].duty_u16(self.pwm_duty[i] * 65535 // 100)
            self.live_levels[i] = self.pwm_duty[i]
        except Exception as e:
            self.status = "pwm error: {!r}".format(e)
            self._apply_mode(i, PIN_MODE_INPUT)

    def _adjust_mode_value(self, i, direction):
        # UP/DOWN auto-repeat target for the LED and PWM modes.
        if self.pin_modes[i] == PIN_MODE_PWM:
            self._pwm_adjust(i, direction)
        else:
            self._led_adjust(i, direction)

    def _cycle_led_mode(self, i):
        idx = LED_FAMILY.index(self.pin_modes[i])
        self._apply_mode(i, LED_FAMILY[(idx + 1) % len(LED_FAMILY)])

    def _ensure_pin(self, i):
        if self.pin_objs[i] is None:
            self._apply_mode(i, self.pin_modes[i])
        return self.pin_objs[i]

    def _pin_index_by_name(self, name):
        name = name.upper()
        for i, (primary, secondary, _kind, _idx) in enumerate(PIN_INFO):
            if name == primary or (secondary and name == secondary):
                return i
        return None

    def _poll_remote_commands(self):
        if self.remote_poll is None:
            return
        try:
            if not self.remote_poll.poll(0):
                return
            line = sys.stdin.readline()
        except Exception:
            return
        if not line:
            return
        line = line.strip()
        if line:
            self._handle_remote_command(line)

    def _handle_remote_command(self, line):
        # Plain-text remote protocol, one command per line, no Ctrl-C
        # involved at all (see remote_poll's own setup comment):
        #   STATUS                 -- print every pin's name=level
        #   GET <pin>               -- print one pin's current level
        #   SET <pin> IN|LATCH|OFF|ON|AUTO|LEDOFF|LEDON|BREATHE [n]
        #                           -- change a pin's mode (see module docstring)
        MODE_WORDS = {
            "IN": PIN_MODE_INPUT,
            "LATCH": PIN_MODE_LATCH,
            "OFF": PIN_MODE_OUTPUT_OFF,
            "ON": PIN_MODE_OUTPUT_ON,
            "AUTO": PIN_MODE_OUTPUT_AUTO,
            "LEDOFF": PIN_MODE_LED_OFF,
            "LEDON": PIN_MODE_LED_ON,
            "BREATHE": PIN_MODE_LED_BREATHE,
            "PWM": PIN_MODE_PWM,
        }
        parts = line.split()
        if not parts:
            return
        cmd = parts[0].upper()
        try:
            if cmd == "STATUS":
                fields = ["{}={}".format(name, self.live_levels[i])
                          for i, (name, _s, _k, _idx) in enumerate(PIN_INFO)]
                print("STATUS " + " ".join(fields))
            elif cmd == "GET" and len(parts) >= 2:
                i = self._pin_index_by_name(parts[1])
                if i is None:
                    print("ERR unknown pin {}".format(parts[1]))
                else:
                    print("GET {} {}".format(parts[1], self.live_levels[i]))
            elif cmd == "SET" and len(parts) >= 3:
                i = self._pin_index_by_name(parts[1])
                mode_word = parts[2].upper()
                if i is None:
                    print("ERR unknown pin {}".format(parts[1]))
                elif mode_word not in MODE_WORDS:
                    print("ERR unknown mode {}".format(mode_word))
                elif MODE_WORDS[mode_word] in LED_FAMILY and PIN_INFO[i][2] != "ls":
                    print("ERR LED mode is LS pins only")
                elif mode_word == "PWM" and PIN_INFO[i][2] != "hs":
                    print("ERR PWM mode is HS pins only")
                else:
                    if len(parts) >= 4:
                        n = int(parts[3])
                        if mode_word == "LEDON":
                            self.led_level[i] = max(0, min(100, n))
                        elif mode_word == "PWM":
                            # n = frequency in Hz (snapped to the nearest
                            # step), optional 5th field = duty percent
                            self.pwm_freq_idx[i] = min(
                                range(len(PWM_FREQS)), key=lambda k: abs(PWM_FREQS[k] - n))
                            if len(parts) >= 5:
                                self.pwm_duty[i] = max(0, min(100, int(parts[4])))
                        elif mode_word == "BREATHE":
                            self.led_period[i] = max(LED_PERIOD_MIN_MS, min(
                                LED_PERIOD_MAX_MS, n * 100))
                    self._apply_mode(i, MODE_WORDS[mode_word])
                    print("OK {} {}".format(parts[1], mode_word))
            else:
                print("ERR bad command: {}".format(line))
        except Exception as e:
            print("ERR {!r}".format(e))

    def update(self, delta):
        if self.remote_active:
            self._poll_remote_commands()

        # Hotplug: the moment a hexpansion is freshly detected on the
        # current port (the system's own HexpansionManagerApp mounting its
        # EEPROM -- checked directly here rather than via the possibly-
        # stale i2c_scan_result, so this works regardless of which tile
        # you're on), passively probe every pin once and land them all on
        # INPUT. This briefly pauses the UI for ~9*60ms -- acceptable for
        # a rare, one-shot event.
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_presence_check_ms) >= 500:
            self.last_presence_check_ms = now
            now_present = self._check_eeprom()["mounted"]
            if now_present and not self.last_hexpansion_present:
                self._auto_detect_pin_types()
            self.last_hexpansion_present = now_present

        # CANCEL (physical F): short press resets the current pin's latch,
        # long press exits. Tracked manually since Buttons.pressed() is
        # edge-triggered and doesn't report hold duration on its own.
        cancel_down = self.buttons.get(BUTTON_TYPES["CANCEL"])
        if cancel_down:
            if self.cancel_press_start is None:
                self.cancel_press_start = time.ticks_ms()
                self.cancel_exit_triggered = False
            elif not self.cancel_exit_triggered and time.ticks_diff(
                time.ticks_ms(), self.cancel_press_start
            ) >= LONG_PRESS_MS:
                self.cancel_exit_triggered = True
                self.buttons.clear()
                self.minimise()
                return True
        else:
            if self.cancel_press_start is not None and not self.cancel_exit_triggered:
                if self.nav == NAV_STATUS:
                    # Safety catch: the first press only arms the reset,
                    # a second press within RESET_CONFIRM_MS carries it out.
                    now = time.ticks_ms()
                    if (self.reset_armed_until is not None
                            and time.ticks_diff(self.reset_armed_until, now) > 0):
                        self.reset_armed_until = None
                        self._reset_all_pins()
                        self.status = "ready"
                        self.reset_msg_until = time.ticks_add(now, 2000)
                    else:
                        self.reset_armed_until = time.ticks_add(now, RESET_CONFIRM_MS)
                elif 0 <= self.nav < NUM_PINS and self.pin_modes[self.nav] == PIN_MODE_PWM:
                    self.pwm_field[self.nav] = 1 - self.pwm_field[self.nav]
                elif 0 <= self.nav < NUM_PINS and self.pin_modes[self.nav] in LED_FAMILY:
                    self._cycle_led_mode(self.nav)
                else:
                    self._reset_current_latch()
            self.cancel_press_start = None
            self.cancel_exit_triggered = False

        if self.buttons.pressed(BUTTON_TYPES["LEFT"]):
            self.nav -= 1
            if self.nav < NAV_STATUS:
                self.nav = NUM_PINS - 1
        elif self.buttons.pressed(BUTTON_TYPES["RIGHT"]):
            self.nav += 1
            if self.nav > NUM_PINS - 1:
                self.nav = NAV_STATUS

        if self.nav != NAV_STATUS:
            self.reset_armed_until = None

        if self.nav == NAV_PORT:
            if self.buttons.pressed(BUTTON_TYPES["UP"]):
                self.port = self.port % 6 + 1
                self._reset_all_pins()
                self._scan_i2c()
                self.i2c_show_all = False
            elif self.buttons.pressed(BUTTON_TYPES["DOWN"]):
                self.port = (self.port - 2) % 6 + 1
                self._reset_all_pins()
                self._scan_i2c()
                self.i2c_show_all = False
            else:
                # Periodic refresh while sitting on this tile, so a
                # hexpansion plugged in while you're looking shows up
                # without needing to nudge the port.
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_i2c_scan_ms) >= 1000:
                    self._scan_i2c()
        elif self.nav == NAV_I2C:
            if self.buttons.pressed(BUTTON_TYPES["UP"]) or self.buttons.pressed(BUTTON_TYPES["DOWN"]):
                self.i2c_show_all = not self.i2c_show_all
            # Periodic re-scan while sitting on this tile, so a hexpansion
            # plugged/unplugged while you're looking at it shows up
            # without needing to nudge the port.
            now = time.ticks_ms()
            if time.ticks_diff(now, self.last_i2c_scan_ms) >= 1000:
                self._scan_i2c()
        elif self.nav == NAV_STATUS:
            if self.buttons.pressed(BUTTON_TYPES["CONFIRM"]):
                self.reset_armed_until = None
                self.remote_active = not self.remote_active
            elif self.buttons.pressed(BUTTON_TYPES["UP"]):
                self.reset_armed_until = None
                self.port = self.port % 6 + 1
                self._reset_all_pins()
                self._scan_i2c()
                self.i2c_show_all = False
            elif self.buttons.pressed(BUTTON_TYPES["DOWN"]):
                self.reset_armed_until = None
                self.port = (self.port - 2) % 6 + 1
                self._reset_all_pins()
                self._scan_i2c()
                self.i2c_show_all = False
        else:
            i = self.nav
            # pressed() is edge-triggered and consumes the event, so read
            # each button exactly once per frame.
            up_pressed = self.buttons.pressed(BUTTON_TYPES["UP"])
            down_pressed = self.buttons.pressed(BUTTON_TYPES["DOWN"])
            if self.buttons.pressed(BUTTON_TYPES["CONFIRM"]):
                # Cycle this pin's family: INPUT -> OUTPUT -> LED (LS pins)
                # or PWM (HS pins) -> INPUT.
                if self.pin_modes[i] in INPUT_FAMILY:
                    self._apply_mode(i, PIN_MODE_OUTPUT_OFF)
                elif self.pin_modes[i] in OUTPUT_FAMILY:
                    self._apply_mode(i, PIN_MODE_LED_OFF if PIN_INFO[i][2] == "ls" else PIN_MODE_PWM)
                else:
                    self._apply_mode(i, PIN_MODE_INPUT)
            elif up_pressed or down_pressed:
                direction = 1 if up_pressed else -1
                if self.pin_modes[i] in INPUT_FAMILY:
                    other = PIN_MODE_LATCH if self.pin_modes[i] == PIN_MODE_INPUT else PIN_MODE_INPUT
                    self._apply_mode(i, other)
                elif self.pin_modes[i] in LED_FAMILY:
                    self._led_adjust(i, direction)
                elif self.pin_modes[i] == PIN_MODE_PWM:
                    self._pwm_adjust(i, direction)
                else:
                    idx = OUTPUT_FAMILY.index(self.pin_modes[i])
                    self._apply_mode(i, OUTPUT_FAMILY[(idx + direction) % len(OUTPUT_FAMILY)])
            if self.pin_modes[i] in LED_FAMILY or self.pin_modes[i] == PIN_MODE_PWM:
                self._hold_repeat(i, up_pressed, down_pressed, lambda d: self._adjust_mode_value(i, d))
            else:
                self.hold_dir = 0

        # Joystick bonus shortcuts (2026 frontboard) -- harmless no-ops if
        # a joystick isn't actually present, since the events just never fire.
        if "A" in JOYSTICK_BUTTON_TYPES and self.buttons.pressed(JOYSTICK_BUTTON_TYPES["A"]):
            self.nav = NAV_STATUS
        if "X" in JOYSTICK_BUTTON_TYPES and self.buttons.pressed(JOYSTICK_BUTTON_TYPES["X"]):
            self._reset_current_latch()
        if "R" in KEYBOARD_BUTTONS and self.buttons.pressed(KEYBOARD_BUTTONS["R"]):
            self._reset_current_latch()

        # Every pin keeps running independently of which one is currently
        # being viewed -- this is what lets e.g. an auto-blinking pin keep
        # blinking, and a latch keep watching, while you look at something
        # else entirely (including the STATUS overview).
        now = time.ticks_ms()
        blink_tick = time.ticks_diff(now, self.last_blink_ms) >= BLINK_PERIOD_MS
        if blink_tick:
            self.last_blink_ms = now
            self.blink_state = not self.blink_state

        for i in range(NUM_PINS):
            mode = self.pin_modes[i]
            if mode in INPUT_FAMILY:
                pin = self._ensure_pin(i)
                if pin is None:
                    continue
                try:
                    v = pin.value()
                    self.live_levels[i] = v
                    if mode == PIN_MODE_LATCH:
                        if v:
                            self.latch_high[i] = True
                        else:
                            self.latch_low[i] = True
                except Exception as e:
                    self.status = "read error: {!r}".format(e)
            elif mode in (PIN_MODE_OUTPUT_OFF, PIN_MODE_OUTPUT_ON):
                self.live_levels[i] = 0 if mode == PIN_MODE_OUTPUT_OFF else 1
            elif mode == PIN_MODE_PWM:
                self.live_levels[i] = self.pwm_duty[i]
            elif mode in LED_FAMILY:
                pin = self._ensure_pin(i)
                if pin is None:
                    continue
                duty = self._led_target(i)
                if duty != self.live_levels[i]:
                    try:
                        pin.duty(duty)
                        self.live_levels[i] = duty
                    except Exception as e:
                        self.status = "write error: {!r}".format(e)
            elif mode == PIN_MODE_OUTPUT_AUTO and blink_tick:
                pin = self._ensure_pin(i)
                if pin is None:
                    continue
                try:
                    pin.value(1 if self.blink_state else 0)
                    self.live_levels[i] = 1 if self.blink_state else 0
                except Exception as e:
                    self.status = "write error: {!r}".format(e)

        return True

    def _reset_current_latch(self):
        if self.nav not in (NAV_STATUS, NAV_PORT, NAV_I2C):
            self.latch_high[self.nav] = False
            self.latch_low[self.nav] = False

    def _draw_status_value(self, ctx, x, y, prefix, value_text, level, prefix_colour):
        # Two-colour status text: the prefix (pin name, or "status = ")
        # stays in its category colour (cyan input / orange output), while
        # the actual value is coloured green for an active/high state and
        # red for an inactive/low one, positioned right after the prefix
        # using its real rendered width so there's no visible gap.
        ctx.rgb(*prefix_colour).move_to(x, y).text(prefix)
        try:
            prefix_w = ctx.text_width(prefix)
        except Exception:
            prefix_w = len(prefix) * 7
        if level is None:
            value_colour = (0.7, 0.7, 0.7)
        else:
            value_colour = (0, 0.9, 0) if level else (1, 0.2, 0.2)
        ctx.rgb(*value_colour).move_to(x + prefix_w, y).text(value_text)

    def _draw_pin_grid(self, ctx, top_y):
        # 2-column grid, 5 rows for up to 9 pins -- stays clear of the
        # hint text at the bottom. Left-aligned at a fixed x (not centred)
        # so the text never shifts position frame to frame as the digit
        # glyph's own rendered width varies slightly in a proportional
        # font -- centring here caused a visible jitter every time the
        # value changed.
        ctx.text_align = ctx.LEFT
        cols_x = (-95, 15)  # roughly symmetric around centre, wide enough for "NAME: HIGH"
        row_step = 18
        for i, (name, _secondary, _kind, _idx) in enumerate(PIN_INFO):
            col = i % 2
            row = i // 2
            x = cols_x[col]
            y = top_y + row * row_step
            level = self.live_levels[i]
            is_output = self.pin_modes[i] in OUTPUT_FAMILY
            if level is None:
                level_text = "?"
            elif self.pin_modes[i] in LED_FAMILY:
                level_text = "{}%".format(self._pct_from_duty(level))
            elif self.pin_modes[i] == PIN_MODE_PWM:
                level_text = "{}%".format(level)
            elif is_output:
                level_text = "ON" if level else "OFF"
            else:
                level_text = "HIGH" if level else "LOW"
            if self.pin_modes[i] in LED_FAMILY:
                colour = COLOR_LED
            else:
                colour = COLOR_OUTPUT if is_output or self.pin_modes[i] == PIN_MODE_PWM else COLOR_INPUT
            if self.pin_auto_detected[i]:
                level_text += "*"  # auto-detected as actively driven on hexpansion insertion
            self._draw_status_value(ctx, x, y, "{}: ".format(name), level_text, level, colour)
        ctx.text_align = ctx.CENTER

    def _hint_text(self):
        if self.nav == NAV_PORT:
            return "U/D: port"
        if self.nav == NAV_I2C:
            return "U/D: back" if self.i2c_show_all else "U/D: show all"
        if self.nav == NAV_STATUS:
            return "C:remote F:reset"
        mode = self.pin_modes[self.nav]
        if mode == PIN_MODE_LED_BREATHE:
            return "U/D:time F:mode C:swap"
        if mode in LED_FAMILY:
            return "U/D:val F:mode C:swap"
        if mode == PIN_MODE_PWM:
            return "U/D:val F:field C:swap"
        return "U/D:mode C:swap"

    def draw(self, ctx):
        ctx.save()
        clear_background(ctx)
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        # Pin-reset confirmation: shown on an otherwise blank display so it
        # doesn't overlap the pin grid underneath.
        if self.reset_armed_until is not None and self.nav == NAV_STATUS:
            if time.ticks_diff(self.reset_armed_until, time.ticks_ms()) > 0:
                ctx.font_size = label_font_size
                ctx.rgb(1, 0.6, 0.2).move_to(0, -25).text("Reset all pins?")
                ctx.font_size = small_font_size
                ctx.rgb(1, 1, 1).move_to(0, 5).text("Press F again to confirm")
                ctx.rgb(0.6, 0.6, 0.6).move_to(0, 28).text("any other button cancels")
                ctx.restore()
                return
            self.reset_armed_until = None

        if self.reset_msg_until is not None and self.nav == NAV_STATUS:
            if time.ticks_diff(self.reset_msg_until, time.ticks_ms()) > 0:
                ctx.font_size = label_font_size
                ctx.rgb(0, 0.9, 0).move_to(0, -10).text("All pins reset")
                ctx.font_size = small_font_size
                ctx.rgb(0.8, 0.8, 0.8).move_to(0, 15).text("to INPUT")
                ctx.restore()
                return
            self.reset_msg_until = None

        if self.nav == NAV_PORT:
            ctx.font_size = label_font_size
            ctx.rgb(1, 1, 1).move_to(0, -55).text("PORT")
            ctx.rgb(1, 0.8, 0).move_to(0, -20).text(str(self.port))
            ctx.font_size = small_font_size
            if self.hexpansion_name:
                ctx.rgb(0, 0.9, 0).move_to(0, 10).text(self.hexpansion_name)
                ctx.rgb(0.8, 0.8, 0.8).move_to(0, 30).text(
                    "VID 0x{:04x}  PID 0x{:04x}".format(self.hexpansion_vid, self.hexpansion_pid))
            else:
                ctx.rgb(0.6, 0.6, 0.6).move_to(0, 10).text("no hexpansion ID")
        elif self.nav == NAV_I2C and self.i2c_show_all:
            # A completely separate, uncluttered layout for the full
            # address list -- U/D toggles here specifically when the
            # compact view's addresses overflowed, rather than squeezing
            # everything onto the same crowded tile.
            ctx.font_size = small_font_size
            ctx.rgb(1, 1, 1).move_to(0, -85).text("Port {}  I2C -- all".format(self.port))
            addr_strs = ["0x{:02x}".format(a) for a in (self.i2c_scan_result or [])]
            groups = [addr_strs[i:i + 3] for i in range(0, len(addr_strs), 3)][:6]
            row_y = -55
            for group in groups:
                ctx.rgb(0, 0.9, 0).move_to(0, row_y).text(", ".join(group))
                row_y += 16
        elif self.nav == NAV_I2C:
            # Port label pinned to the very top (matching STATUS's title
            # position) to leave the middle of the screen free for however
            # many I2C addresses turn up.
            ctx.font_size = small_font_size
            ctx.rgb(1, 1, 1).move_to(0, -88).text("Port {}  I2C".format(self.port))

            if self.i2c_scan_error is not None:
                ctx.rgb(1, 0.4, 0.4).move_to(0, -40).text("scan error")
            elif not self.i2c_scan_result:
                ctx.rgb(0.6, 0.6, 0.6).move_to(0, -40).text("no device found")
            else:
                addr_strs = ["0x{:02x}".format(a) for a in self.i2c_scan_result]
                shown = ", ".join(addr_strs[:3])
                if len(addr_strs) > 3:
                    shown += "..."
                ctx.rgb(0, 0.9, 0).move_to(0, -40).text(shown)

            eeprom = self._check_eeprom()
            if eeprom["mounted"]:
                ctx.rgb(0, 0.9, 0).move_to(0, 10).text("EEPROM: mounted")
                ctx.rgb(0.8, 0.8, 0.8).move_to(0, 30).text("files: {}".format(eeprom["count"]))
                app_colour = (0, 0.9, 0) if eeprom["has_app"] else (1, 0.4, 0.4)
                app_text = "yes" if eeprom["has_app"] else "no"
                ctx.rgb(*app_colour).move_to(0, 50).text("app available: {}".format(app_text))
            else:
                ctx.rgb(0.6, 0.6, 0.6).move_to(0, 10).text("EEPROM: not mounted")
        elif self.nav == NAV_STATUS:
            ctx.font_size = label_font_size
            ctx.rgb(1, 1, 1).move_to(0, -88).text("Pin Tester")
            ctx.font_size = small_font_size
            ctx.rgb(1, 1, 1).move_to(0, -64).text("Port {}  STATUS".format(self.port))
            if self.remote_active:
                ctx.rgb(1, 0.6, 0.2).move_to(0, -40).text("Remote: ON")
            else:
                ctx.rgb(0.5, 0.5, 0.5).move_to(0, -40).text("Remote: OFF")
            self._draw_pin_grid(ctx, top_y=-18)
        else:
            i = self.nav
            name, secondary, _kind, _idx = PIN_INFO[i]
            mode = self.pin_modes[i]
            if mode in LED_FAMILY:
                colour = COLOR_LED
            else:
                colour = COLOR_OUTPUT if mode in OUTPUT_FAMILY or mode == PIN_MODE_PWM else COLOR_INPUT
            title = "{} ({})".format(name, secondary) if secondary else name
            ctx.font_size = small_font_size
            ctx.rgb(*colour).move_to(0, -55).text("Port {}  {}".format(self.port, title))
            if self._hexpansion_present():
                ctx.rgb(1, 0.7, 0).move_to(0, -42).text("⚠ hexpansion present")
            mode_text = PIN_MODE_LABELS[mode]
            if self.pin_auto_detected[i]:
                mode_text += " (auto)"
            ctx.rgb(1, 0.6, 1).move_to(0, -22).text(mode_text)

            level = self.live_levels[i]
            hilo_text = "?" if level is None else ("HIGH" if level else "LOW")
            onoff_text = "?" if level is None else ("ON" if level else "OFF")
            ctx.text_align = ctx.LEFT
            if mode == PIN_MODE_INPUT:
                self._draw_status_value(ctx, -60, -5, "status = ", hilo_text, level, colour)
            elif mode == PIN_MODE_LATCH:
                self._draw_status_value(ctx, -60, -5, "status = ", hilo_text, level, colour)
                ctx.rgb(0.6, 1, 0.6).move_to(-80, 18).text("seen high:{} low:{}".format(self.latch_high[i], self.latch_low[i]))
            elif mode in LED_FAMILY:
                pct = "?" if level is None else "{}%".format(self._pct_from_duty(level))
                set_pct = "{}%".format(self.led_level[i])
                if mode == PIN_MODE_LED_BREATHE:
                    ctx.text_align = ctx.CENTER
                    ctx.rgb(*colour).move_to(0, -5).text(
                        "cycle {}  max {}".format(self._fmt_secs(self.led_period[i]), set_pct))
                    ctx.text_align = ctx.LEFT
                    self._draw_status_value(ctx, -60, 18, "brightness = ", pct, level, colour)
                elif mode == PIN_MODE_LED_ON:
                    self._draw_status_value(ctx, -60, -5, "brightness = ", set_pct, level, colour)
                else:
                    # OFF: the output is dark, but UP/DOWN still edits the
                    # brightness that LED ON / BREATHE will use -- show it.
                    ctx.rgb(*colour).move_to(-60, -5).text("brightness = {}".format(set_pct))
                    self._draw_status_value(ctx, -60, 18, "output = ", "OFF", 0, colour)
            elif mode == PIN_MODE_PWM:
                rows = (("freq", self._fmt_freq(PWM_FREQS[self.pwm_freq_idx[i]])),
                        ("duty", "{}%".format(self.pwm_duty[i])))
                for n, (label, value) in enumerate(rows):
                    active = n == self.pwm_field[i]
                    row_colour = (1, 1, 0.3) if active else (0.8, 0.8, 0.8)
                    ctx.rgb(*row_colour).move_to(-60, -5 + n * 23).text(
                        "{} {} = {}".format(">" if active else " ", label, value))
            elif mode == PIN_MODE_OUTPUT_AUTO:
                ctx.text_align = ctx.CENTER
                ctx.rgb(*colour).move_to(0, -5).text("blinking @2Hz")
                ctx.text_align = ctx.LEFT
                self._draw_status_value(ctx, -60, 18, "status = ", onoff_text, level, colour)
            else:
                self._draw_status_value(ctx, -60, -5, "status = ", onoff_text, level, colour)
            ctx.text_align = ctx.CENTER

        if self.status != "ready":
            ctx.font_size = small_font_size
            ctx.rgb(1, 0.4, 0.4).move_to(0, 52).text(self.status)

        ctx.font_size = small_font_size * HINT_FONT_SCALE
        hint_dy = 4 if self.nav == NAV_STATUS else 0  # STATUS hints sit 4px lower
        ctx.rgb(0.6, 0.6, 0.6).move_to(0, 68 + hint_dy).text(self._hint_text())
        ctx.rgb(0.6, 0.6, 0.6).move_to(0, 88 + hint_dy).text("L/R:tile  hold F:exit")
        ctx.restore()

    def deinit(self):
        self._pwm_stop_all()
        for p in self.pin_objs:
            if p is not None:
                try:
                    p.init(p.IN)
                except Exception:
                    pass


__app_export__ = PinTesterApp
