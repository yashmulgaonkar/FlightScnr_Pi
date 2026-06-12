"""Flight detail screen."""

from display.round_touch import aircraft, draw, geo, nav, theme
from display.round_touch.screens import common
from utilities.airline_branding import display_flight_id_for_flight
from utilities.icao_types import format_aircraft_type

FOOTER_BUTTONS = ("prev", "next", "radar")
FOOTER_EMPTY = ("radar",)


def footer_labels(flights) -> tuple[str, ...]:
    return FOOTER_BUTTONS if flights else FOOTER_EMPTY


def tap_footer_action(x: int, y: int, flights) -> str | None:
    labels = footer_labels(flights)
    idx = nav.tap_footer_button(x, y, len(labels))
    if idx is None:
        return None
    if not flights:
        return "radar"
    return ("prev", "next", "radar")[idx]


def draw_flight_detail(surface, flights, selected_index, scroll_offset: int = 0) -> int:
    draw.fill_background(surface)
    title_font = draw.load_font(theme.s(22), bold=True)
    body_font = draw.load_font(theme.s(18))
    detail_font = draw.load_font(theme.s(16))
    chrome_top = nav.content_top_y(has_dots=True)
    line_gap = theme.s(3)

    if not flights:
        nav.draw_breadcrumb(surface, ["Radar", "Flight"])
        nav.draw_footer_buttons(surface, list(FOOTER_EMPTY))
        common.draw_center_row(surface, "No aircraft", chrome_top, body_font, theme.MUTED)
        return 0

    idx = max(0, min(selected_index, len(flights) - 1))
    f = flights[idx]
    callsign = display_flight_id_for_flight(f)
    nav.draw_breadcrumb(surface, ["Radar", "Flight", callsign])
    nav.draw_page_dots(surface, idx, len(flights), active_color=theme.LABEL)

    airline = f.get("airline") or "Airline unknown"
    origin = f.get("origin") or "—"
    dest = f.get("destination") or "—"
    plane_type = format_aircraft_type(f.get("plane") or "")
    alt = aircraft.format_altitude(f.get("altitude"))

    telemetry: list[str] = []
    if alt != "—":
        telemetry.append(alt)
    speed_str = common.format_speed(f.get("ground_speed"))
    if speed_str:
        telemetry.append(speed_str)
    heading = f.get("heading")
    if heading is not None and int(heading) > 0:
        telemetry.append(f"HDG {int(heading)}°")

    lat = f.get("plane_latitude")
    lon = f.get("plane_longitude")
    dist_line = ""
    if lat is not None and lon is not None:
        dist_line = common.format_local_distance(geo.local_offset_km(lat, lon)[2])

    rows: list[tuple[str, object, tuple[int, int, int]]] = [
        (callsign, title_font, theme.LABEL),
        (airline, body_font, theme.MUTED),
        (f"{origin} > {dest}", body_font, theme.ROUTE),
    ]
    if plane_type:
        rows.append((plane_type, detail_font, theme.MUTED))
    if telemetry:
        rows.append(("  ·  ".join(telemetry), detail_font, theme.LABEL))
    if dist_line:
        rows.append((dist_line, detail_font, theme.MUTED))

    logo_h = theme.s(36) + theme.s(4)
    rows_h = sum(font.get_height() + line_gap for _, font, _ in rows) - line_gap
    total_h = logo_h + rows_h
    bottom = nav.content_bottom_y()
    max_scroll = max(0, total_h - (bottom - chrome_top))

    y = chrome_top - scroll_offset
    y = common.draw_logo(surface, f, y)
    for text, font, color in rows:
        h = font.get_height()
        if y + h >= chrome_top and y <= bottom:
            common.draw_center_row(surface, text, int(y), font, color)
        y += h + line_gap

    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))
    return max_scroll
