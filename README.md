# Pin Tester

A Tildagon badge app for testing hexpansion port pin wiring/continuity, independent of any
SPI/I2C driver or protocol. Built while debugging the [Hexi-GFX HDMI mirror
project](https://github.com/Corteil/rp2350-hdmi-hexpansion) after needing a way to confirm
that a given hexpansion port's wires actually reach the far end, without any protocol-level
assumptions getting in the way.

## What it does

For a chosen hexpansion port (1-6) and pin (MOSI, SCK, CS, or DC), you can:

- **INPUT** -- continuously read and display the live level, with a latch that remembers
  whether the pin has ever been seen HIGH and/or LOW since you last switched to it (catches a
  brief pulse you'd otherwise have to be watching at exactly the right moment to see).
- **OUTPUT: OFF / ON** -- drive the pin to a fixed level.
- **OUTPUT: AUTO** -- auto-blink the pin at ~2Hz, so a toggle counter or multimeter on the far
  end of the wire can confirm continuity without needing precise button timing.
- **STATUS** -- a single screen showing the live level of all four pins on the current port
  at once.
- **REMOTE** -- a passive, hands-off display of the live level, for watching a pin while
  something *else* (another device, or a script on your computer) drives it.

## Controls

Works on any badge/frontboard:

| Button | Action |
|---|---|
| UP / DOWN | Select hexpansion port (1-6) |
| LEFT / RIGHT | Select pin |
| CONFIRM | Cycle mode: INPUT -> OUTPUT:OFF -> OUTPUT:ON -> OUTPUT:AUTO -> STATUS -> REMOTE -> ... |
| CANCEL | Exit |

Joystick bonus (2026 frontboard only -- harmless no-ops on other hardware):

| Button | Action |
|---|---|
| A | Jump straight to the STATUS page |
| X | Clear the INPUT-mode latch immediately |

A keyboard hexpansion's arrow keys and ENTER already work as UP/DOWN/LEFT/RIGHT/CONFIRM
automatically (they're aliased at the button-event level), so no extra setup is needed there.

## Driving it from a computer, no button presses needed

`pin_control.py` is a plain, standalone module the app itself is just a thin UI wrapper
around -- you can import and call it directly over USB from `mpremote`, without the app
running or any physical interaction with the badge at all. Handy for automated or unattended
continuity testing (e.g. correlating with a separate microcontroller's own GPIO toggle
counters on the far end of the wire):

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
`read_all(port)`, and `latch(port, name, duration_ms=3000, poll_ms=2)` (the USB-side
equivalent of the app's own INPUT-mode latch).

## Installing

Copy the contents of this repo onto the badge's `/apps/pin_tester/` directory, e.g. via
`mpremote cp`, then reset the badge -- it'll show up as "Pin Tester" under Settings in the
launcher menu.

## Pin naming

`MOSI`, `SCK`, `CS`, `DC` (in that order) is the correct, consistent mapping of a
hexpansion port's 4 high-speed pins across all 6 ports, cross-checked against both
`system.hexpansion.config.HexpansionConfig`'s own pin ordering and
[hazanjon's independent `PORT_PINS` table](https://github.com/emfcamp/badge-2024-software/pull/454).
There's no MISO line in that convention -- the 4th pin is DC.

## License

MIT
