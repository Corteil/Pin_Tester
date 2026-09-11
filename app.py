# Hexpansion Pin Tester -- a small diagnostic app for checking wiring
# continuity on a badge hexpansion port, independent of any SPI/I2C driver
# or protocol. Built for the Hexi-GFX HDMI mirror project after a
# protocol-agnostic RP2350-side GPIO snoop needed to confirm continuity on
# each of a port's 4 pins.
#
# This is a thin UI wrapper over pin_control.py -- see that module for the
# actual pin logic, and for how to drive/read pins directly over
# USB/REPL (no button presses, no need for this app to even be running)
# for unattended/automatable continuity testing.
#
# Controls (work on any badge/frontboard):
#   UP/DOWN     -- select hexpansion port (1-6)
#   LEFT/RIGHT  -- select pin (see pin_control.PIN_INFO for names)
#   CONFIRM     -- cycle mode: INPUT -> OUTPUT:OFF -> OUTPUT:ON ->
#                  OUTPUT:AUTO -> STATUS -> REMOTE -> INPUT -> ...
#   CANCEL      -- exit
# Joystick bonus (2026 frontboard only -- harmless no-ops elsewhere):
#   X           -- clear the INPUT-mode latch immediately
#   A           -- jump straight to the STATUS page
# A keyboard hexpansion's arrow keys/ENTER already alias to the controls
# above automatically (see events/keyboard.py's own parent-button
# declarations), so no extra code is needed for that case.

import app
import time
from machine import Pin
from events.input import Buttons, BUTTON_TYPES
from app_components.tokens import clear_background, small_font_size, label_font_size
try:
    from events.joystick import JOYSTICK_BUTTON_TYPES
except ImportError:
    JOYSTICK_BUTTON_TYPES = {}
try:
    from apps.pin_tester.pin_control import PIN_INFO, _resolve
except ImportError:
    from pin_control import PIN_INFO, _resolve

MODE_INPUT = 0
MODE_OUTPUT_OFF = 1
MODE_OUTPUT_ON = 2
MODE_OUTPUT_AUTO = 3
MODE_STATUS = 4
MODE_REMOTE = 5
NUM_MODES = 6
MODE_LABELS = ("INPUT", "OUTPUT: OFF", "OUTPUT: ON", "OUTPUT: AUTO", "STATUS", "REMOTE")

BLINK_PERIOD_MS = 250  # ~2Hz full cycle


