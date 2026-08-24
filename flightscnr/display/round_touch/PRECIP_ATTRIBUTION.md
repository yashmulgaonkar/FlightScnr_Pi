# Precipitation radar overlay

Live rain/snow tiles for the circular radar map (see `rainviewer_overlay.py`).
Providers are tried in order:

## LibreWXR (primary)

- Project: [LibreWXR](https://librewxr.net/) by [Joshua Kimsey](https://github.com/JoshuaKimsey)
- Source: [github.com/JoshuaKimsey/LibreWXR](https://github.com/JoshuaKimsey/LibreWXR)
- Public API used here: [api.librewxr.net](https://api.librewxr.net/)

**API data** from the public LibreWXR instance is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Credit **LibreWXR** when using or redistributing that data.

The LibreWXR *software* is AGPL-3.0-or-later; this project only consumes the
public HTTP API and does not ship or host LibreWXR itself.

Upstream data-licensing details (including the Italy / Radar-DPC CC-BY-SA
carve-out) are documented on [librewxr.net](https://librewxr.net/) under
Data Licensing.

## RainViewer (fallback)

- [RainViewer Weather Maps API](https://www.rainviewer.com/api.html)
- Used when LibreWXR metadata or tiles are unavailable

Credit **RainViewer** when that path is active.

## On-device credit

When precipitation is shown, the map corner attribution reads `© LibreWXR` or
`© RainViewer` for the active provider.
