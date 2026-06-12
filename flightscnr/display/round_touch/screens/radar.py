"""Radar screen — FlightScnr-style sweep and aircraft markers."""

import math
import time

import pygame

from display.round_touch import aircraft, draw, geo, map_bg, scale, settings, theme


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
        for label, angle in (("NE", 45), ("SE", 135), ("SW", 225), ("NW", 315)):
            rad = math.radians(angle - 90)
            x = theme.CENTER_X + int(diag_r * math.cos(rad))
            y = theme.CENTER_Y + int(diag_r * math.sin(rad))
            rendered = font.render(label, True, theme.GRID)
            rect = rendered.get_rect(center=(x, y))
            surface.blit(rendered, rect)

    use_miles = settings.distance_in_miles()
    scale_font = draw.load_font(theme.FONT_DETAIL)
    outer_km = scale.active_band()["label_km"]
    for ring in range(1, theme.RING_COUNT + 1):
        ring_km = outer_km * ring / theme.RING_COUNT
        label = scale.format_scale_tag(ring_km, use_miles)
        r = theme.GRID_OUTER_RADIUS * ring // theme.RING_COUNT
        gap = theme.SCALE_GAP_OUTER_RING_KM if ring == theme.RING_COUNT and not use_miles else theme.SCALE_GAP_FROM_OUTER_RING
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


def _draw_aircraft_tag(surface, x, y, flight):
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


def _above_min_height(flight) -> bool:
    try:
        from config import passes_altitude_filter
        return passes_altitude_filter(flight.get("altitude"))
    except ImportError:
        return True


def _visible_flights(flights):
    return [f for f in flights if _above_min_height(f)]


def _draw_flights(surface, flights):
    for flight in _visible_flights(flights):
        lat = flight.get("plane_latitude")
        lon = flight.get("plane_longitude")
        if lat is None or lon is None:
            continue
        heading = flight.get("heading") or 0
        _, _, dist_km = geo.local_offset_km(lat, lon)
        if dist_km <= geo.inner_ring_max_km():
            x, y = geo.lat_lon_to_screen(lat, lon)
            aircraft.draw_plane_icon(surface, x, y, heading, theme.AIRCRAFT)
            _draw_aircraft_tag(surface, x, y, flight)
        else:
            pos = geo.beyond_ring_position(lat, lon)
            if pos:
                aircraft.draw_plane_icon(surface, pos[0], pos[1], heading, theme.AIRCRAFT, compact=True)


def _draw_status(surface, flights):
    try:
        from config import location_configured, location_status
    except ImportError:
        location_configured = lambda: False
        location_status = lambda: ""

    font = draw.load_font(theme.FONT_DETAIL)
    scale_tag = scale.format_active_tag(settings.distance_in_miles())
    y = theme.CENTER_Y - int(theme.VISIBLE_RADIUS * 0.62)

    visible = _visible_flights(flights)
    if visible:
        header = f"{scale_tag}  ·  {len(visible)} aircraft"
        color = theme.SWEEP
    elif not location_configured():
        header = f"{scale_tag}  ·  no location"
        color = theme.TAG_ALT_DESCEND
    else:
        header = f"{scale_tag}  ·  no aircraft"
        color = theme.HINT

    draw.draw_center_line(surface, header, y, font, color)

    if not location_configured():
        lines = [
            "Set radar center on web portal",
            "in /etc/flightscnr.env",
        ]
        color = theme.TAG_ALT_DESCEND
    elif not visible:
        try:
            min_line = f"Min height: {settings.min_height_ft()} ft"
        except ImportError:
            min_line = ""
        lines = [location_status(), "Waiting for ADS-B / FR24…"]
        if min_line:
            lines.insert(1, min_line)
        color = theme.HINT
    else:
        return

    y = theme.CENTER_Y - theme.s(30)
    for line in lines:
        y = draw.draw_center_line(surface, line, y, font, color)


def draw_radar(surface, flights, full_redraw=True):
    draw.fill_background(surface)
    map_bg.request_background()
    map_bg.draw_background(surface)
    _draw_grid(surface)

    _draw_flights(surface, flights)
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


def range_header_rect() -> pygame.Rect:
    """Tap target for the range readout at the top of the round dial."""
    font = draw.load_font(theme.FONT_DETAIL)
    row_h = font.get_height()
    y = theme.CENTER_Y - int(theme.VISIBLE_RADIUS * 0.62)
    half_w = draw.circle_half_width_at_row(y, row_h * 2 + theme.s(8))
    height = row_h * 2 + theme.s(12)
    return pygame.Rect(theme.CENTER_X - half_w, y, half_w * 2, height)


def tap_on_range_header(x: int, y: int) -> bool:
    return range_header_rect().collidepoint(x, y)


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