class PinTesterApp(app.App):
    def __init__(self):
        super().__init__()
        self.buttons = Buttons(self)
        self.port = 1
        self.pin_idx = 0
        self.mode = MODE_INPUT
        self.pin_obj = None
        self.status = ""
        self.live_level = None
        self.latch_high = False
        self.latch_low = False
        self.blink_state = False
        self.last_blink_ms = time.ticks_ms()
        self.status_levels = None
        self._reconfigure()

    def _reconfigure(self):
        self.latch_high = False
        self.latch_low = False
        if self.mode == MODE_STATUS:
            self.pin_obj = None
            self.status = "ready"
            return
        name = PIN_INFO[self.pin_idx][0]
        try:
            raw_pin = _resolve(self.port, name)
        except Exception as e:
            self.status = "config error: {!r}".format(e)
            self.pin_obj = None
            return
        try:
            if self.mode == MODE_OUTPUT_OFF:
                raw_pin.init(Pin.OUT, value=0)
            elif self.mode == MODE_OUTPUT_ON:
                raw_pin.init(Pin.OUT, value=1)
            elif self.mode == MODE_OUTPUT_AUTO:
                raw_pin.init(Pin.OUT, value=0)
                self.blink_state = False
                self.last_blink_ms = time.ticks_ms()
            else:  # INPUT or REMOTE
                raw_pin.init(Pin.IN)
            self.pin_obj = raw_pin
            self.status = "ready"
        except Exception as e:
            self.status = "pin error: {!r}".format(e)
            self.pin_obj = None

    def update(self, delta):
        if self.buttons.pressed(BUTTON_TYPES["CANCEL"]):
            self.buttons.clear()
            self.minimise()
            return True

        changed = False
        if self.buttons.pressed(BUTTON_TYPES["UP"]):
            self.port = self.port % 6 + 1
            changed = True
        elif self.buttons.pressed(BUTTON_TYPES["DOWN"]):
            self.port = (self.port - 2) % 6 + 1
            changed = True
        elif self.buttons.pressed(BUTTON_TYPES["LEFT"]):
            self.pin_idx = (self.pin_idx - 1) % len(PIN_INFO)
            changed = True
        elif self.buttons.pressed(BUTTON_TYPES["RIGHT"]):
            self.pin_idx = (self.pin_idx + 1) % len(PIN_INFO)
            changed = True
        elif self.buttons.pressed(BUTTON_TYPES["CONFIRM"]):
            self.mode = (self.mode + 1) % NUM_MODES
            changed = True

        # Joystick bonus shortcuts (2026 frontboard) -- harmless if a
        # joystick isn't actually present, since the events just never fire.
        if "A" in JOYSTICK_BUTTON_TYPES and self.buttons.pressed(JOYSTICK_BUTTON_TYPES["A"]):
            self.mode = MODE_STATUS
            changed = True
        if "X" in JOYSTICK_BUTTON_TYPES and self.buttons.pressed(JOYSTICK_BUTTON_TYPES["X"]):
            self.latch_high = False
            self.latch_low = False

        if changed:
            self._reconfigure()

        if self.mode == MODE_STATUS:
            try:
                levels = {}
                for i, (nm, _label) in enumerate(PIN_INFO):
                    p = _resolve(self.port, nm)
                    p.init(Pin.IN)
                    levels[nm] = p.value()
                self.status_levels = levels
                self.status = "ready"
            except Exception as e:
                self.status = "status error: {!r}".format(e)
        elif self.pin_obj is not None:
            if self.mode in (MODE_INPUT, MODE_REMOTE):
                try:
                    self.live_level = self.pin_obj.value()
                    if self.mode == MODE_INPUT:
                        if self.live_level:
                            self.latch_high = True
                        else:
                            self.latch_low = True
                except Exception as e:
                    self.status = "read error: {!r}".format(e)
                    self.live_level = None
            elif self.mode == MODE_OUTPUT_AUTO:
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_blink_ms) >= BLINK_PERIOD_MS:
                    self.last_blink_ms = now
                    self.blink_state = not self.blink_state
                    try:
                        self.pin_obj.value(1 if self.blink_state else 0)
                    except Exception as e:
                        self.status = "write error: {!r}".format(e)

        return True

    def draw(self, ctx):
        ctx.save()
        clear_background(ctx)
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE

        ctx.font_size = label_font_size
        ctx.rgb(1, 1, 1).move_to(0, -85).text("Pin Tester")

        if self.mode == MODE_STATUS:
            ctx.font_size = small_font_size
            ctx.rgb(0, 1, 1).move_to(0, -60).text("Port {}  STATUS".format(self.port))
            if self.status_levels:
                y = -30
                for name, _label in PIN_INFO:
                    lvl = self.status_levels.get(name, "?")
                    ctx.rgb(1, 1, 0).move_to(0, y).text("{}: {}".format(name, lvl))
                    y += 22
        else:
            name, label = PIN_INFO[self.pin_idx]
            ctx.font_size = small_font_size
            ctx.rgb(0, 1, 1).move_to(0, -60).text("Port {}  {} ({})".format(self.port, name, label))
            ctx.rgb(1, 0.6, 1).move_to(0, -35).text(MODE_LABELS[self.mode])

            if self.mode == MODE_INPUT:
                level_text = "?" if self.live_level is None else str(self.live_level)
                ctx.rgb(1, 1, 0).move_to(0, -5).text("level = {}".format(level_text))
                latch_text = "HIGH" if self.latch_high else "-", "LOW" if self.latch_low else "-"
                ctx.rgb(0.6, 1, 0.6).move_to(0, 18).text("latch: seen {} / {}".format(*latch_text))
            elif self.mode == MODE_REMOTE:
                level_text = "?" if self.live_level is None else str(self.live_level)
                ctx.rgb(1, 0.6, 0.2).move_to(0, -5).text("externally controlled")
                ctx.rgb(1, 1, 0).move_to(0, 18).text("level = {}".format(level_text))
            elif self.mode == MODE_OUTPUT_AUTO:
                ctx.rgb(1, 0.5, 0).move_to(0, -5).text("blinking @2Hz")
                ctx.rgb(0.6, 1, 0.6).move_to(0, 18).text("driving: {}".format(1 if self.blink_state else 0))
            else:
                driven = 0 if self.mode == MODE_OUTPUT_OFF else 1
                ctx.rgb(0.6, 1, 0.6).move_to(0, -5).text("driving: {}".format(driven))

        ctx.font_size = small_font_size
        ctx.rgb(0.7, 0.7, 0.7).move_to(0, 50).text(self.status)

        ctx.rgb(0.6, 0.6, 0.6).move_to(0, 75).text("U/D: port  L/R: pin")
        ctx.rgb(0.6, 0.6, 0.6).move_to(0, 95).text("C: mode  F: exit")
        ctx.restore()

    def deinit(self):
        if self.pin_obj is not None:
            try:
                self.pin_obj.init(Pin.IN)
            except Exception:
                pass


__app_export__ = PinTesterApp
