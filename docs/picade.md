# FlightScnr Pi — Picade cabinet version

This branch (`community/picade`) adapts FlightScnr Pi to an arcade cabinet: a large
rectangular screen instead of the round 4in panel, and a joystick with panel
buttons instead of a touchscreen.

Nothing here is Picade-specific by design — the same settings run FlightScnr on
**any screen attached to a Pi**. See [Using another screen](#using-another-screen).

![FlightScnr running on a Picade Max cabinet](images/picade-cabinet.jpg) · ![The panel close up](images/picade-radar-screen.jpg)

![Picade Max one-player control layout](images/picade-max-controls.png)

<sub>Image: Pimoroni, "Setting up Picade Max" —
https://learn.pimoroni.com/article/setting-up-picade-max</sub>

---

## What changes

| | Upstream | This branch |
|---|---|---|
| Screen | round 4in DSI panel, 720×720 | any panel; tested on 1280×1024 |
| Pointing | capacitive touchscreen | mouse, or joystick mapped to the pointer |
| Navigation | swipe gestures | panel buttons |
| Range | pinch to zoom | zoom buttons |

## Display

The radar is drawn into a **square** buffer, centred on the panel, with the
sides left black. On a 1280×1024 screen that gives a 1024×1024 radar and two
128px bands:

```ini
DISPLAY_WIDTH=1024
DISPLAY_HEIGHT=1024
DISPLAY_ROTATION=0
```

![Radar on a 1280x1024 panel: a 1024 square, centred, sides blacked out](images/picade-screen-radar.jpg)

Three adaptations were needed for a screen this large:

- **`UI_SCALE`** — layout sizes derive from the panel resolution, so a big panel
  magnifies every label and icon. This multiplier decouples the chrome from the
  resolution: below 1.0 it shrinks fonts, aircraft icons and grid dashes,
  leaving more of the circle for map detail. The basemap picks its own tile zoom
  and is unaffected. `1.0` keeps the stock proportions.
- **Pointer offset** — a window narrower than the panel is centred, but SDL
  reports pointer positions in desktop space. Without correction every click
  lands offset by the side band: menus still work (their targets span the width)
  while aircraft can never be hit. The offset is now subtracted before
  hit-testing, and is zero on square panels.
- **Chrome weight** — the corner mask outside the circle is painted black so it
  matches the side bands, range rings and crosshairs are drawn hairline, and the
  crosshairs stop short of the N/E/S/W letters instead of striking through them.

## Input

There is no touchscreen, so the two input paths are:

| Layer | Component | Role |
|---|---|---|
| System | `picade-pointer.service` | joystick and some buttons → virtual mouse and keyboard (`/dev/uinput`) |
| Application | FlightScnr (`joystick_nav.py`) | panel buttons read from SDL → UI actions |

The `flightscnr/setup/picade-pointer` daemon creates `Picade Virtual Mouse` and
`Picade Virtual Keyboard`. FlightScnr knows nothing about them: it receives
ordinary mouse and keyboard events, which is the path it already uses under
Wayland/Xwayland. Stick motion uses an acceleration ramp — slow at first for
aiming, faster while a direction is held.

The stick is **not** read by the application itself: pointer duty and swipe duty
would conflict on the same axes.

### System layer — pointer and keys

| Button | Emits |
|---|---|
| Joystick | pointer motion |
| X | left click |
| Y | right click |
| Select (black) | Enter |
| Start | Tab |
| Red, next to Start | Space |

### Application layer — FlightScnr bindings

Bound by SDL button index in `/etc/flightscnr.env`:

| Button | SDL index | Setting | Action |
|---|---|---|---|
| B | 1 | `JOYSTICK_BTN_RADAR` | back to radar |
| L1 | 6 | `JOYSTICK_BTN_SWIPE_LEFT` | swipe left |
| R1 | 7 | `JOYSTICK_BTN_SWIPE_RIGHT` | swipe right |
| L3 | 10 | `JOYSTICK_BTN_SWIPE_UP` | swipe up |
| R3 | 11 | `JOYSTICK_BTN_SWIPE_DOWN` | swipe down |
| L2 | 8 | `JOYSTICK_BTN_ZOOM_OUT` | zoom out (wider range) |
| R2 | 9 | `JOYSTICK_BTN_ZOOM_IN` | zoom in (shorter range) |

What the two swipe buttons do depends on the screen:

| Screen | Swipe left | Swipe right | Swipe up | Swipe down |
|---|---|---|---|---|
| Radar | flip board (when the layer is on) | Tracked flights | About | Clock |
| About | Settings | — | — | back to radar |
| Settings | previous page, then About | next page | scroll | scroll |
| Flight detail | previous flight | next flight | scroll | scroll |
| Fire detail | previous fire | next fire | scroll | scroll |

Left and right follow the page dots at the top of the paged screens: right
advances the lit dot rightwards, left walks it back. Settings sits beside
About, so it takes up then left from the radar.

Every binding is unset by default, so a touchscreen install is unaffected.

### Screens

The upstream screens are unchanged — only the way you reach them differs. Flight
detail is opened by clicking an aircraft, then walked with the swipe buttons
instead of the PREV / NEXT footer; Settings pages are stepped with the same two
buttons rather than tapping the breadcrumb.

![Flight detail](images/picade-screen-flight.jpg) · ![Settings](images/picade-screen-settings.jpg)

### Finding a button index

SDL indices are not printed on the panel. Press an unbound button and read the
log:

```bash
journalctl -u flightscnr -f | grep "cabinet button"
# INFO: Unbound cabinet button 4 pressed
```

## Configuration

```ini
# /etc/flightscnr.env
DISPLAY_WIDTH=1024
DISPLAY_HEIGHT=1024
DISPLAY_ROTATION=0
UI_SCALE=0.7                # chrome scale, 0.2-3.0 (1.0 = stock)
SHOW_MOUSE_CURSOR=True      # with no touchscreen you need to see where you aim

JOYSTICK_BTN_SWIPE_LEFT=6
JOYSTICK_BTN_SWIPE_RIGHT=7
JOYSTICK_BTN_SWIPE_UP=10
JOYSTICK_BTN_SWIPE_DOWN=11
JOYSTICK_BTN_RADAR=1
JOYSTICK_BTN_ZOOM_OUT=8
JOYSTICK_BTN_ZOOM_IN=9
```

## Known limitations

- **Sliders are awkward with the stick.** Brightness, ATC and chime volume, HUD
  and VFR opacity, and the theme RGB channels are all press-and-drag controls.
  Driving them means holding the click button while steering the stick, which is
  imprecise. They remain easy with a real mouse. *TODO: step them with the swipe
  or zoom buttons when a slider has focus.*
- **No pinch to zoom.** Pinch needs multi-touch, which pointer emulation cannot
  provide. Use the zoom buttons, or Settings → Options → Range.
- **The boot safety disclaimer needs a pointer.** It is mandatory and must not
  be bypassed; it is remembered between boots and auto-continues, but the first
  acceptance on a fresh install wants a mouse click.

## Using another screen

Nothing above is tied to the Picade. For any panel on a Pi:

1. Set `DISPLAY_WIDTH` and `DISPLAY_HEIGHT` to the **square side** you want —
   normally the smaller of the panel's two dimensions, so the radar fills the
   height. The app centres that square and blacks out the rest.
2. Tune `UI_SCALE` until labels and icons look right at your viewing distance.
   Larger panel or closer viewing → smaller value.
3. Leave the rest alone: the pointer offset is computed from the panel and the
   buffer, so clicks land correctly with no extra setting.

A touchscreen of any size works the same way, without the pointer daemon or the
button bindings.

## Dual boot with Recalbox

A cabinet usually still has to play games. Rather than reinstall anything, the
**original Recalbox stays untouched on the NVMe** and Raspberry Pi OS with
FlightScnr goes on a **separate USB SSD**. Nothing is shared between the two —
either drive can be wiped without affecting the other.

| Drive | System | Origin |
|---|---|---|
| NVMe (`nvme0n1`) | Recalbox | the cabinet's original install, left as-is |
| USB SSD (`sda`) | Raspberry Pi OS + FlightScnr | added for this project |

The cabinet power button is wired to **GPIO17** through a latching
power-management board (OnOff SHIM style), and the bootloader EEPROM picks the
drive from the button state. This is the full config, as flashed:

```ini
[all]
BOOT_UART=1
POWER_OFF_ON_HALT=1
BOOT_ORDER=0xf14

[gpio17=0]
BOOT_ORDER=0xf16
```

| Boot order | Sequence | Selected when |
|---|---|---|
| `0xf14` | USB → SD → retry | GPIO17 high → **Pi OS / FlightScnr** |
| `0xf16` | NVMe → SD → retry | GPIO17 pulled low during boot → **Recalbox** |

Digits are read right to left: `4` = USB-MSD, `6` = NVMe, `1` = SD card, and the
leading `f` restarts the sequence instead of giving up.

Apply it with `sudo rpi-eeprom-config --edit`, then reboot.

### First boot from the USB SSD

Before that config is flashed the Pi boots the NVMe, so the freshly written USB
SSD is never reached. Two ways out for that first boot:

- **Unplug the NVMe.** Crude, always works.
- **Use the bootloader menu.** Plug a USB keyboard into the Pi, hold **Space**,
  and power the cabinet on with the button. The bootloader lists the bootable
  devices; pick the USB SSD.

> **Keyboards are the hard part here.** At bootloader stage USB support is much
> narrower than under Linux: several keyboards are simply never seen, and among
> those that are, some only enumerate on the **USB 2.0** ports while others only
> work on **USB 3.0**. If Space appears to be ignored, that is the first thing to
> suspect — try the other pair of ports, then another keyboard, before
> concluding the menu is unavailable. A keyboard that works perfectly once the
> desktop is up proves nothing about this stage.

### Choosing the system at power-on

The same button powers the cabinet **and** drives GPIO17, so the order of
presses is what selects the system.

**Pi OS / FlightScnr** — short press, then leave the button alone. GPIO17 stays
high, the `[gpio17=0]` filter does not apply, and `0xf14` boots the USB SSD.

**Recalbox** — three steps:

1. **Start from a cabinet that is completely off.** The Picade power HAT cuts
   the supply at shutdown, so the Pi is genuinely unpowered rather than halted.
   The bootloader only samples GPIO17 on a real cold start, so a reboot from a
   running system cannot select Recalbox.
2. **Short press** — the HAT latches the power on and the Pi starts.
3. **Press again immediately**, and keep it down briefly. The bootloader
   evaluates `[gpio17=0]` a moment into boot; seeing the pin low it switches to
   `0xf16` and boots the NVMe.

Release within the first three seconds. Once the kernel loads, the
`gpio-shutdown` overlay watches the same pin and a press still held reads as a
shutdown request — the machine powers straight back off.

**This works on a Pi 5, which is not obvious.** The `[gpioNN=X]` filters are
documented for the 2711 bootloader but not the 2712 one, and GPIO 0-27 sit
behind the RP1 over PCIe — there was good reason to expect them to be ignored
this early in boot. They are evaluated.

### Pitfalls

- **`rpi-eeprom-config` with no argument reads `blconfig`** — the config loaded
  at boot, not the flash. After a successful write it still prints the old
  values until you reboot; that is not a failure. To read the flash:
  ```bash
  sudo rpi-eeprom-ab dump out.bin && sudo rpi-eeprom-config out.bin
  ```
  On a Pi 5, `rpi-eeprom-config --edit` writes through `rpi-eeprom-ab` (A/B
  slots, committed immediately), so there is no pending `pieeprom.upd` in
  `/boot/firmware` to look for.
- **Recalbox rewrites the EEPROM.** `/etc/init.d/S01rpieeprom` reflashes once
  per Recalbox version, but only when `POWER_OFF_ON_HALT=0` or
  `NET_INSTALL_AT_POWER_ON=1`. Keeping `POWER_OFF_ON_HALT=1` disables that path.
  It does preserve conditional sections, since it starts from a dump of the
  current config.
- **Clean shutdown from the Pi OS side** needs the same overlays Recalbox uses,
  in `/boot/firmware/config.txt`:
  ```ini
  dtoverlay=gpio-shutdown,gpio_pin=17,active_low,debounce=2000
  dtoverlay=gpio-poweroff,gpiopin=4,active_low
  ```
  The 2s debounce means a deliberate long press. Release the button within the
  first three seconds of boot, otherwise `gpio-keys` powers the machine off as
  soon as the kernel loads.

## Installation

`install-pi.sh` handles the pointer service in `install_pointer_service()`: the
systemd unit is generated from `flightscnr/setup/picade-pointer.service`, and the
`uinput` module is queued for boot (`/etc/modules-load.d/picade-pointer.conf`) —
without it `/dev/uinput` does not exist and the daemon fails at every boot.
