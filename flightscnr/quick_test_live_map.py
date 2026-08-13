# SPDX-License-Identifier: CC-BY-NC-SA-4.0
# Copyright (c) 2026 Yash Mulgaonkar - https://github.com/yashmulgaonkar/FlightScnr_Pi

"""quick_test_live_map.py — interaktiver, manueller Test für
display/round_touch/live_map.py. Braucht einen echten Display/Internet-
Zugriff (Kartenkacheln) und ist deshalb bewusst KEIN automatisierter
unittest — siehe tests/test_live_map.py für die headless/CI-taugliche
Variante ohne Netzwerk.

Ausführen (von überall im Repo aus funktioniert der sys.path-Fix unten):

    python3 tests/quick_test_live_map.py
    # oder, aus dem tests/-Ordner selbst:
    cd tests && python3 quick_test_live_map.py

Steuerung:
    Pfeiltasten       -> Flugzeug "fliegen" lassen (lat/lon simuliert bewegen)
    W / S             -> simulierte Geschwindigkeit (kt) hoch/runter
                         -> der angezeigte Radius wird daraus **berechnet**,
                            nicht direkt gesetzt (position_source.compute_tracking_radius_km)
    [ / ]             -> Heading drehen (nur Flugzeug-Icon, Karte bleibt nord-oben)
    h                 -> Heading-up an/aus (rotiert die ganze Karte)
    r                 -> Basemap-Cache invalidieren (live_map.invalidate())
    f                 -> Vollbild an/aus umschalten
    ESC / Fenster zu  -> Beenden

Was du beobachten solltest:
    - Fenster öffnet jetzt über video.init_display() -- dieselbe Funktion,
      die auch die echte App zum Fenster-Öffnen benutzt (inkl. FULLSCREEN-
      Flag und SDL-Treiber-Fallbacks). Vorher wurde nur ein normales,
      unskaliertes pygame-Fenster ohne FULLSCREEN geöffnet, was auf einem
      Pi je nach Konfiguration nicht bildschirmfüllend war.
    - Erste 1-2 Sekunden: Karte ggf. noch grau (Tiles laden asynchron).
    - Karte sollte jetzt deutlich schärfer sein als vorher -- Zoom wird
      pro Radius passend gewählt (vorher hart auf Zoom 7 gedeckelt, siehe
      live_map._pick_zoom_for_live_map).
    - Flugzeug-Icon bleibt JETZT IMMER exakt in der Bildschirmmitte, auch
      während du kontinuierlich in eine Richtung fliegst -- die Karte
      wandert darunter, das Icon bewegt sich nicht mehr sichtbar (das war
      der Kern-Bugfix: vorher driftete es innerhalb der gecachten Kachel
      und sprang erst bei einem Refetch zurück zur Mitte).
    - W/S: Bei hoher simulierter Geschwindigkeit wird der Radius größer
      (bis zum 48km-Maximum), bei niedriger/0 kt auf das 8km-Minimum --
      das ist dieselbe compute_tracking_radius_km()-Funktion, die auch im
      echten Backend (utilities/position_source.py) für den Live-Fallback
      benutzt wird -- kein separat nachgebauter Wert.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from display.round_touch import theme, live_map, video  # noqa: E402
from utilities.position_source import compute_tracking_radius_km  # noqa: E402

# --- Startwerte: ersetze durch echte, aktuelle Koordinaten eines
#     Flugzeugs, das du gerade auf deinem Radar siehst, wenn du reale
#     Tile-Ausschnitte um einen bekannten Ort sehen willst. ---
lat = 53.6304
lon = 9.9882
heading = 90.0
speed_kt = 228.0  # ~422 kph, wie im Tracking-Screenshot aus der Konversation
heading_up = False

TEST_FLIGHT = {
    "callsign": "DLH123",
    "plane": "A320",
    "icao_hex": "3C1234",
}

STEP_DEG = 0.0015
SPEED_STEP_KT = 20.0


def main() -> None:
    global lat, lon, heading, speed_kt, heading_up

    try:
        from config import DISPLAY_FULLSCREEN

        fullscreen = DISPLAY_FULLSCREEN
    except ImportError:
        fullscreen = os.environ.get("DISPLAY_FULLSCREEN", "true").lower() in ("1", "true", "yes")

    # Gleicher Weg wie RoundTouchDisplay.__init__ in display/round_touch/app.py:
    # video.init_display() probiert mehrere SDL-Treiber durch und setzt bei
    # Bedarf FULLSCREEN -- ein nacktes pygame.display.set_mode(...) (wie in
    # der vorherigen Version dieses Skripts) tut das NICHT von selbst.
    display_surface = video.init_display(theme.SIZE, theme.SIZE, fullscreen)
    fit_side = min(display_surface.get_size())
    if fit_side != theme.SIZE:
        theme.set_framebuffer_side(fit_side)
        from display.round_touch import map_bg

        map_bg.invalidate()
        if display_surface.get_size() != (fit_side, fit_side):
            pygame.display.quit()
            display_surface = video.init_display(fit_side, fit_side, fullscreen)

    pygame.display.set_caption("live_map.py quick test")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, max(14, theme.s(16)))

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    live_map.invalidate()
                    print("[test] cache invalidated")
                elif event.key == pygame.K_h:
                    heading_up = not heading_up
                    print(f"[test] heading_up = {heading_up}")
                elif event.key == pygame.K_f:
                    fullscreen = not fullscreen
                    pygame.display.quit()
                    display_surface = video.init_display(theme.SIZE, theme.SIZE, fullscreen)
                    print(f"[test] fullscreen = {fullscreen}")

        keys = pygame.key.get_pressed()
        if keys[pygame.K_UP]:
            lat += STEP_DEG
        if keys[pygame.K_DOWN]:
            lat -= STEP_DEG
        if keys[pygame.K_RIGHT]:
            lon += STEP_DEG
        if keys[pygame.K_LEFT]:
            lon -= STEP_DEG
        if keys[pygame.K_w]:
            speed_kt = min(speed_kt + SPEED_STEP_KT * 0.1, 600.0)
        if keys[pygame.K_s]:
            speed_kt = max(speed_kt - SPEED_STEP_KT * 0.1, 0.0)
        if keys[pygame.K_LEFTBRACKET]:
            heading = (heading - 1.5) % 360
        if keys[pygame.K_RIGHTBRACKET]:
            heading = (heading + 1.5) % 360

        # Kein manuell gesetzter Radius mehr -- exakt dieselbe Funktion wie
        # im echten Fallback-Code, damit dieser Test wirklich prüft, ob die
        # Geschwindigkeits->Radius-Kopplung so funktioniert wie im Backend.
        radius_km = compute_tracking_radius_km(speed_kt)

        display_surface.fill((6, 12, 18))

        if heading_up:
            # Gleiche Rotationslogik wie in display/round_touch/app.py's
            # _draw_tracked_live_map: north-up rendern, dann um -heading
            # drehen und auf die sichtbare Kreisfläche zurückschneiden.
            side = theme.VISIBLE_RADIUS * 2
            raw = live_map.render_live_tracking_map(
                lat=lat, lon=lon, heading=heading, radius_km=radius_km,
                width=side, height=side, flight=TEST_FLIGHT,
            )
            if raw is not None:
                rotated = pygame.transform.rotate(raw, -heading)
                rect = rotated.get_rect(center=(theme.CENTER_X, theme.CENTER_Y))
                mask = pygame.Surface((theme.SIZE, theme.SIZE), pygame.SRCALPHA)
                pygame.draw.circle(
                    mask, (255, 255, 255, 255),
                    (theme.CENTER_X, theme.CENTER_Y), theme.VISIBLE_RADIUS,
                )
                layer = pygame.Surface((theme.SIZE, theme.SIZE), pygame.SRCALPHA)
                layer.blit(rotated, rect)
                layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                display_surface.blit(layer, (0, 0))
        else:
            live_map.blit_live_tracking_map(
                display_surface,
                lat=lat, lon=lon, heading=heading, radius_km=radius_km,
                flight=TEST_FLIGHT,
            )

        hud_lines = [
            f"lat={lat:.5f} lon={lon:.5f}",
            f"speed={speed_kt:.0f}kt -> radius={radius_km:.1f}km (berechnet, nicht manuell)",
            f"heading={heading:.0f} deg  heading_up={heading_up}",
            f"viewport-cache-entries={len(getattr(live_map, '_viewport', {}))}",
            "Pfeiltasten=fliegen  w/s=Speed  [ ]=Heading  h=Heading-up  r=Cache-Reset  f=Vollbild",
        ]
        y = 6
        for line in hud_lines:
            img = font.render(line, True, (230, 230, 230))
            shadow = font.render(line, True, (0, 0, 0))
            display_surface.blit(shadow, (theme.s(7), y + 1))
            display_surface.blit(img, (theme.s(6), y))
            y += font.get_height() + 2

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
