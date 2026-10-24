# Pin Tester

A Tildagon badge app for testing hexpansion port pin wiring/continuity, independent of any
SPI/I2C driver or protocol. Built while debugging the [Hexi-GFX HDMI mirror
project](https://github.com/Corteil/rp2350-hdmi-hexpansion) after needing a way to confirm
that a given hexpansion port's wires actually reach the far end, without any protocol-level
assumptions getting in the way.

## What it does

Covers all 9 pins a hexpansion port exposes: the 4 high-speed ones (`HS_F`, `HS_G`, `HS_H`,
`HS_I` -- functionally `MOSI`, `SCK`, `CS`, `DC`) and the 5 low-speed/EGPIO ones (`LS_A`
through `LS_E`).

Each pin's mode is tracked and kept running **independently** -- set one pin auto-blinking,
then go look at a different pin's input reading, and the first one keeps blinking in the
background the whole time.

Per-pin modes:
- **INSPECT ONLY** (the default for every pin *while a hexpansion is present on the port*) --
  **hands off**: only reads how the pin is *already* set and never reconfigures it, so you can
  open the app on a port that another app or a hexpansion is using without disturbing it. On an
  **empty** port there's nothing to disturb, so pins default to plain INPUT instead; presence
  comes from the badge's per-port hexpansion-detect line (see below) -- so it works for
  hexpansions with no EEPROM or I2C device too -- plus an already-mounted EEPROM. It's
  re-checked every 0.5s and the pins follow it. A pin whose
  mode you've picked is never changed automatically. Shows direction (input/output), live level,
  pull-up/down (HS pins), whether the pin is routed to a peripheral such as PWM (HS pins), and
  GPIO vs LED mode (LS pins). The STATUS grid shows each as a compact `IN:H`, `OUT:L`, `PWM`,
  `LED`... A pin is only changed once you press CONFIRM on its tile; cycling back round to
  INSPECT releases it to a plain input and stops touching it. Limits: LED brightness can't be
  read back (the expander's dim registers read as 0), and peripheral *input* routing (e.g. a
  pin used as an SPI input) isn't detected. HS pins are read straight from the ESP32 GPIO/IO_MUX
  registers; LS pins from the AW9523B expanders over I2C, and the live level of an LS *input*
  is read the same way INPUT mode does it.
- **INPUT** -- continuously read and display the live level (shown as HIGH/LOW, colour-coded
  green/red).
- **LATCHED** -- like INPUT, but also remembers whether the pin has ever been seen HIGH
  and/or LOW since you last reset it -- catches a brief pulse you'd otherwise have to be
  watching at exactly the right moment to see.
- **OUTPUT: OFF / ON** -- drive the pin to a fixed level.
- **OUTPUT: AUTO** -- auto-blink the pin at ~2Hz, so a toggle counter or multimeter on the far
  end of the wire can confirm continuity without needing precise button timing.
- **LED: OFF / ON / BREATHE** (LS pins only) -- puts the pin in the AW9523B's constant-current
  LED/PWM mode (`pin.init(pin.PWM)` + `pin.duty(0-255)`) for testing an LED wired to the pin.
  UP/DOWN sets the brightness in 1% steps in OFF/ON (the set value is shown even while OFF);
  in BREATHE it fades up and down to that brightness and UP/DOWN sets the cycle time in 0.1s
  steps (0.2s to 10.0s). F (short press) cycles
  OFF -> ON -> BREATHE. Not available on HS pins (plain ESP32 GPIOs).

Tiles (reached with LEFT/RIGHT, wrapping): **STATUS** (every pin's live value at once,
colour-coded input/output), **PORT** (pick which hexpansion port, 1-6 -- also shows the
identification EEPROM's friendly name and VID/PID if a real hexpansion is present, checking
both the `0x50` and `0x57` EEPROM addresses since larger EEPROMs use the latter), **I2C**
(scans the port's I2C bus for a real device, and reports whether the system has an EEPROM
filesystem mounted there -- file count, and whether an app is available), then each pin in
turn.

- **PWM** (HS pins only) -- real ESP32 hardware PWM on the pin, 10 Hz to 100 kHz in 1-2-5 steps
  and duty 0-100% in 1% steps (the firmware can't generate below 10 Hz). CONFIRM cycles
  INSPECT -> INPUT -> OUTPUT -> PWM, F switches between editing frequency and duty, UP/DOWN changes the
  field being edited (auto-repeating while held). Starts at 1 kHz / 50%. Each pin keeps its own
  PWM settings and runs independently. The LS pins can't do this (the expander only has
  fixed-frequency LED dimming) -- use their LED modes instead.

### Hexpansion safety

If a real hexpansion is detected present on the current port (an I2C device answers, or its
EEPROM filesystem is mounted), pin tiles show a "⚠ hexpansion present" warning -- OUTPUT
modes are still fully usable, this is informational only, so you don't accidentally drive a
pin a live device is also driving.

The moment a hexpansion is freshly detected on a port, every pin is passively probed once (a
brief ~0.5s pause): each pin is watched at its own natural, unbiased level for a short window,
and flagged "active" if it's ever seen to toggle during that window (no pull resistor bias is
applied, so this can't itself perturb anything on the far end). Pins still in INSPECT are only
watched, never reconfigured; any pin you've taken over lands on INPUT regardless of the result -- the probe only ever confirms activity, never a reason to choose
OUTPUT for you. Pins found active are marked "(auto)" on their own tile and with a trailing
`*` on the STATUS grid.

## Controls

Works on any badge/frontboard:

| Button | Action |
|---|---|
| LEFT / RIGHT | Move between tiles: STATUS, PORT, I2C, then the LS pins (LS_A-LS_E), then the HS pins (HS_F-HS_I). **Hold** to jump a whole section at a time: home -> LS pins -> HS pins |
| UP / DOWN (on PORT or STATUS tile) | Change the hexpansion port (1-6) -- hands every pin back (anything we took over is released to INPUT, then all pins return to read-only INSPECT), since it's a different physical connector |
| UP / DOWN (on I2C tile) | Toggle between the compact address list and a full-list view (when there are more addresses than fit compactly) |
| CONFIRM (on STATUS tile) | Toggle "Remote: ON/OFF" (see below) |
| CONFIRM (on a pin tile) | Cycle that pin's family: INSPECT (read-only) -> INPUT/LATCHED -> OUTPUT (OFF/ON/AUTO) -> LED (LS pins) or PWM (HS pins) -> back to INSPECT |
| UP / DOWN (on a pin tile) | Within INPUT family: toggle INPUT <-> LATCHED. Within OUTPUT family: cycle OFF -> ON -> AUTO. Within LED family: brightness (OFF/ON) or breathe cycle time. F short press cycles LED OFF/ON/BREATHE |
| CANCEL, short press | On the STATUS tile: hand every pin back: anything we took over is released to INPUT, then all pins return to read-only INSPECT (also clears LED and PWM settings) -- the first press only asks "Reset all pins?", press F again within 3s to confirm, any other button cancels. On a pin tile: reset that pin's LATCHED indicator (or cycle LED OFF/ON/BREATHE in an LED mode) |
| CANCEL, held | Exit the app |

Joystick bonus (2026 frontboard only -- harmless no-ops on other hardware):

| Button | Action |
|---|---|
| A | Jump straight to the STATUS tile |
| X | Also resets the current pin's latch |

Keyboard hexpansion bonus: **R** also resets the current pin's latch. A keyboard's arrow keys
and ENTER already work as UP/DOWN/LEFT/RIGHT/CONFIRM automatically (aliased at the
button-event level), so no extra setup is needed there.

## Driving it from a computer

Two ways, depending on whether the app needs to keep running live on screen or not.

### One-shot, app doesn't need to be running

`pin_control.py` is a plain, standalone module the app itself is just a thin UI wrapper
around -- import and call it directly over USB from `mpremote`, without the app running or
any physical interaction with the badge. Note this goes through `mpremote exec`, which always
sends Ctrl-C first and will kill the app if it happens to be running and on screen at the
time:

```sh
mpremote connect /dev/ttyACM0 exec "import sys; sys.path.insert(0, '/apps/pin_tester'); \
    import pin_control; pin_control.blink_all(1, 4000)"
```

Import it via `sys.path` like that, not as `apps.pin_tester.pin_control` -- the dotted form
runs `apps/pin_tester/__init__.py` first (normal package-import semantics), which pulls in
`app.py`'s own dependency chain and fails unless the badge's scheduler is already running.
Importing straight off `sys.path` skips that entirely and works standalone.

Available functions: `read(port, name)`, `write(port, name, value)`,
`blink(port, name, duration_ms=3000, period_ms=250)`, `blink_all(port, ...)`,
`read_all(port)`, `latch(port, name, duration_ms=3000, poll_ms=2)`, `led(port, name, ...)`, and
`inspect_pins(port, names=None)` -- a read-only snapshot of how pins are currently configured
(func/dir/level/pull for HS, func/dir/level for LS) that never calls `.init()`, so it's safe
against a port something else is using.

### Live, while the app keeps running on screen

Enable **"Remote: ON"** from the STATUS tile (CONFIRM), then write plain text commands
directly to the same serial port -- **not** through `mpremote exec`/`cp`, which would
interrupt the app. This channel is a non-blocking `stdin` poll built into the app's own
update loop, so it never touches Ctrl-C or raw-REPL at all:

```sh
stty -F /dev/ttyACM0 raw -echo
cat /dev/ttyACM0 &            # read responses in the background
printf 'GET HS_F\n' > /dev/ttyACM0
printf 'SET HS_F ON\n' > /dev/ttyACM0
```

Commands, one per line:
- `STATUS` -- every pin's `name=level`
- `INSPECT [<pin>]` -- fresh read-only snapshot of how the pin(s) are currently configured
- `SET <pin> INSPECT` -- hands off again (releases the pin to INPUT, then read-only)
- `GET <pin>` -- one pin's current level
- `SET <pin> IN|LATCH|OFF|ON|AUTO` -- change a pin's mode
- `SET <pin> PWM [hz [duty]]` -- HS pins only
- `SET <pin> LEDOFF|LEDON|BREATHE [n]` -- LED modes, LS_A..LS_E only; optional `n` is brightness 0-100 (LEDON) or cycle time in 0.1s units, 2-100 (BREATHE)

This only has any effect while "Remote: ON" is showing on the STATUS tile -- otherwise
incoming commands are silently ignored, so a pin can never change remotely by surprise.

## Installing

Copy the contents of this repo onto the badge's `/apps/pin_tester/` directory, e.g. via
`mpremote cp`, then reset the badge -- it'll show up as "Pin Tester" under Settings in the
launcher menu.

## Known limitations

The hexpansion connector's dedicated `HEXP_DET` presence-detect line (pulled up on the badge,
tied to GND by a real hexpansion PCB on insertion -- see the [Hexi-GFX
README](https://github.com/Corteil/rp2350-hdmi-hexpansion)'s own pinout section) is **not** a
pin on the port, so it isn't testable like the HS/LS pins -- but the badge does expose it:
`system.hexpansion.app` reads it from six expander pins, `ePin(tildagonos.EPIN_ND_A..F)` for
ports 1-6, where `.value() == 0` means a hexpansion is plugged in. This app reads those to decide
whether a port is in use (INSPECT ONLY vs plain INPUT) and shows the state on the PORT tile
(`detect line: present / empty`). It can only tell you something is plugged in, not whether
`HEXP_DET` is wired correctly on a particular board beyond that.

## Pin naming

The primary name for every pin is its physical hexpansion label -- `HS_F`..`HS_I` and
`LS_A`..`LS_E`, one contiguous A-I lettering across low- and high-speed pins. The 4 HS pins
additionally carry a functional name (`MOSI`, `SCK`, `CS`, `DC`, in that order), both
resolvable from either name. That order was cross-checked against both
`system.hexpansion.config.HexpansionConfig`'s own pin ordering and
[hazanjon's independent `PORT_PINS` table](https://github.com/emfcamp/badge-2024-software/pull/454)
across all 6 ports -- there's no MISO line in that convention, the 4th HS pin is DC.

## License

MIT
