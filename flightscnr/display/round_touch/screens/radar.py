"""Radar screen — FlightScnr-style sweep and aircraft markers."""

import math
import time

import pygame

from display.round_touch import aircraft, draw, geo, map_bg, rainviewer_overlay, scale, settings, theme
from display.round_touch import alert_prefs
from display.round_touch import vessel_declutter
from utilities import aircraft_alert
from utilities.overhead import load_tracked_callsign


_sweep_angle = 0.0
_sweep_last_ms = 0
_backdrop: pygame.Surface | None = None
_backdrop_key = None


def _init_sweep():
    global _sweep_angle, _sweep_last_ms, _backdrop, _backdrop_key
    _sweep_angle = 0.0
    _sweep_last_ms = time.time() * 1000
    _backdrop = None
    _backdrop_key = None


def tick_sweep():
    global _sweep_angle, _sweep_last_ms
    now = time.time() * 1000
    if _sweep_last_ms == 0:
        _sweep_last_ms = now
        return
    dt = now - _sweep_last_ms
    # Cap dt so a hitch doesn't jump the beam across the dial.
    if dt > 100:
        dt = 100
    _sweep_last_ms = now
    _sweep_angle = (_sweep_angle + 360.0 * dt / theme.SWEEP_PERIOD_MS) % 360.0


def _backdrop_cache_key(*, pan_mode: bool, calibrate: bool):
    if pan_mode or calibrate:
        return None
    facing = round(float(settings.effective_facing_deg() or 0.0), 1)
    bg = map_bg.get_background()
    overlay = rainviewer_overlay.get_overlay()
    return (
        theme.SIZE,
        scale.active_index(),
        facing,
        id(bg) if bg is not None else 0,
        id(overlay) if overlay is not None else 0,
        settings.show_compass_rose(),
        settings.show_range_rings(),
        settings.show_aircraft_tag(),
        settings.theme_index(),
        settings.theme_custom(),
        settings.theme_rgb(),
        settings.distance_units(),
        settings.map_style(),
        settings.vfr_map_opacity() if settings.map_style() == "vfr" else 0,
    )


def _ensure_backdrop(*, calibrate: bool, pan_mode: bool, pan_offset) -> pygame.Surface | None:
    """Cached map + precip + grid (no aircraft / sweep) for cheaper radar frames."""
    global _backdrop, _backdrop_key
    key = _backdrop_cache_key(pan_mode=pan_mode, calibrate=calibrate)
    if key is None:
        return None
    if _backdrop is not None and _backdrop_key == key and _backdrop.get_size() == (theme.SIZE, theme.SIZE):
        return _backdrop

    surf = pygame.Surface((theme.SIZE, theme.SIZE))
    draw.fill_background(surf)
    map_bg.draw_background(surf, pan_offset=None)
    rainviewer_overlay.draw_overlay(surf, pan_offset=None)
    _draw_grid(surf, calibrate=False)
    _backdrop = surf
    _backdrop_key = key
    return _backdrop


def draw_radar(
    surface,
    flights,
    full_redraw=True,
    *,
    calibrate: bool = False,
    pan_mode: bool = False,
    pan_offset: tuple[int, int] | None = None,
):
    alert_prefs.reload()
    offset = pan_offset if pan_mode else None
    backdrop = None if (pan_mode or calibrate) else _ensure_backdrop(
        calibrate=calibrate,
        pan_mode=pan_mode,
        pan_offset=offset,
    )
    if backdrop is not None:
        surface.blit(backdrop, (0, 0))
    else:
        draw.fill_background(surface)
        map_bg.request_background()
        map_bg.draw_background(surface, pan_offset=offset)
        rainviewer_overlay.request_overlay()
        rainviewer_overlay.draw_overlay(surface, pan_offset=offset)
        _draw_grid(surface, calibrate=calibrate or pan_mode)

    # Keep async map/precip fetch warm even when using the cached backdrop.
    map_bg.request_background()
    rainviewer_overlay.request_overlay()

    if pan_mode:
        _draw_map_pan_overlay(surface, pan_offset=offset)
    elif calibrate:
        _draw_facing_calibrate_overlay(surface)
    else:
        _draw_flights(surface, flights)
        if settings.show_sweep_line():
            sweep = (_sweep_angle - settings.effective_facing_deg()) % 360.0
            draw.draw_sweep_line(
                surface,
                sweep,
                theme.SWEEP,
                width=max(2, theme.s(2)),
                trail_color=theme.SWEEP_TRAIL,
            )
        if aircraft_alert.rim_flash_active():
            _draw_alert_rim_flash(surface)
        _draw_status(surface, flights)
        _draw_map_attribution(surface)


