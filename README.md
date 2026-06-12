# FlightScnr Pi

Round **1080×1080 touch display** flight tracker for Raspberry Pi. UI modeled after [FlightScnr](https://github.com/yashmulgaonkar/FlightScnr). Uses FR24 gRPC, ADS-B, weather APIs, and a built-in web portal.

**API keys:** `FR24_API_KEY` and `TOMORROW_API_KEY` are required for the full experience (flight details + clock weather). Without FR24, the radar can still show ADS-B positions only (`ADSB_ENABLED=True`).

**Quick setup:** `sudo bash install-pi.sh` (after clone)

---

## Hardware

- Raspberry Pi with desktop/X11 (tested on Pi 3/4 class boards)
- Round 1080×1080 touch LCD
- Network connection for flight data and map tiles

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
cd ~/FlightScnr_Pi
sudo bash install-pi.sh
```

This installs system packages, creates a virtualenv, extracts airline logos from `logo.zip`, creates `/var/lib/flightscnr/`, installs `/etc/flightscnr.env`, and registers the `flightscnr` systemd service.

### Updates (git pull)

After the initial install, updates are a single command:

```bash
bash ~/FlightScnr_Pi/install-pi.sh update
```

That runs `git pull --ff-only`, refreshes Python dependencies if `requirements.txt` changed, reinstalls the systemd unit if needed, and restarts the service.

Or manually:

```bash
cd ~/FlightScnr_Pi
git pull
sudo bash install-pi.sh
```

**What stays outside git** (safe across updates):

| Path | Purpose |
|------|---------|
| `/etc/flightscnr.env` | API keys and settings (never overwritten by install) |
| `/var/lib/flightscnr/` | Runtime data, maps, web portal state |
| `.venv/` | Python virtualenv (refreshed on install) |
| `logo/` | Extracted from `logo.zip` on install |
| `flightscnr/airlines.json` etc. | Downloaded on first app run |

### 2. Configure

Copy and edit environment settings:

```bash
cp .env.example .env
nano .env
```

On first install, `install-pi.sh` copies `.env` → `/etc/flightscnr.env` (if that file does not already exist). After that, edit production config there:

```bash
sudo nano /etc/flightscnr.env
sudo systemctl restart flightscnr
```

| Variable | Required? | What it does |
|----------|-----------|--------------|
| `FR24_API_KEY` | **Yes** (full app) | FR24 gRPC feed — routes, airlines, flight details, tracked flights |
| `TOMORROW_API_KEY` | **Yes** (clock weather) | Temperature on the clock screen |
| `HOME_LAT` / `HOME_LON` | **Yes** | Radar center and search zone |
| `AIRLABS_API_KEY` | Optional | Pre-departure schedule when a flight isn't airborne yet |
| `ADSB_ENABLED` | Default `True` | Free ADS-B positions; sole radar source if FR24 is missing |

Without `FR24_API_KEY`, the app still starts but only shows ADS-B aircraft (callsign, position, altitude — no routes or rich flight-detail screens). See `.env.example` for all options.

Display settings for the round panel:

```bash
DISPLAY_WIDTH=1080
DISPLAY_HEIGHT=1080
DISPLAY_FULLSCREEN=True
```

### 3. Run

```bash
sudo systemctl start flightscnr
sudo systemctl status flightscnr
sudo journalctl -u flightscnr -f
```

---

## Round touch UI

Visual design follows FlightScnr: dark green radar background, animated sweep, map tiles, amber aircraft icons, and altitude tags.

### Screens & navigation

| Screen | How to open | Gestures |
|--------|-------------|----------|
| **Radar** (home) | Boot → radar | Tap aircraft → flight detail; **tap range label (top)** → cycle zoom |
| **Clock** | Swipe down from radar | Swipe up → radar |
| **About** | Swipe up from radar | Swipe down → radar |
| **Settings** | Swipe left from radar | PREV/NEXT footer buttons between pages; tap rows on Display page |
| **Flight detail** | Tap aircraft on radar | PREV/NEXT or swipe to cycle flights; RADAR → back |
| **Tracked flight** | Web portal | RADAR footer → back |

Radar center can be set in `/etc/flightscnr.env` or from the web portal (saved to `/var/lib/flightscnr/location.json`).

---

## Web portal

Open from any device on the same LAN:

**`http://<hostname>.local`**

(e.g. `http://raspberrypi.local` — port 80 by default; set `WEB_PORT` in `/etc/flightscnr.env` to change.)

- Set radar center coordinates
- Track a specific flight (shown on the **Tracked** screen)
- View closest / farthest flight maps and logs
- **Flight Statistics** — daily overhead flight counts and charts

UI preferences (brightness, units, theme, min height) are stored on-device in `/var/lib/flightscnr/round_touch_settings.json`.

---

## Data & caching

Runtime data lives in `/var/lib/flightscnr/`:

| File | Purpose |
|------|---------|
| `location.json` | Radar center (web portal override) |
| `round_touch_settings.json` | Display settings |
| `flight_counter.json` | Flight statistics |
| `tracked_flight.json` | Web-selected tracked flight |
| `close.txt` / `farthest.txt` | Closest / farthest flight logs |
| `maps/` | Cached map tiles and generated maps |

Offline databases (`airports.json`, `airlines.json`, `icao_types.json`) download automatically on first run into `flightscnr/`.

API caching (FR24 feed ~90s, flight details ~30min, weather ~1hr) reduces quota usage during 24/7 operation.

---

## Configuration reference

All settings are environment variables — see `.env.example`. Production values go in `/etc/flightscnr.env`.

| Area | Examples |
|------|----------|
| API keys (required) | `FR24_API_KEY`, `TOMORROW_API_KEY` |
| API keys (optional) | `AIRLABS_API_KEY` |
| Location | `HOME_LAT`, `HOME_LON`, `SEARCH_RADIUS_NM` |
| Display | `DISPLAY_WIDTH`, `DISPLAY_HEIGHT`, `BRIGHTNESS`, `NIGHT_START` |
| Web | `WEB_PORT` (default `80`) |
| Data sources | `ADSB_ENABLED`, `MIN_HEIGHT` |

---

## Credits

- [FlightScnr](https://github.com/yashmulgaonkar/FlightScnr) — round radar UI design
- [Colin Waddell / its-a-plane-python](https://github.com/ColinWaddell/its-a-plane-python) — original flight tracker
- [c0wsaysmoo/plane-tracker-rgb-pi](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi) — RGB matrix fork and web portal foundation
- [ajplotkin/plane-tracker-rgb-pi](https://github.com/ajplotkin/plane-tracker-rgb-pi) — local airport/airline databases and pipeline improvements
