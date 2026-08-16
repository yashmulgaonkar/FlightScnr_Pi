# FlightScnr Pi

A [round **4″ touch display**](https://www.waveshare.com/4inch-dsi-lcd-c.htm?&aff_id=108718) flight and marine tracker for Raspberry Pi. Dark radar UI, animated sweep, map tiles, gesture navigation, LiveATC audio, and a local **web portal** for setup — no SSH required for day-to-day use. Modeled after [FlightScnr](https://github.com/yashmulgaonkar/FlightScnr).

![FlightScnr Pi on a round display](docs/images/flightscnrpi.jpg)

<p align="center">
  <a href="https://buymeacoffee.com/yashmulgaonkar" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" style="height: 35px;">
  </a>
</p>

---

## Features

Live aircraft (and optional marine traffic) on a circular radar, with rich detail screens when you tap. Powered by **FR24**, **[adsb.fi](https://adsb.fi)**, optional local dump1090/readsb, **Tomorrow.io** weather, and optional route enrichment. Configure everything from the web portal. Full detail: [Features wiki](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Features).

### Screens

Radar home, flight detail, tracked flight with route map, and clock / weather — swipe between them on the 720×720 round touch display.

<table>
<tr>
<td align="center" width="50%">

![Radar screen](docs/images/features/IMG_3336_hero.jpg)

**Radar**

</td>
<td align="center" width="50%">

![Flight detail](docs/images/features/IMG_3375_flightdetails.jpg)

**Flight detail**

</td>
</tr>
<tr>
<td align="center" width="50%">

![Tracked flight](docs/images/features/IMG_3377_track_flight.jpg)

**Tracked flight**

</td>
<td align="center" width="50%">

![Clock and weather](docs/images/features/IMG_3360_weather.jpg)

**Clock & weather**

</td>
</tr>
</table>

### Map layers

Dark and Voyager CARTO street maps, plus free FAA VFR sectionals (US). Light CARTO is also available on-device.

<table>
<tr>
<td align="center" width="50%">

![CARTO dark map](docs/images/features/IMG_3367_carto_darkmap.jpg)

**Dark**

</td>
<td align="center" width="50%">

![Voyager map](docs/images/features/IMG_3401_voyager_map.jpg)

**Voyager**

</td>
</tr>
<tr>
<td align="center" width="50%">

![FAA VFR sectional](docs/images/features/IMG_3411_VFR_map.jpg)

**VFR**

</td>
<td width="50%"></td>
</tr>
</table>

### Radar clock HUD

Optional frosted HUD on the radar: time, weather, wind, and US AQI. Light or dark pill, adjustable opacity, and per-channel audio controls (chime, tracked, military, ATC).

<table>
<tr>
<td align="center" width="50%">

![HUD light mode](docs/images/features/IMG_3388_HUD_lightmode.jpg)

**HUD light**

</td>
<td align="center" width="50%">

![HUD dark mode](docs/images/features/IMG_3387_HUD_darkmode.jpg)

**HUD dark**

</td>
</tr>
<tr>
<td align="center" width="50%">

![HUD audio / detail controls](docs/images/features/HUD_details.jpg)

**HUD audio controls**

</td>
<td width="50%"></td>
</tr>
</table>

### Aircraft photos & marine AIS

Flight detail can show aircraft photos ([planespotters.net](https://www.planespotters.net/) / Wikimedia). Optional marine AIS from [aisstream.io](https://aisstream.io/) puts vessels on the same radar, with ship photos from Wikimedia Commons.

<table>
<tr>
<td align="center" width="50%">

![Marine AIS traffic on radar](docs/images/features/IMG_3391_marinetraffic.jpg)

**Marine traffic**

</td>
<td align="center" width="50%">

![Marine vessel photo](docs/images/features/IMG_3393_marinedetails.jpg)

**Vessel detail**

</td>
</tr>
<tr>
<td align="center" width="50%">

![Marine vessel photo](docs/images/features/IMG_3396_marinedetails2.jpg)

**Vessel detail**

</td>
<td width="50%"></td>
</tr>
</table>

### ATC audio

Optional **LiveATC** streams to a USB or Bluetooth speaker — pick airport and channel on-device or in the portal.

<table>
<tr>
<td align="center" width="50%">

![ATC settings](docs/images/features/IMG_3385_ATC_menu.jpg)

**ATC settings**

</td>
<td align="center" width="50%">

![ATC channel picker](docs/images/features/IMG_3386_ATC_menu2.jpg)

**Channel picker**

</td>
</tr>
</table>

Also included: wildfire markers (CAL FIRE / NIFC / NASA FIRMS), global earthquake epicenters (USGS), alert mode, facing / orientation, favorite locations, and a boot safety disclaimer. See the [Features wiki](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Features) for the full list.

---

## Documentation

**Full guides live in the [Wiki](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki).** Start there for build, install, features, and troubleshooting.

| Topic | Wiki page |
| ----- | --------- |
| Screens, gestures, radar, marine, ATC, wildfires, earthquakes | [Features](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Features) |
| Bill of materials | [Hardware](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Hardware) |
| Physical assembly | [Hardware Assembly](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Hardware-Assembly) |
| OS, display overlay, install, Wi‑Fi, config | [Software Setup](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Software-Setup) |
| Portal sections and settings | [Web Portal](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Web-Portal) |
| FR24, adsb.fi, weather, AIS, and more | [Data Sources](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Data-Sources) |
| Touch, pinch-zoom, common fixes | [Troubleshooting](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Troubleshooting) |
| Updating from the portal | [Updates](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Updates) |
| Credits and license details | [Credits and License](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Credits-and-License) |

**Upgrading from older builds:** one **Update Now** is enough. If an OTA pulled a newer installer but could not run it (pre-re-exec path), the device **auto-finishes** install steps after restart — or use **Finish install** in the portal. If LightDM is switched to X11 for pinch-zoom, the Pi **reboots automatically**.

**Stuck on `2026.8.5.x` (Update fails silently):** an older install step flipped permissions on `scripts/release.sh`, which used to block the update pull. That file is now frozen upstream, so pressing **Update Now** once more in the portal should work — no terminal needed. If it still fails (other local edits, corrupted git store), run on the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/yashmulgaonkar/FlightScnr_Pi/main/scripts/repair-ota.sh | bash
```

Use `| bash -s -- --hard` only if other local edits also block the pull.

---

## Quick install

1. Gather parts and assemble the unit — see [Hardware](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Hardware) and [Hardware Assembly](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Hardware-Assembly).
2. Flash Raspberry Pi OS (64-bit, with desktop), enable the Waveshare panel overlay, then:

```bash
git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
cd ~/FlightScnr_Pi
sudo bash install-pi.sh
```

The installer forces the desktop to **X11** (needed for pinch-to-zoom) and **reboots automatically** when that switch is pending. It also enables the enclosure cooling fan via the kernel `gpio-fan` overlay (**GPIO 14**, on at **60°C**).

3. Open the web portal at `http://<hostname>.local` and add API keys.

Step-by-step instructions: [Software Setup](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Software-Setup).

---

## Contributing

Contributions are welcome. If you find a bug, have an idea, or want to improve the project, open a [pull request](https://github.com/yashmulgaonkar/FlightScnr_Pi/pulls). For larger changes, opening an [issue](https://github.com/yashmulgaonkar/FlightScnr_Pi/issues) first is helpful so we can discuss the approach.

Join the [FlightScnrPi community on Discord](https://discord.gg/wjqgUjv8Re) for questions or troubleshooting help.

---

## Credits

- Parts of this repo are based on code by [c0wsaysmoo](https://github.com/c0wsaysmoo), used with their prior written permission. Thank you!
- AIS WebSocket client design adapted from [capsule-radar-ais](https://github.com/socquique/capsule-radar-ais) (MIT).
- Aircraft photos courtesy of [planespotters.net](https://www.planespotters.net/) contributors (when credited on screen).
- Vessel photos from [Wikimedia Commons](https://commons.wikimedia.org/) contributors under their respective licenses.

Full asset attributions: [Credits and License](https://github.com/yashmulgaonkar/FlightScnr_Pi/wiki/Credits-and-License).

---

## License

### Firmware

Original application code, tools, and documentation in this repository are licensed under **[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-nc-sa/4.0/)** ([LICENSE](LICENSE)). Required attribution text is in [NOTICE](NOTICE).

- **Attribution:** credit the author (Yash Mulgaonkar), link to https://github.com/yashmulgaonkar/FlightScnr_Pi and the license when you share or adapt this work.
- **NonCommercial:** you may not use this material for commercial purposes without separate permission.
- **ShareAlike:** adaptations must be released under the same license.

First-party source files include a copyright / SPDX / `[AI-DIRECTIVE]` header. Do not remove those headers. AI coding agents and forks should follow [AGENTS.md](AGENTS.md) (and `.cursor/rules/license-attribution.mdc`) so attribution and license terms stay intact.

### Enclosure license

The 3D-printed enclosure is **not** part of this firmware repository. Its digital files and physical prints are governed by the license shown on the MakerWorld model page. There are two print profiles on the same model:

- [Enclosure without speaker](https://makerworld.com/en/models/3024952-flightscnrpi-large-ads-b-traffic-sweeping-radar#profileId-3399104)
- [Enclosure with speaker](https://makerworld.com/en/models/3024952-flightscnrpi-large-ads-b-traffic-sweeping-radar#profileId-3532792)

That content is published under a **Standard Digital File License**, which includes terms such as:

> This user content is licensed under a Standard Digital File License.  
> You shall not share, sub-license, sell, rent, host, transfer, or distribute in any way the digital or 3D printed versions of this object, nor any other derivative work of this object in its digital or physical format (including - but not limited to - remixes of this object, and hosting on other digital platforms). The objects may not be used without permission in any way whatsoever in which you charge money, or collect fees.

Always read the full license on MakerWorld before downloading, printing, or sharing the enclosure design.
