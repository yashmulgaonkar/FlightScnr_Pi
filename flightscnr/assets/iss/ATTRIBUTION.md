# ISS screen sources

The world chart is the same tile basemap as the tracked-flight route map.
The ground track, the position readout, and the next pass are computed in
FlightScnr from the public station feeds. No API key.

- Position: https://api.wheretheiss.at/v1/satellites/25544, then
  http://api.open-notify.org/iss-now.json if that feed fails.
- Crew: https://corquaid.github.io/international-space-station-APIs/JSON/people-in-space.json
- Station marker: `assets/iss.png`, tinted to the display aircraft color
  (the yellow blip).
- HUD label colors follow filbot/iss-tracker
  (https://github.com/filbot/iss-tracker).
