"""Radar screen — FlightScnr-style sweep and aircraft markers."""

import math
import time

import pygame

from display.round_touch import aircraft, draw, geo, map_bg, scale, settings, theme
from display.round_touch import alert_prefs
from display.round_touch import vessel_declutter
from utilities import aircraft_alert
from utilities.overhead import load_tracked_callsign


_sweep_angle = 0.0
_sweep_last_ms = 0


def _init_sweep():
    global _sweep_angle, _sweep_last_ms
    _sweep_angle = 0.0
    _sweep_last_ms = time.time() * 1000


def tick_sweep():
    global _sweep_angle, _sweep_last_ms
    now = time.time() * 1000
    if _sweep_last_ms == 0:
        _sweep_last_ms = now
        return
    dt = now - _sweep_last_ms
    _sweep_last_ms = now
    _sweep_angle = (_sweep_angle + 360.0 * dt / theme.SWEEP_PERIOD_MS) % 360.0


def _draw_grid(surface):
    center = (theme.CENTER_X, theme.CENTER_Y)
    line_w = max(1, theme.s(2))
    for ring in range(1, theme.RING_COUNT + 1):
        r = theme.GRID_OUTER_RADIUS * ring // theme.RING_COUNT
        draw.draw_dashed_circle(surface, center, r, theme.GRID, width=line_w)

    cx, cy = theme.CENTER_X, theme.CENTER_Y
    r = theme.GRID_OUTER_RADIUS
    draw.draw_dashed_line(surface, (cx - r, cy), (cx + r, cy), theme.CROSSHAIR, width=line_w)
    draw.draw_dashed_line(surface, (cx, cy - r), (cx, cy + r), theme.CROSSHAIR, width=line_w)

    if settings.show_compass_rose():
        font = draw.load_font(theme.FONT_CARDINAL, bold=True)
        north_y = theme.CENTER_Y - theme.VISIBLE_RADIUS + theme.CARDINAL_NORTH_OFFSET_Y
        south_y = theme.CENTER_Y + theme.VISIBLE_RADIUS - theme.CARDINAL_SOUTH_OFFSET_Y - font.get_height()
        west_x = theme.CENTER_X - theme.VISIBLE_RADIUS + theme.s(10)
        east_x = theme.CENTER_X + theme.VISIBLE_RADIUS - theme.s(10) - font.size("E")[0]
        labels = [
            ("N", theme.CENTER_X, north_y),
            ("S", theme.CENTER_X, south_y),
            ("W", west_x, theme.CENTER_Y - font.get_height() // 2),
            ("E", east_x, theme.CENTER_Y - font.get_height() // 2),
        ]
        for text, x, y in labels:
            rendered = font.render(text, True, theme.GRID)
            if text in ("N", "S"):
                rect = rendered.get_rect(midtop=(x, y))
            elif text == "W":
                rect = rendered.get_rect(midleft=(x, y))
            else:
                rect = rendered.get_rect(topleft=(x, y))
            surface.blit(rendered, rect)

        diag_r = theme.GRID_OUTER_RADIUS - theme.CARDINAL_DIAGONAL_INSET
        diag_font = draw.load_font(theme.FONT_CARDINAL_DIAG, bold=True)
        for label, angle in (("NE", 45), ("SE", 135), ("SW", 225), ("NW", 315)):
            rad = math.radians(angle - 90)
            x = theme.CENTER_X + int(diag_r * math.cos(rad))
            y = theme.CENTER_Y + int(diag_r * math.sin(rad))
            rendered = diag_font.render(label, True, theme.GRID)
            rect = rendered.get_rect(center=(x, y))
            surface.blit(rendered, rect)

    use_units = settings.distance_units()
    scale_font = draw.load_font(theme.FONT_DETAIL)
    outer_km = scale.active_band()["label_km"]
    for ring in range(1, theme.RING_COUNT + 1):
        ring_km = outer_km * ring / theme.RING_COUNT
        label = scale.format_scale_tag(ring_km, use_units)
        r = theme.GRID_OUTER_RADIUS * ring // theme.RING_COUNT
        gap = theme.SCALE_GAP_OUTER_RING_KM if ring == theme.RING_COUNT and use_units == "km" else theme.SCALE_GAP_FROM_OUTER_RING
        label_r = r - gap
        rad = math.radians(theme.SCALE_LABEL_BEARING_DEG - 90)
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
        color = theme.GRID
        if vessel_declutter.hierarchy_enabled() and vessel_declutter.is_parked(flight):
            color = theme.HINT
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
        (name, theme.GRID, main_font, offsets[0]),
        (plane_type, theme.TAG_TYPE, sub_font, offsets[1]),
        (alt, theme.TAG_ALT_ASCEND, sub_font, offsets[2]),
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
        (callsign, theme.GRID, main_font, offsets[0]),
        (plane_type, theme.TAG_TYPE, sub_font, offsets[1]),
        (alt, alt_color, sub_font, offsets[2]),
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
    cs = (flight.get("callsign") or "").strip().upper()
    return cs == tracked or cs.startswith(tracked)


def _flight_icon_color(flight, *, compact: bool):
    if _is_tracked(flight) and not compact:
        return theme.SWEEP
    if aircraft_alert.is_highlighted(flight):
        if aircraft_alert.pulse_phase():
            return theme.ALERT_FLASH
        return theme.GRID
    if vessel_declutter.is_vessel(flight) and vessel_declutter.hierarchy_enabled():
        if vessel_declutter.is_parked(flight):
            return theme.VESSEL_PARKED
        return theme.VESSEL_MOVING
    return theme.AIRCRAFT


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
        heading = flight.get("heading") or 0
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
        aircraft.draw_plane_icon(
            surface,
            x,
            y,
            flight.get("heading") or 0,
            _flight_icon_color(flight, compact=True),
            compact=True,
            flight=flight,
        )

    for _, flight, (x, y) in inner_items:
        heading = flight.get("heading") or 0
        color = _flight_icon_color(flight, compact=False)
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


def draw_radar(surface, flights, full_redraw=True):
    alert_prefs.reload()
    draw.fill_background(surface)
    map_bg.request_background()
    map_bg.draw_background(surface)
    _draw_grid(surface)

    _draw_flights(surface, flights)
    if settings.show_sweep_line():
        draw.draw_sweep_line(surface, _sweep_angle, theme.SWEEP, width=max(2, theme.s(2)))
    _draw_status(surface, flights)
    _draw_map_attribution(surface)


def _draw_map_attribution(surface):
    text = map_bg.attribution_text()
    if not text:
        return
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