def _draw_grid(surface, *, calibrate: bool = False):
    center = (theme.CENTER_X, theme.CENTER_Y)
    line_w = max(1, theme.s(2))
    facing = settings.effective_facing_deg()
    if settings.show_range_rings():
        for ring in range(1, theme.RING_COUNT + 1):
            r = theme.GRID_OUTER_RADIUS * ring // theme.RING_COUNT
            draw.draw_dashed_circle(surface, center, r, theme.GRID, width=line_w)

        cx, cy = theme.CENTER_X, theme.CENTER_Y
        r = theme.GRID_OUTER_RADIUS
        # Crosshairs follow true N/S and E/W (rotate with facing).
        for bearing in (0, 90):
            rad = math.radians(bearing - facing - 90)
            dx = r * math.cos(rad)
            dy = r * math.sin(rad)
            draw.draw_dashed_line(
                surface,
                (cx - dx, cy - dy),
                (cx + dx, cy + dy),
                theme.CROSSHAIR,
                width=line_w,
            )

    cx, cy = theme.CENTER_X, theme.CENTER_Y
    if settings.show_compass_rose():
        font = draw.load_font(theme.FONT_CARDINAL, bold=True)
        # Place cardinals on the visible rim so they track true north.
        card_r = theme.VISIBLE_RADIUS - theme.CARDINAL_NORTH_OFFSET_Y
        for text, bearing in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            rad = math.radians(bearing - facing - 90)
            x = cx + int(card_r * math.cos(rad))
            y = cy + int(card_r * math.sin(rad))
            rendered = font.render(text, True, theme.GRID)
            surface.blit(rendered, rendered.get_rect(center=(x, y)))

        diag_r = theme.GRID_OUTER_RADIUS - theme.CARDINAL_DIAGONAL_INSET
        diag_font = draw.load_font(theme.FONT_CARDINAL_DIAG, bold=True)
        for label, angle in (("NE", 45), ("SE", 135), ("SW", 225), ("NW", 315)):
            rad = math.radians(angle - facing - 90)
            x = theme.CENTER_X + int(diag_r * math.cos(rad))
            y = theme.CENTER_Y + int(diag_r * math.sin(rad))
            rendered = diag_font.render(label, True, theme.GRID)
            rect = rendered.get_rect(center=(x, y))
            surface.blit(rendered, rect)

    # Range tags collide with calibrate help text — omit them in that mode.
    if calibrate or not settings.show_range_rings():
        return

    use_units = settings.distance_units()
    scale_font = draw.load_font(theme.FONT_TAG, bold=True)
    outer_km = scale.active_band()["label_km"]
    for ring in range(1, theme.RING_COUNT + 1):
        ring_km = outer_km * ring / theme.RING_COUNT
        label = scale.format_scale_tag(ring_km, use_units)
        r = theme.GRID_OUTER_RADIUS * ring // theme.RING_COUNT
        gap = theme.SCALE_GAP_OUTER_RING_KM if ring == theme.RING_COUNT and use_units == "km" else theme.SCALE_GAP_FROM_OUTER_RING
        label_r = r - gap
        rad = math.radians(theme.SCALE_LABEL_BEARING_DEG - facing - 90)
        x = theme.CENTER_X + int(label_r * math.cos(rad))
        y = theme.CENTER_Y + int(label_r * math.sin(rad))
        rendered = scale_font.render(label, True, theme.GRID)
        surface.blit(rendered, rendered.get_rect(center=(x, y)))


