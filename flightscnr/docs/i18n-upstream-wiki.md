# Proposed upstream wiki updates for internationalisation

This file is the hand-off checklist and draft copy for the separate FlightScnr
Pi GitHub wiki. It does not publish or edit the upstream wiki. Apply it only
after the feature pull request is approved and the translations have completed
native-speaker review.

## Features

Add a **Language and region** paragraph:

> FlightScnr Pi can display its clock, dates, forecast, Tomorrow.io weather
> descriptions, settings menu, and web portal in English, Dutch, German,
> French, or Spanish. English is the default and safe per-message fallback.
> On the round display, open **Settings → Display** to choose **Language** and
> **Date Order**. Language does not change the existing 12/24-hour clock,
> temperature, distance, or speed settings.

Evidence to attach: 720×720 screenshots of the language picker, clock, and
forecast for every released language, including the longest labels.

## Web Portal

Add to the settings overview:

> **Language & Region** selects the display language and US or European date
> order and shows a localized date preview. Saving redraws the display and
> portal without requesting new weather data. Provider names, airport codes,
> route-source identifiers, units, and user-entered values are not translated.

Clarify that clock format and measurement units remain in their existing
sections and are never inferred from the chosen language.

Evidence to attach: desktop and phone-width screenshots, a save/reload
round-trip, a regional tag such as `fr-FR`, and an unknown-language fallback.

## Software Setup

Add an optional system-language subsection:

> English is used when `display_language` is missing, empty, invalid, or has no
> installed pack. **System** is opt-in. It checks
> `FLIGHTSCNR_SYSTEM_LOCALE`, then the host's `/etc/default/locale` and the
> standard message-locale variables `LC_ALL`, `LC_MESSAGES`, and `LANG`.
> `LC_TIME` does not choose UI language because date order and clock format are
> independent settings. FlightScnr does not call process-global
> `locale.setlocale()`, and the systemd service may continue to run under
> `C.UTF-8`.

## Troubleshooting

Add:

> If text remains English, first confirm that the selected language appears in
> **Language & Region**. A missing message, invalid placeholder, empty value,
> incompatible revision, unsafe control character, or damaged pack falls back
> safely to English. A structurally invalid pack is skipped as a whole and a
> failed runtime refresh retains the last-known-good catalog. Check the
> FlightScnr service log for `Skipping language pack` or `Catalog refresh
> rejected`.

> Language changes are live, but newly installed catalog files require a
> normal FlightScnr service restart. Do not place unreviewed language packs in
> the firmware checkout.

## Updates

Add:

> Language packs are versioned firmware files delivered through the normal
> FlightScnr update process; the portal has no pack-upload endpoint. Settings
> preserve `display_language` and date order across upgrades. Legacy persisted
> weather snapshots are migrated locally to the semantic cache schema without
> making an extra Tomorrow.io request. Rollback follows the same repository and
> service restart behavior as the rest of the firmware.

Required OTA evidence: upgrade from `2026.8.31.1`, preserve a legacy
`weather_cache.json`, retain language/date settings during interleaved portal
and display saves, and confirm that `scripts/release.sh` is byte-identical.

## Credits and License

Add the released language contributors only after review:

> Translation catalogs are adaptations of FlightScnr Pi by Yash Mulgaonkar,
> <https://github.com/yashmulgaonkar/FlightScnr_Pi>, licensed under
> CC BY-NC-SA 4.0. Each shipped `manifest.json` identifies its translation
> contributors and native reviewer. Commercial use remains prohibited without
> separate permission from the original author.

Do not present machine-assisted draft attribution as native review. The pull
request must name the native reviewer for Dutch, German, French, and Spanish
and include their corrections or approval evidence.

## Pull-request evidence checklist

- Branch based on current upstream `main`, with conflict resolutions listed.
- Full current pytest suite plus the focused i18n, settings, portal, weather
  cache, and cross-process tests.
- Pi 4 validation on the actual round 720×720 display; no production data path
  is used by tests.
- Portal screenshots at desktop and phone width for each released language.
- English template/catalog equivalence and protected-identifier tests.
- Broken, incomplete, old, newer, empty, duplicate-key, and unsafe-control
  catalog tests.
- System-locale matrix and proof that a language switch performs no
  Tomorrow.io call.
- Named native reviewer and manifest attribution for every non-English pack.
- Confirmation that `LICENSE`, `NOTICE`, `VERSION`, the mandatory boot safety
  disclaimer, and frozen `scripts/release.sh` are unchanged by the feature.
