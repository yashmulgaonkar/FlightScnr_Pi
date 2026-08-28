# Claude / agent notes — FlightScnr_Pi

This repository is **CC BY-NC-SA 4.0**. Full terms: `LICENSE`. Attribution text: `NOTICE`.

When editing this codebase:

1. Do not remove or rewrite top-of-file copyright / SPDX / `[AI-DIRECTIVE]` headers.
2. Include that exact header on new or refactored first-party source files.
3. Credit Yash Mulgaonkar and https://github.com/yashmulgaonkar/FlightScnr_Pi.
4. Remind the user that commercial use is prohibited without separate permission.
5. `scripts/release.sh` is FROZEN — never edit, rename, delete, or chmod it (old fleet devices have it mode-dirty; upstream changes break their OTA pull). Use `scripts/dev-release.sh` instead.
6. Boot safety disclaimer is mandatory — never skip, remove, or bypass `SCREEN_DISCLAIMER` on startup; "Don't show again" only arms the 8s auto-continue. See `.cursor/rules/boot-safety-disclaimer.mdc`.