def _tag_block_metrics():
    """Return (block_height, row_offsets) for callsign + smaller type/alt lines."""
    main_font = draw.load_font(theme.FONT_TAG, bold=True)
    sub_font = draw.load_font(theme.FONT_TAG_SUB, bold=True)
    main_h = main_font.get_height()
    sub_h = sub_font.get_height()
    # Font metrics include extra leading; tuck rows to keep tags compact.
    tuck_main = theme.s(6)
    tuck_sub = theme.s(4)
    offsets = [0, main_h - tuck_main, main_h - tuck_main + sub_h - tuck_sub]
    block_h = offsets[-1] + sub_h
    return block_h, offsets, main_font, sub_font


def _above_min_height(flight) -> bool:
    if flight.get("kind") == "vessel":
        return vessel_declutter.should_show_on_radar(flight)
    try:
        from config import passes_altitude_filter
        return passes_altitude_filter(flight.get("altitude"))
    except ImportError:
        return True


def _draw_vessel_tag(surface, x, y, flight):
    """One-line vessel name (no MMSI, no type/speed when short tags are on)."""
    name = vessel_declutter.display_name(flight)
    if not name:
        return
    if vessel_declutter.short_tags_enabled():
        name = vessel_declutter.truncate_name(name, 14)
        font = draw.load_font(theme.FONT_TAG, bold=True)
        color = _overlay_color_for_basemap(theme.GRID)
        if vessel_declutter.hierarchy_enabled() and vessel_declutter.is_parked(flight):
            color = _overlay_color_for_basemap(theme.HINT)
        rendered = font.render(name, True, color)
        tag_on_right = x < theme.CENTER_X
        symbol_half = theme.AIRCRAFT_ICON_RADIUS
        if tag_on_right:
            anchor_x = min(
                x + symbol_half + theme.AIRCRAFT_LABEL_GAP,
                theme.CENTER_X + theme.VISIBLE_RADIUS - theme.s(20),
            )
            surface.blit(rendered, (anchor_x, y - rendered.get_height() // 2))
        else:
            anchor_x = max(
                x - symbol_half - theme.AIRCRAFT_LABEL_GAP,
                theme.CENTER_X - theme.VISIBLE_RADIUS + theme.s(20),
            )
            surface.blit(rendered, rendered.get_rect(midright=(anchor_x, y)))
        return

    # Legacy multi-line vessel tag (still never uses MMSI).
    block_h, offsets, main_font, sub_font = _tag_block_metrics()
    plane_type = flight.get("plane") or "Vessel"
    sog = flight.get("sog_kt")
    try:
        alt = f"{float(sog):.0f} kt" if sog is not None else (flight.get("nav_status_name") or "")
    except (TypeError, ValueError):
        alt = flight.get("nav_status_name") or ""
    ly = y - block_h // 2
    tag_on_right = x < theme.CENTER_X
    symbol_half = theme.AIRCRAFT_ICON_RADIUS
    if tag_on_right:
        anchor_x = min(
            x + symbol_half + theme.AIRCRAFT_LABEL_GAP,
            theme.CENTER_X + theme.VISIBLE_RADIUS - theme.s(20),
        )
        align = "left"
    else:
        anchor_x = max(
            x - symbol_half - theme.AIRCRAFT_LABEL_GAP,
            theme.CENTER_X - theme.VISIBLE_RADIUS + theme.s(20),
        )
        align = "right"
    lines = [
        (name, _overlay_color_for_basemap(theme.GRID), main_font, offsets[0]),
        (plane_type, _overlay_color_for_basemap(theme.TAG_TYPE), sub_font, offsets[1]),
        (alt, _overlay_color_for_basemap(theme.TAG_ALT_ASCEND), sub_font, offsets[2]),
    ]
    for text, color, font, row_y in lines:
        if not text:
            continue
        rendered = font.render(text, True, color)
        if align == "left":
            surface.blit(rendered, (anchor_x, ly + row_y))
        else:
            surface.blit(rendered, rendered.get_rect(topright=(anchor_x, ly + row_y)))


def _draw_aircraft_tag(surface, x, y, flight):
    if flight.get("kind") == "vessel":
        if not vessel_declutter.should_label(flight):
            return
        _draw_vessel_tag(surface, x, y, flight)
        return

    if settings.show_aircraft_tag():
        block_h, offsets, main_font, sub_font = _tag_block_metrics()
        try:
            from utilities.airline_branding import display_flight_id_for_flight
            callsign = display_flight_id_for_flight(flight)
        except ImportError:
            callsign = flight.get("callsign") or "—"
        plane_type = flight.get("plane") or ""
        alt = aircraft.format_altitude(flight.get("altitude"))
        alt_color = aircraft.altitude_tag_color(flight.get("vertical_speed"))

        ly = y - block_h // 2
        tag_on_right = x < theme.CENTER_X
        symbol_half = theme.AIRCRAFT_ICON_RADIUS

        if tag_on_right:
            anchor_x = min(x + symbol_half + theme.AIRCRAFT_LABEL_GAP, theme.CENTER_X + theme.VISIBLE_RADIUS - theme.s(20))
            align = "left"
        else:
            anchor_x = max(x - symbol_half - theme.AIRCRAFT_LABEL_GAP, theme.CENTER_X - theme.VISIBLE_RADIUS + theme.s(20))
            align = "right"

        lines = [
            (callsign, _overlay_color_for_basemap(theme.GRID), main_font, offsets[0]),
            (plane_type, _overlay_color_for_basemap(theme.TAG_TYPE), sub_font, offsets[1]),
            (alt, _overlay_color_for_basemap(alt_color), sub_font, offsets[2]),
        ]
        for i, (text, color, font, row_y) in enumerate(lines):
            if not text or text == "—" and i == 1:
                continue
            rendered = font.render(text, True, color)
            if align == "left":
                surface.blit(rendered, (anchor_x, ly + row_y))
            else:
                surface.blit(rendered, rendered.get_rect(topright=(anchor_x, ly + row_y)))


def _visible_flights(flights):
    visible = []
    max_km = geo.fetch_max_km()
    for f in flights:
        if not _above_min_height(f):
            continue
        lat = f.get("plane_latitude")
        lon = f.get("plane_longitude")
        if lat is None or lon is None:
            continue
        if geo.local_offset_km(lat, lon)[2] > max_km:
            continue
        visible.append(f)
    return visible


def _is_tracked(flight) -> bool:
    tracked = (load_tracked_callsign() or "").strip().upper()
    if not tracked:
        return False
    from utilities.aircraft_alert import flight_identity_keys

    tracked_keys = flight_identity_keys({"callsign": tracked, "registration": tracked})
    return bool(tracked_keys & flight_identity_keys(flight))


def _light_basemap() -> bool:
    """Pale street / VFR charts need a dedicated high-contrast overlay palette."""
    try:
        return settings.map_style() in ("light", "vfr")
    except Exception:
        return False


# Curated colors for busy pale charts (VFR / light CARTO) — not just darkened neon.
_LIGHT_MAP_ICON = (15, 23, 42)          # near-black slate (silhouette)
_LIGHT_MAP_TRACKED = (21, 128, 61)      # deep green
_LIGHT_MAP_CALLSIGN = (15, 23, 42)      # near-black
_LIGHT_MAP_TYPE = (30, 64, 175)         # indigo
_LIGHT_MAP_ALT_UP = (14, 116, 144)      # deep teal
_LIGHT_MAP_ALT_DOWN = (126, 34, 206)    # deep purple
_LIGHT_MAP_VESSEL_PARKED = (71, 85, 105)
_LIGHT_MAP_ALERT_MIL = (185, 28, 28)    # keep alerts punchy
_LIGHT_MAP_ALERT_OTHER = (29, 78, 216)
_LIGHT_MAP_HALO = (255, 255, 255)


def _overlay_color_for_basemap(color: tuple) -> tuple:
    """Map dark-radar accents to legible colors on light/VFR basemaps."""
    r, g, b = int(color[0]), int(color[1]), int(color[2])
    if not _light_basemap():
        return (r, g, b)
    key = (r, g, b)
    mapping = {
        tuple(theme.AIRCRAFT[:3]): _LIGHT_MAP_ICON,
        tuple(theme.VESSEL_MOVING[:3]): _LIGHT_MAP_ICON,
        tuple(theme.SWEEP[:3]): _LIGHT_MAP_TRACKED,
        tuple(theme.GRID[:3]): _LIGHT_MAP_CALLSIGN,
        tuple(theme.TAG_TYPE[:3]): _LIGHT_MAP_TYPE,
        tuple(theme.TAG_ALT_ASCEND[:3]): _LIGHT_MAP_ALT_UP,
        tuple(theme.TAG_ALT_DESCEND[:3]): _LIGHT_MAP_ALT_DOWN,
        tuple(theme.VESSEL_PARKED[:3]): _LIGHT_MAP_VESSEL_PARKED,
        tuple(theme.ALERT_MILITARY[:3]): _LIGHT_MAP_ALERT_MIL,
        tuple(theme.ALERT_OTHER[:3]): _LIGHT_MAP_ALERT_OTHER,
        tuple(theme.ALERT_FLASH[:3]): _LIGHT_MAP_ALERT_MIL,
        tuple(theme.ALERT_FLASH_OTHER[:3]): _LIGHT_MAP_ALERT_OTHER,
        tuple(theme.HINT[:3]): _LIGHT_MAP_VESSEL_PARKED,
    }
    if key in mapping:
        return mapping[key]
    # Unknown accent: pull toward near-black while keeping a hint of hue.
    return (
        max(12, int(r * 0.28)),
        max(12, int(g * 0.28)),
        max(12, int(b * 0.28)),
    )


def _draw_light_map_icon_halo(surface, x: int, y: int, *, compact: bool) -> None:
    """White soft disc behind icons so slate silhouettes read on busy charts."""
    if not _light_basemap():
        return
    r = theme.s(11) if compact else theme.s(15)
    halo = pygame.Surface((r * 2 + 2, r * 2 + 2), pygame.SRCALPHA)
    pygame.draw.circle(halo, (*_LIGHT_MAP_HALO, 210), (r + 1, r + 1), r)
    pygame.draw.circle(halo, (*_LIGHT_MAP_HALO, 90), (r + 1, r + 1), r + 1)
    surface.blit(halo, (int(x) - r - 1, int(y) - r - 1))


def _flight_icon_color(flight, *, compact: bool):
    if _is_tracked(flight) and not compact:
        return _overlay_color_for_basemap(theme.SWEEP)
    if aircraft_alert.is_highlighted(flight):
        # Pulse between alert color (red/blue) and normal aircraft yellow.
        if aircraft_alert.pulse_phase():
            return _overlay_color_for_basemap(aircraft_alert.alert_pulse_color(flight))
        return _overlay_color_for_basemap(aircraft_alert.alert_color(flight))
    if vessel_declutter.is_vessel(flight) and vessel_declutter.hierarchy_enabled():
        if vessel_declutter.is_parked(flight):
            return _overlay_color_for_basemap(theme.VESSEL_PARKED)
        return _overlay_color_for_basemap(theme.VESSEL_MOVING)
    return _overlay_color_for_basemap(theme.AIRCRAFT)


def _draw_flights(surface, flights):
    rim_items: list[tuple[float, dict, tuple[int, int]]] = []
    inner_items: list[tuple[float, dict, tuple[int, int]]] = []

    for flight in _visible_flights(flights):
        if not aircraft_alert.is_shown_on_radar(flight):
            continue
        lat = flight.get("plane_latitude")
        lon = flight.get("plane_longitude")
        if lat is None or lon is None:
            continue
        _, _, dist_km = geo.local_offset_km(lat, lon)
        if dist_km <= geo.inner_ring_max_km():
            x, y = geo.lat_lon_to_screen(lat, lon)
            inner_items.append((dist_km, flight, (x, y)))
        else:
            pos = geo.beyond_ring_position(lat, lon)
            if pos:
                rim_items.append((dist_km, flight, pos))

    # Draw parked vessels under moving ones when hierarchy is on.
    def _draw_order(item):
        dist_km, flight, _ = item
        parked = 1 if vessel_declutter.is_parked(flight) else 0
        return (parked, -dist_km)

    rim_items.sort(key=_draw_order)
    inner_items.sort(key=_draw_order)

    for _, flight, (x, y) in rim_items:
        _draw_light_map_icon_halo(surface, x, y, compact=True)
        aircraft.draw_plane_icon(
            surface,
            x,
            y,
            geo.screen_heading(flight.get("heading") or 0),
            _flight_icon_color(flight, compact=True),
            compact=True,
            flight=flight,
        )

    for _, flight, (x, y) in inner_items:
        heading = geo.screen_heading(flight.get("heading") or 0)
        color = _flight_icon_color(flight, compact=False)
        _draw_light_map_icon_halo(surface, x, y, compact=False)
        aircraft.draw_plane_icon(surface, x, y, heading, color, flight=flight)
        _draw_aircraft_tag(surface, x, y, flight)


def visible_in_range_count(flights) -> int:
    """In-range aircraft on radar (excludes rim blips), matching FlightScnr idle-clock logic."""
    count = 0
    for flight in _visible_flights(flights):
        if not aircraft_alert.is_shown_on_radar(flight):
            continue
        lat = flight.get("plane_latitude")
        lon = flight.get("plane_longitude")
        if lat is None or lon is None:
            continue
        if geo.local_offset_km(lat, lon)[2] <= geo.inner_ring_max_km():
            count += 1
    return count


def _draw_status(surface, flights):
    try:
        from config import location_configured, location_status
    except ImportError:
        location_configured = lambda: False
        location_status = lambda: ""

    visible = _visible_flights(flights)
    if visible:
        return

    font = draw.load_font(theme.FONT_DETAIL)
    y = theme.CENTER_Y - int(theme.VISIBLE_RADIUS * 0.62)

    if not location_configured():
        lines = [
            "Set radar center on web portal",
            "in /etc/flightscnr.env",
        ]
        color = theme.TAG_ALT_DESCEND
    else:
        try:
            min_line = f"Min height: {settings.min_height_ft()} ft"
        except ImportError:
            min_line = ""
        lines = [location_status(), "Waiting for traffic…"]
        if min_line:
            lines.insert(1, min_line)
        try:
            from display.round_touch import settings as _settings
            mode = _settings.traffic_mode()
            if mode == "marine":
                lines[-1] = "Waiting for AIS…"
            elif mode == "both":
                lines[-1] = "Waiting for aircraft / AIS…"
        except Exception:
            pass
        color = theme.HINT

    for line in lines:
        y = draw.draw_center_line(surface, line, y, font, color)


def _draw_alert_rim_flash(surface):
    """Pulse the visible rim so a new mil/squawk/watch alert is hard to miss."""
    color = aircraft_alert.rim_flash_color()
    if color is None:
        return
    width = max(6, theme.s(7))
    # Match timeout-ring placement so the bezel does not clip the stroke.
    r = theme.VISIBLE_RADIUS - width // 2 - theme.s(1)
    pygame.draw.circle(
        surface,
        color,
        (theme.CENTER_X, theme.CENTER_Y),
        r,
        width,
    )


def _draw_map_pan_overlay(surface, pan_offset: tuple[int, int] | None = None):
    """Tips while dragging the map to set a new radar center."""
    title = draw.load_font(theme.s(14), bold=True)
    font = draw.load_font(theme.s(11))
    ox = int(pan_offset[0]) if pan_offset else 0
    oy = int(pan_offset[1]) if pan_offset else 0
    # Geographic point currently under the crosshair after the map shift.
    preview_lat, preview_lon = geo.screen_to_lat_lon(
        theme.CENTER_X - ox,
        theme.CENTER_Y - oy,
    )
    center_line = f"{preview_lat:.5f}, {preview_lon:.5f}"
    lines = [
        ("Set radar center", title, theme.LABEL),
        ("Drag map · tap center to save", font, theme.HINT),
        ("Tap rim to cancel", font, theme.MUTED),
        (center_line, font, theme.MUTED),
    ]
    pad_x = theme.s(8)
    pad_y = theme.s(6)
    gap = theme.s(1)
    rendered = [(fo.render(text, True, color), fo) for text, fo, color in lines]
    text_w = max(r.get_width() for r, _ in rendered)
    text_h = sum(r.get_height() for r, _ in rendered) + gap * (len(rendered) - 1)
    panel_w = text_w + pad_x * 2
    panel_h = text_h + pad_y * 2
    panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
    panel_rect.centerx = theme.CENTER_X
    panel_rect.top = theme.CENTER_Y + theme.s(14)
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((0, 0, 0, 200))
    pygame.draw.rect(panel, (*theme.GRID[:3], 90), panel.get_rect(), max(1, theme.s(1)))
    surface.blit(panel, panel_rect.topleft)
    y = panel_rect.top + pad_y
    for surf, _fo in rendered:
        surface.blit(surf, surf.get_rect(midtop=(theme.CENTER_X, y)))
        y += surf.get_height() + gap
    pygame.draw.circle(
        surface,
        theme.LABEL,
        (theme.CENTER_X, theme.CENTER_Y),
        max(3, theme.s(4)),
        max(1, theme.s(2)),
    )


def _draw_facing_calibrate_overlay(surface):
    """Facing readout + tips on a dark panel so they stay readable over the grid."""
    title = draw.load_font(theme.s(14), bold=True)
    font = draw.load_font(theme.s(11))
    facing = settings.effective_facing_deg()
    label = settings.facing_label(facing)
    lines = [
        (f"Facing {label}", title, theme.LABEL),
        ("Drag to rotate", font, theme.HINT),
        ("Tap center to save", font, theme.MUTED),
        ("Tap rim to cancel", font, theme.MUTED),
    ]

    pad_x = theme.s(8)
    pad_y = theme.s(6)
    gap = theme.s(1)
    rendered = [(font_obj.render(text, True, color), font_obj) for text, font_obj, color in lines]
    text_w = max(r.get_width() for r, _ in rendered)
    text_h = sum(r.get_height() for r, _ in rendered) + gap * (len(rendered) - 1)
    panel_w = text_w + pad_x * 2
    panel_h = text_h + pad_y * 2

    # Sit just below center so the hub stays clear for the save tap target.
    panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
    panel_rect.centerx = theme.CENTER_X
    panel_rect.top = theme.CENTER_Y + theme.s(14)

    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((0, 0, 0, 200))
    pygame.draw.rect(panel, (*theme.GRID[:3], 90), panel.get_rect(), max(1, theme.s(1)))
    surface.blit(panel, panel_rect.topleft)

    y = panel_rect.top + pad_y
    for surf, _font in rendered:
        surface.blit(surf, surf.get_rect(midtop=(theme.CENTER_X, y)))
        y += surf.get_height() + gap

    # Small center marker so the save tap zone is obvious.
    pygame.draw.circle(
        surface,
        theme.LABEL,
        (theme.CENTER_X, theme.CENTER_Y),
        max(3, theme.s(4)),
        max(1, theme.s(2)),
    )


def _draw_map_attribution(surface):
    parts = []
    map_text = map_bg.attribution_text()
    if map_text:
        parts.append(map_text)
    precip_text = rainviewer_overlay.attribution_text()
    if precip_text:
        parts.append(precip_text)
    if not parts:
        return
    text = " · ".join(parts)
    font = draw.load_font(theme.s(11))
    rendered = font.render(text, True, theme.HINT)
    y = theme.CENTER_Y + int(theme.VISIBLE_RADIUS * 0.52)
    half = draw.circle_half_width_at_row(y, rendered.get_height())
    x = theme.CENTER_X + half - rendered.get_width() - theme.s(4)
    surface.blit(rendered, (x, y))


def _flight_screen_xy(flight) -> tuple[int, int] | None:
    lat = flight.get("plane_latitude")
    lon = flight.get("plane_longitude")
    if lat is None or lon is None:
        return None
    _, _, dist_km = geo.local_offset_km(lat, lon)
    if dist_km <= geo.inner_ring_max_km():
        return geo.lat_lon_to_screen(lat, lon)
    return geo.beyond_ring_position(lat, lon)


def _aircraft_tag_rect(x: int, y: int) -> pygame.Rect:
    block_h, _, _, _ = _tag_block_metrics()
    ly = y - block_h // 2
    symbol_half = theme.AIRCRAFT_ICON_RADIUS + theme.s(12)
    if x < theme.CENTER_X:
        anchor_x = min(
            x + symbol_half + theme.AIRCRAFT_LABEL_GAP,
            theme.CENTER_X + theme.VISIBLE_RADIUS - theme.s(20),
        )
        width = theme.CENTER_X + theme.VISIBLE_RADIUS - anchor_x - theme.s(4)
        return pygame.Rect(anchor_x, ly, max(theme.s(48), width), block_h)
    anchor_x = max(
        x - symbol_half - theme.AIRCRAFT_LABEL_GAP,
        theme.CENTER_X - theme.VISIBLE_RADIUS + theme.s(20),
    )
    width = anchor_x - (theme.CENTER_X - theme.VISIBLE_RADIUS) + theme.s(4)
    return pygame.Rect(anchor_x - max(theme.s(48), width), ly, max(theme.s(48), width), block_h)


def pick_flight_at(flights, tap_x, tap_y, alt_x=None, alt_y=None):
    points = [(tap_x, tap_y)]
    if alt_x is not None and alt_y is not None:
        points.append((alt_x, alt_y))

    best = None
    best_d2 = None
    for flight in _visible_flights(flights):
        if not aircraft_alert.is_shown_on_radar(flight):
            continue
        pos = _flight_screen_xy(flight)
        if not pos:
            continue
        x, y = pos
        lat = flight.get("plane_latitude")
        lon = flight.get("plane_longitude")
        _, _, dist_km = geo.local_offset_km(lat, lon)
        compact = dist_km > geo.inner_ring_max_km()
        hit_r = theme.TAP_PICK_RADIUS if compact else max(theme.TAP_PICK_RADIUS, theme.s(52))
        hit_r2 = hit_r * hit_r
        tag_rect = None if compact else _aircraft_tag_rect(x, y)

        for px, py in points:
            d2 = (x - px) ** 2 + (y - py) ** 2
            hit = d2 <= hit_r2
            if not hit and tag_rect is not None:
                hit = tag_rect.collidepoint(px, py)
                if hit:
                    d2 = d2 // 2
            if hit and (best_d2 is None or d2 < best_d2):
                best = flight
                best_d2 = d2
    return best


def flights_by_distance(flights):
    def dist_key(f):
        lat = f.get("plane_latitude")
        lon = f.get("plane_longitude")
        if lat is None or lon is None:
            return 1e9
        return geo.local_offset_km(lat, lon)[2]

    return sorted(_visible_flights(flights), key=dist_key)
