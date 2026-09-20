# Lofi bed starter playlist

The starter tracks are original compositions by Reuben Thiessen
(CC BY-NC-SA 4.0, like the rest of the repository). They are too heavy
to live in git, so they ship as a zip on **GitHub Releases** and install
from the web portal: **LoFi Beats → Download starter playlist**.

## Pack catalog

[`pack.json`](pack.json) lists the available release pack(s). Today there
is one entry (`lofi-pack-v1`). The portal reads label, size, and download
URL from this file; the zip itself stays on GitHub Releases so device
`git pull` OTAs stay light.

Tracks land in `/var/lib/flightscnr/lofi-pack/` and behave like
built-ins (play/disable, no remove).

## Overrides

- `LOFI_PACK_URL` — point at a different release zip (forks, mirrors)
- `LOFI_PACK_ID` — which catalog entry to use when multiple exist later

See `.env.example`.

Add your own MP3s on the device in `/var/lib/flightscnr/lofi/` — they
join the playlist automatically.
