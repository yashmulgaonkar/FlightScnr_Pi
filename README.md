# FlightScnr Pi
A round **4in touch display** flight tracker for Raspberry Pi. The on-device UI is modeled after my other project, [FlightScnr](https://github.com/yashmulgaonkar/FlightScnr): dark radar aesthetic, animated sweep, map tiles, and gesture navigation. A built-in **web portal** configures everything from your phone or laptop on the same network.

![FlightScnr Pi on a round display](docs/images/flightscnrpi.jpg)


<p align="center">
  <a href="https://buymeacoffee.com/yashmulgaonkar" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" style="height: 35px;">
  </a>
</p>

---

## What it does

FlightScnr Pi shows live aircraft around your pre set location on a circular radar, with rich flight details when you tap a plane. It combines **FlightRadar24 (FR24)**, live positions from **[adsb.fi](https://adsb.fi)** (free cloud feed — no local ADS-B dongle), **Tomorrow.io weather**, and optional **AirLabs** schedule data. Settings, API keys, tracking, and updates are managed through a local web portal. No SSH required for day-to-day use.

### Round touch display

The UI is designed for a **4in round LCD with touch** (default layout: **720x720**).

| Screen | How to open | What you see |
|--------|-------------|--------------|
| **Radar** | Boot / home | Live aircraft, map background, sweep line, compass rose, range label, altitude tags |
| **Flight detail** | Tap aircraft on radar | Airline logo, route, type, altitude, speed, heading; swipe or footer to cycle aircraft |
| **Tracked flight** | Web portal → Track, or swipe right on radar | Route header, progress bar with aircraft icon, LIVE/ETA, vertical speed ticker; pin to keep screen open |
| **Clock and current weather** | Swipe down from radar | Time, date, current weather and conditions |
| **Weather Forecast** | Swipe right from clock | Multi-day forecast (Tomorrow.io) |
| **Clock settings** | Swipe left from clock | Clock format and related options on-device |
| **About / details** | Swipe up from radar | Version, network, API status, portal URL |
| **Settings** | Swipe left from radar | Brightness, timeouts, color theme, display options (multi-page) |

**Gestures and controls**

- **Tap** aircraft → flight detail
- **Tap** range label (top) → cycle zoom presets (2–30 mi / km / nm)
- **Two-finger pinch** → zoom radar range in and out
- **Swipe** between screens (see table above)
- **Footer buttons** on detail, tracked, and settings screens (PREV / NEXT / RADAR / PIN)
- **Auto-return** to clock when no aircraft are visible (optional, portal setting)
- **Off-hours** schedule can dim the panel, turn it off, or force the clock screen at night

![Radar screen](docs/images/flightscnrpi.jpg)

![Flight detail](docs/images/flight-detail.jpg) · ![Tracked flight](docs/images/tracked.jpg)

![Clock and forecast](docs/images/clock.jpg) · ![Forecast](docs/images/forecast.jpg)

#### Radar features

- Animated radar sweep with configurable accent **color themes** (Red, Yellow, Orange, Green, White)
- Optional **compass rose** and **sweep line** (toggle in portal)
- **Map tile background** (CARTO dark or OpenStreetMap) with cached tiles in `/var/lib/flightscnr/maps/`
- **Aircraft-type icons** (jet, turboprop, helicopter, military, etc.) with altitude/speed tags
- **Minimum altitude floor** to hide low aircraft (e.g. pattern traffic)
- **Alert mode**: highlight military aircraft, emergency squawks (7700/7600/7500), or a custom watch list; optionally hide non-alerted traffic
- Distance units: **km**, **statute miles**, or **nautical miles**

#### Tracked flight

Pick any callsign in the web portal. The display shows origin → destination, aircraft type, a **progress bar** with a moving plane icon, and live stats (time remaining, distance, vertical speed). Flights not yet airborne can use **AirLabs** schedule data when configured.

---

### Web portal

Open from any device on your LAN:

**`http://<hostname>.local`** (default port **80**; change with `WEB_PORT` in `/etc/flightscnr.env`)

| Section | Purpose |
|---------|---------|
| **Radar** | Set radar center (lat/lon), range, distance units, min altitude, color theme, compass, sweep |
| **Display & screens** | Brightness, flight-detail and clock timeouts, auto-return to clock when empty |
| **Off-hours** | Night schedule - dim, turn off display, or show clock |
| **Weather** | °C / °F for clock and forecast |
| **Alerts** | Military, emergency squawk, watch list, hide non-alerted aircraft |
| **Tracking** | Track a callsign; **route search** (origin + destination) for live flights |
| **API keys** | FR24, Tomorrow.io, AirLabs - save or save & restart |
| **Updates** | Check GitHub for new releases; **Update Now** runs `git pull` and re-syncs (git checkout required) |
| **System** | **Reboot** or **Shutdown** the Pi remotely |

**Additional web pages**

| URL | Purpose |
|-----|---------|
| `/stats` | Daily overhead flight counts and charts |
| `/closest`, `/farthest` | Maps and logs for closest / farthest aircraft seen |
| `/counter` | Raw flight counter JSON |

<img width="1190" height="721" alt="image" src="https://github.com/user-attachments/assets/3e4f4f7b-a0c9-41e6-9798-cca13743c8e4" />

Portal preferences are stored on the Pi in `/var/lib/flightscnr/` and apply without wiping on update.

---

### Data sources and modes

| Source | Required? | Provides |
|--------|-----------|----------|
| **FR24 API** | Yes (full app) | Routes, airlines, flight details, tracked flights, enriched radar |
| **[adsb.fi](https://adsb.fi)** | Optional (on by default) | Free live positions over the internet — merges with FR24 or fills the radar when FR24 is off (`ADSB_ENABLED=True`). **Not** a USB ADS-B receiver. |
| **Tomorrow.io** | Yes (weather) | Clock temperature and multi-day forecast |
| **AirLabs** | Optional | Scheduled departure info when a tracked flight is not yet airborne |

API responses are **cached** (e.g. FR24 feed ~90s, flight details ~30 min, weather ~1 hr) to reduce quota use during 24/7 operation. Offline databases (`airports.json`, `airlines.json`, `icao_types.json`) download on first run.

---

## Hardware

![Hardware assembly](docs/images/assembly.jpg)

### Bill of materials

| Qty | Item | Notes | Link |
|-----|------|-------|------|
| 1 | **Raspberry Pi 4 Model B** (2 GB or 4 GB RAM) | Tested on Pi 4; Pi 3B+/Pi 5 also work with the Waveshare panel. Needs desktop OS for pygame/X11. | [raspberrypi.com](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) |
| 1 | **Waveshare 4″ DSI LCD (C)** | 720x720 round IPS, **10-point capacitive** touch, DSI + I2C. Part of Waveshare “4inch DSI LCD (C)” line - not the 480x800 or HDMI round variants. | [Waveshare Link](https://www.waveshare.com/4inch-dsi-lcd-c.htm?&aff_id=108718)|
| 1 | **microSD card** (32 GB+, A2 recommended) | Flash **Raspberry Pi OS (64-bit) with desktop**. |
| 1 | **USB-C power supply** (5 V, **3 A** minimum) | Official Pi 4 PSU or equivalent. Budget headroom for the DSI panel. |
| 1 | **Network** | Wi‑Fi (built in) or Ethernet for flight data, maps, weather, and the web portal. |
| 1 | **Case / stand** | 3D-printed desktop enclosure. | [MakerWorld Link]()|
| 0–1 | **Heatsink + fan** (optional) | Recommended for 24/7 operation on Pi 4. | [Waveshare Link](https://www.waveshare.com/pi4-fan-pwm.htm?&aff_id=108718) |

---

### Assembly

#### 1. Flash Raspberry Pi OS

1. Download **Raspberry Pi OS (64-bit)** with desktop from [raspberrypi.com/software](https://www.raspberrypi.com/software/).
2. Flash to the microSD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
3. In Imager **OS customisation** (recommended): set hostname, enable SSH, configure Wi‑Fi, and set your locale/time zone.
4. Insert the card and boot once without the display attached to confirm the Pi boots and you are able to ping it.

#### 2. Mount the display

1. **Power off** the Pi and disconnect USB-C power.
2. Secure the display to the Pi with the **included standoffs and screws**.
3. Connect the **DSI ribbon cable** between the round display board and the Pi’s **DSI port** (on Pi 4, the DSI connector above the micro SD card).
4. If your board has an **I2C address DIP switch**, leave it in the factory position.

#### 3. Enable the panel in firmware

On the Pi, edit the boot **`config.txt`** (not in the FlightScnr repo):

| Raspberry Pi OS | Path |
|-----------------|------|
| Older releases | `/boot/config.txt` |

Add these lines:

```ini
dtoverlay=vc4-kms-v3d
#DSI1 Use
dtoverlay=vc4-kms-dsi-waveshare-panel,4_0_inchC
#DSI0 Use
#dtoverlay=vc4-kms-dsi-waveshare-panel,4_0_inchC,dsi0
```

On **Pi 4**, use the **DSI1** line as shown. On **Pi 5 / Compute Module**, if the screen stays blank, comment out the DSI1 overlay and uncomment the **DSI0** line instead.

Reboot and confirm the desktop fills the round panel and touch works:

```bash
sudo reboot
```

#### 4. Install FlightScnr Pi

```bash
git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
cd ~/FlightScnr_Pi
sudo bash install-pi.sh
```

This installs system packages, creates `flightscnr-venv/`, downloads UI assets (fonts, weather icons, aircraft icons), extracts airline logos from `logo.zip`, creates `config.h` from `config.h.example`, creates `/var/lib/flightscnr/`, writes `/etc/flightscnr.env`, and registers the `flightscnr` systemd service.

#### 5. Verify

| Check | How |
|-------|-----|
| Radar fills the circle | Display should show the radar UI on boot |
| Touch | Tap an aircraft → flight detail; swipe down → clock |
| Pinch zoom | Two-finger pinch on radar changes range |
| Web portal | Open `http://raspberrypi.local` from another device on the LAN |
| Logs | `sudo journalctl -u flightscnr -f` |

#### 6. Configure

**Easiest:** open the web portal → **API Keys** → enter `FR24_API_KEY` and `TOMORROW_API_KEY` → **Save & restart**.

**Or edit `config.h`** in the project folder:

```bash
nano ~/FlightScnr_Pi/config.h
sudo systemctl restart flightscnr
```

Without `FR24_API_KEY`, the app still runs using **adsb.fi only** (radar positions and basic tags — no routes or rich detail screens). Set `ADSB_ENABLED=True` and your home location. See `config.h.example` and `.env.example` for all options.

## Updates

### From the web portal (recommended)

If the Pi was installed from a **git clone**, open **Updates** in the portal → **Check Updates** → **Update Now**. That runs `git pull`, refreshes Python dependencies, re-syncs assets, and restarts the service. Settings and API keys are preserved.

---

## Credits

- Parts of this repo are based on code by [c0wsaysmoo](https://github.com/c0wsaysmoo), used with their prior written permission. Thank you!

## License

### Firmware

Original application code, tools, and documentation in this repository are licensed under **[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)** ([LICENSE](LICENSE)).

- **Attribution:** credit the author and link to the license when you share or adapt this work.
- **NonCommercial:** you may not use this material for commercial purposes without separate permission.
- **ShareAlike:** adaptations must be released under the same license.

### Enclosure license

The 3D-printed enclosure is **not** part of this firmware repository. Its digital files and physical prints are governed by the license shown on the linked **MakerWorld** model page. That content is published under a **Standard Digital File License**, which includes terms such as:

> This user content is licensed under a Standard Digital File License.  
> You shall not share, sub-license, sell, rent, host, transfer, or distribute in any way the digital or 3D printed versions of this object, nor any other derivative work of this object in its digital or physical format (including - but not limited to - remixes of this object, and hosting on other digital platforms). The objects may not be used without permission in any way whatsoever in which you charge money, or collect fees.

Always read the full license on MakerWorld before downloading, printing, or sharing the enclosure design.

<p align="center">
  <a href="https://buymeacoffee.com/yashmulgaonkar" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" style="height: 35px;">
  </a>
</p>
