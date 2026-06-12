"""Tracked flight screen — route, progress bar, and live stats."""

from __future__ import annotations

import math
import socket
import time

import pygame

try:
    from config import CLOCK_FORMAT, DISTANCE_UNITS
except ImportError:
    CLOCK_FORMAT = "24hr"
    DISTANCE_UNITS = "metric"

from display.round_touch import aircraft, draw, nav, settings, theme
from display.round_touch.screens import common
from utilities.airline_branding import display_flight_id_for_flight
from utilities.icao_types import format_aircraft_type
from utilities.overhead import load_tracked_callsign

try:
    from config import web_portal_url
except ImportError:
    def web_portal_url(hostname: str) -> str:
        name = (hostname or "raspberrypi").split(".")[0].strip() or "raspberrypi"
        return f"http://{name}.local"

FOOTER_BUTTONS = ("pin", "radar")

# Push progress bar, stats, and footer lower; header (logo / route / type) stays up.
_TRACKED_FOOTER_Y_OFFSET = theme.s(25)
_TRACKED_FOOTER_BUTTON_SIZE = theme.s(46)
_TRACKED_FOOTER_BUTTON_GAP = theme.s(25)
_TRACKED_LOWER_BLOCK_Y_OFFSET = theme.s(18)

_pinned = False


def is_pinned() -> bool:
    return _pinned


def set_pinned(value: bool) -> None:
    global _pinned
    _pinned = bool(value)


def toggle_pinned() -> bool:
    set_pinned(not _pinned)
    return _pinned


def clear_pinned() -> None:
    set_pinned(False)


def footer_button_kinds(tracked_data) -> tuple[str, ...]:
    return FOOTER_BUTTONS if tracked_data else ("radar",)


def tap_footer_action(x: int, y: int, tracked_data=None) -> str | None:
    buttons = footer_button_kinds(tracked_data)
    idx = nav.tap_footer_button(
        x,
        y,
        len(buttons),
        y_offset=_TRACKED_FOOTER_Y_OFFSET,
        button_size=_TRACKED_FOOTER_BUTTON_SIZE,
        button_gap=_TRACKED_FOOTER_BUTTON_GAP,
    )
    if idx is None:
        return None
    return buttons[idx]

# Nearest-city cache for progress-bar landmark labels.
_city_cache = {"lat": None, "lon": None, "result": None}
_CITY_CACHE_THRESHOLD = 0.01

# Horizontal marquee for stats lines that exceed the round viewport width.
_marquee_states: dict[str, dict] = {}
_marquee_animating = False
_marquee_active_keys: set[str] = set()

TICKER_KEY = "ticker"


def _callsign_variants(callsign: str) -> set[str]:
    from utilities.airline_branding import IATA_TO_ICAO

    cs = (callsign or "").strip().upper()
    if not cs:
        return set()
    variants = {cs}
    if len(cs) >= 3 and cs[:2] in IATA_TO_ICAO and cs[2:3].isdigit():
        variants.add(IATA_TO_ICAO[cs[:2]] + cs[2:])
    if len(cs) >= 4 and cs[:3].isalpha():
        for iata, icao in IATA_TO_ICAO.items():
            if icao == cs[:3]:
                variants.add(iata + cs[3:])
    return variants


def resolve_display_data(tracked_data, flights) -> dict | None:
    """Merge tracked backend data with the radar flight list (same 2s refresh path)."""
    if not tracked_data:
        return tracked_data
    data = dict(tracked_data)
    if not data.get("is_live") and data.get("last_seen_ts"):
        from utilities.overhead import estimate_stale_data

        data = estimate_stale_data(data)
    if not flights:
        return data

    variants = _callsign_variants(data.get("callsign") or load_tracked_callsign() or "")
    live = None
    for flight in flights:
        fcs = (flight.get("callsign") or "").strip().upper()
        if fcs in variants:
            live = flight
            break
    if not live:
        return data

    for field in ("altitude", "ground_speed", "heading", "vertical_speed"):
        val = live.get(field)
        if val is not None:
            data[field] = val
    lat = live.get("plane_latitude")
    lon = live.get("plane_longitude")
    if lat is not None:
        data["latitude"] = lat
    if lon is not None:
        data["longitude"] = lon
    dest_lat = data.get("dest_lat") or 0
    dest_lon = data.get("dest_lon") or 0
    if lat is not None and lon is not None and dest_lat and dest_lon:
        from utilities.overhead import haversine

        data["dist_remaining"] = haversine(lat, lon, dest_lat, dest_lon)
    return data


def invalidate_ticker():
    """Reset marquee scroll state (e.g. when leaving the tracked screen)."""
    global _marquee_animating
    _marquee_states.clear()
    _marquee_active_keys.clear()
    _marquee_animating = False
    _city_cache["lat"] = None
    _city_cache["lon"] = None
    _city_cache["result"] = None


def reset_marquee():
    """Clear marquee scroll positions (e.g. when leaving the tracked screen)."""
    invalidate_ticker()


def tick_marquee() -> bool:
    """Advance marquee positions; return True while any line is scrolling."""
    global _marquee_animating
    if not _marquee_states:
        _marquee_animating = False
        return False
    step = max(1, theme.s(1))
    active = False
    for key, state in list(_marquee_states.items()):
        state["x"] -= step
        if state["x"] + state["width"] < state["clip_left"]:
            _marquee_states[key]["x"] = float(state["clip_left"] + state["clip_width"])
        active = True
    _marquee_animating = active
    return active


def marquee_animating() -> bool:
    return _marquee_animating


def live_status_active(tracked_data, flights) -> bool:
    """True when the tracked flight should show the pulsing LIVE tag."""
    if not tracked_data:
        return False
    data = resolve_display_data(tracked_data, flights)
    return data is not None and _status_label(data) == "LIVE"


def _lerp_color(
    low: tuple[int, int, int],
    high: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(low, high))


def _pulse_live_color() -> tuple[int, int, int]:
    """Fade LIVE tag between dim and bright blue (~2s cycle)."""
    phase = (math.sin(time.time() * math.pi) + 1.0) / 2.0
    return _lerp_color(theme.LIVE_DIM, theme.LIVE, phase)


def _marquee_key(y: int, row_id: str) -> str:
    if row_id == "ticker":
        return TICKER_KEY
    return f"{y}:{row_id}"


def _draw_marquee_line(
    surface,
    y: int,
    text: str,
    font,
    color,
    *,
    always_scroll: bool = False,
    row_id: str = "line",
    pulse: bool = False,
) -> None:
    """Draw a stats line; scroll horizontally when wide or always_scroll is set."""
    h = font.get_height()
    max_w = draw.circle_half_width_at_row(int(y), h) * 2
    text_w = font.size(text)[0]
    clip_left = theme.CENTER_X - max_w // 2
    clip_rect = pygame.Rect(clip_left, int(y), max_w, h + 2)
    draw_color = _pulse_live_color() if pulse else color

    if text_w <= max_w and not always_scroll:
        _marquee_states.pop(_marquee_key(int(y), row_id), None)
        rendered = font.render(text, True, draw_color)
        surface.blit(rendered, rendered.get_rect(midtop=(theme.CENTER_X, int(y))))
        return

    key = _marquee_key(int(y), row_id)
    _marquee_active_keys.add(key)
    state = _marquee_states.get(key)
    if state is None:
        state = {
            "text": text,
            "width": text_w,
            "clip_left": clip_left,
            "clip_width": max_w,
            "x": float(clip_left + max_w),
        }
        _marquee_states[key] = state
    elif row_id == "ticker" and state["text"] != text:
        state["text"] = text
        state["width"] = font.size(text)[0]
        state["clip_left"] = clip_left
        state["clip_width"] = max_w
    elif row_id != "ticker" and state["text"] != text:
        state = {
            "text": text,
            "width": text_w,
            "clip_left": clip_left,
            "clip_width": max_w,
            "x": float(clip_left + max_w),
        }
        _marquee_states[key] = state
    else:
        state["clip_left"] = clip_left
        state["clip_width"] = max_w
        if row_id == "ticker":
            state["width"] = font.size(state["text"])[0]

    display_text = state["text"] if row_id == "ticker" else text
    rendered = font.render(display_text, True, draw_color)
    old_clip = surface.get_clip()
    surface.set_clip(clip_rect)
    surface.blit(rendered, (int(state["x"]), int(y)))
    surface.set_clip(old_clip)


def _delay_color(real, scheduled, *, is_arrival: bool = False):
    if real is None or scheduled in (None, 0):
        return theme.MUTED
    delay = (real - scheduled) / 60
    if is_arrival:
        if delay <= 0:
            return theme.SWEEP
        if delay <= 30:
            return theme.TAG_TYPE
        if delay <= 60:
            return theme.AIRCRAFT
        if delay <= 240:
            return theme.TAG_ALT_DESCEND
        return theme.ROUTE
    if delay <= 20:
        return theme.SWEEP
    if delay <= 40:
        return theme.TAG_TYPE
    if delay <= 60:
        return theme.AIRCRAFT
    if delay <= 240:
        return theme.TAG_ALT_DESCEND
    return theme.ROUTE


def _calc_progress(data) -> float:
    dist_remaining = data.get("dist_remaining")
    total_distance = data.get("total_distance")
    if dist_remaining is None:
        return 0.0
    if not total_distance or total_distance <= 0:
        return 0.0
    dist_flown = total_distance - dist_remaining
    return max(0.0, min(1.0, dist_flown / total_distance))


def _format_dep_time(dep_time_str: str) -> str:
    if not dep_time_str:
        return ""
    try:
        parts = dep_time_str.split(" ")
        if len(parts) < 2:
            return dep_time_str
        hm = parts[1].split(":")
        hour = int(hm[0])
        minute = int(hm[1]) if len(hm) > 1 else 0
        if CLOCK_FORMAT == "12hr":
            ampm = "a" if hour < 12 else "p"
            display_hour = hour % 12 or 12
            if minute:
                return f"{display_hour}:{minute:02d}{ampm}"
            return f"{display_hour}{ampm}"
        return f"{hour}:{minute:02d}"
    except (ValueError, IndexError):
        return dep_time_str


def _format_dist_remaining(dist) -> str | None:
    """Format distance remaining using display units from Settings → Display."""
    if dist is None:
        return None
    use_miles = settings.distance_in_miles()
    stored_km = DISTANCE_UNITS == "metric"
    value = float(dist)
    if stored_km and use_miles:
        value /= 1.609344
    elif not stored_km and not use_miles:
        value *= 1.609344
    unit = "mi" if use_miles else "km"
    return f"{int(value)}{unit}"


def _nearest_city_label(data) -> str:
    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is None or lon is None:
        return ""
    if (
        _city_cache["lat"] is None
        or abs(lat - _city_cache["lat"]) > _CITY_CACHE_THRESHOLD
        or abs(lon - _city_cache["lon"]) > _CITY_CACHE_THRESHOLD
    ):
        _city_cache["lat"] = lat
        _city_cache["lon"] = lon
        try:
            from utilities.cities import get_nearest_city

            _city_cache["result"] = get_nearest_city(lat, lon)
        except Exception:
            _city_cache["result"] = None
    nearest = _city_cache["result"]
    if nearest:
        return f"nr {nearest['name']}"
    return ""


def _status_label(data) -> str:
    if data.get("is_scheduled"):
        return "SCHEDULED"
    if data.get("has_landed"):
        return "LANDED"
    if not data.get("is_live", True):
        return "ESTIMATED"
    return "LIVE"


def _eta_line(data) -> str | None:
    time_remaining = data.get("time_remaining")
    if not time_remaining:
        return None
    return f"Estimated Time Remaining: {time_remaining}"


def _ticker_parts(data) -> list[str]:
    parts: list[str] = []
    dist_str = _format_dist_remaining(data.get("dist_remaining"))
    if dist_str:
        parts.append(dist_str)
    landmark = _nearest_city_label(data)
    if landmark:
        parts.append(landmark)
    parts.extend(_telemetry_parts(data))
    return parts


def _telemetry_parts(data) -> list[str]:
    parts: list[str] = []
    alt_str = aircraft.format_altitude(data.get("altitude"))
    if alt_str != "—":
        vs = data.get("vertical_speed", 0) or 0
        if vs > 64:
            alt_str += " ↑"
        elif vs < -64:
            alt_str += " ↓"
        parts.append(alt_str)

    speed_str = common.format_speed(data.get("ground_speed"))
    if speed_str:
        parts.append(speed_str)

    heading = data.get("heading")
    if heading is not None and int(heading) > 0:
        parts.append(f"HDG {int(heading)}°")
    return parts


def _scheduled_rows(data) -> list[tuple[str, tuple[int, int, int]]]:
    dep = _format_dep_time(data.get("dep_time", ""))
    origin = data.get("origin", "")
    dest = data.get("destination", "")
    if dep:
        return [(f"Departs {dep}  {origin} → {dest}", theme.ROUTE)]
    return [(f"Scheduled  {origin} → {dest}", theme.ROUTE)]


def _build_stats_rows(
    data,
) -> list[tuple[str, tuple[int, int, int], bool, bool]]:
    """Status, ETA line, then scrolling distance/telemetry ticker."""
    if data.get("is_scheduled"):
        return [(text, color, False, False) for text, color in _scheduled_rows(data)]

    rows: list[tuple[str, tuple[int, int, int], bool, bool]] = []
    status = _status_label(data)
    if status == "LANDED":
        rows.append((status, theme.SWEEP, False, False))
    elif status == "LIVE":
        rows.append((status, theme.LIVE, False, True))
    else:
        rows.append((status, theme.TAG_TYPE, False, False))

    eta = _eta_line(data)
    if eta:
        rows.append((eta, theme.MUTED, False, False))

    parts = _ticker_parts(data)
    if parts:
        rows.append(("  ·  ".join(parts), theme.LABEL, True, False))
    return rows


def _draw_stats_rows_at(
    surface,
    rows,
    y: int,
    font,
    *,
    clip_top: int | None = None,
    clip_bottom: int | None = None,
) -> int:
    gap = theme.s(6)
    h = font.get_height()
    for i, (text, color, always_scroll, pulse) in enumerate(rows):
        if clip_bottom is not None and int(y) > clip_bottom:
            break
        if clip_top is None or int(y) + h >= clip_top:
            row_id = "ticker" if always_scroll else f"stat{i}"
            _draw_marquee_line(
                surface,
                int(y),
                text,
                font,
                color,
                always_scroll=always_scroll,
                row_id=row_id,
                pulse=pulse,
            )
        y += h + gap
    return y


def _draw_route_header(surface, data, y: int, title_font, body_font) -> int:
    airline_name = data.get("airline_name", "") or data.get("airline", "")
    display_id = display_flight_id_for_flight(data)
    flight_num = "".join(ch for ch in display_id if ch.isnumeric())
    display_name = f"{airline_name} {flight_num}".strip() if airline_name else display_id
    origin = data.get("origin", "???")
    destination = data.get("destination", "???")

    title_h = title_font.get_height()
    title_max_w = draw.circle_half_width_at_row(y, title_h) * 2
    title_line = draw.fit_text(display_name, title_font, title_max_w)
    title_img = title_font.render(title_line, True, theme.LABEL)
    surface.blit(title_img, title_img.get_rect(midtop=(theme.CENTER_X, y)))
    y += title_h + theme.s(2)

    origin_color = _delay_color(
        data.get("time_real_departure"),
        data.get("time_scheduled_departure"),
    )
    dest_color = _delay_color(
        data.get("time_estimated_arrival"),
        data.get("time_scheduled_arrival"),
        is_arrival=True,
    )

    h = body_font.get_height()
    max_w = draw.circle_half_width_at_row(y, h) * 2
    sep = "  →  "
    origin_img = body_font.render(origin, True, origin_color)
    sep_img = body_font.render(sep, True, theme.MUTED)
    dest_img = body_font.render(destination, True, dest_color)
    total_w = origin_img.get_width() + sep_img.get_width() + dest_img.get_width()
    if total_w > max_w:
        y = draw.draw_center_line(surface, f"{origin}{sep}{destination}", y, body_font, theme.ROUTE)
        return y + theme.s(1)

    x = theme.CENTER_X - total_w // 2
    surface.blit(origin_img, (x, y))
    x += origin_img.get_width()
    surface.blit(sep_img, (x, y))
    x += sep_img.get_width()
    surface.blit(dest_img, (x, y))
    return y + h + theme.s(1)


def _draw_aircraft_type(surface, data, y: int, font) -> int:
    plane_type = format_aircraft_type(data.get("aircraft_type") or "")
    if not plane_type:
        return y
    h = font.get_height()
    max_w = draw.circle_half_width_at_row(y, h) * 2
    line = draw.fit_text(plane_type, font, max_w)
    rendered = font.render(line, True, theme.MUTED)
    surface.blit(rendered, rendered.get_rect(midtop=(theme.CENTER_X, y)))
    return y + h + theme.s(2)


def _draw_progress_bar(surface, data, y: int) -> int:
    bar_h = theme.s(5)
    icon_pad = theme.s(4)
    half_w = draw.circle_half_width_at_row(y, bar_h + icon_pad * 2)
    bar_w = max(theme.s(80), half_w * 2 - theme.s(16))
    x0 = theme.CENTER_X - bar_w // 2
    bar_y = y + icon_pad
    bar_rect = pygame.Rect(x0, bar_y, bar_w, bar_h)
    pygame.draw.rect(surface, theme.GRID, bar_rect, 1)

    progress = _calc_progress(data)
    is_live = data.get("is_live", True)
    flown_color = theme.SWEEP if is_live else theme.TAG_ALT_DESCEND

    flown_w = int(bar_w * progress)
    if flown_w > 0:
        pygame.draw.rect(surface, flown_color, pygame.Rect(x0, bar_y, flown_w, bar_h))

    if flown_w < bar_w:
        pygame.draw.rect(
            surface,
            theme.GRID,
            pygame.Rect(x0 + flown_w, bar_y, bar_w - flown_w, bar_h),
            1,
        )

    # Aircraft icon on the bar — nose points toward destination (right).
    margin = theme.s(6)
    usable = max(1, bar_w - margin * 2)
    plane_x = x0 + margin + int(usable * progress)
    plane_y = bar_y + bar_h // 2
    plane_color = theme.AIRCRAFT if is_live else theme.TAG_ALT_DESCEND
    aircraft.draw_progress_plane(surface, plane_x, plane_y, plane_color)

    return bar_y + bar_h + icon_pad


def _draw_empty(surface, top: int, bottom: int):
    title_font = draw.load_font(theme.FONT_TITLE, bold=True)
    body_font = draw.load_font(theme.FONT_BODY)
    detail_font = draw.load_font(theme.FONT_DETAIL)

    y = top + theme.s(12)
    y = draw.draw_center_line(surface, "No tracked flight.", y, title_font, theme.LABEL)
    y += theme.s(6)
    if y + body_font.get_height() <= bottom:
        y = draw.draw_center_line(
            surface,
            "Select a flight on the web portal.",
            y,
            body_font,
            theme.MUTED,
        )
        y += theme.s(6)
    if y + detail_font.get_height() <= bottom:
        host = socket.gethostname().split(".")[0]
        draw.draw_center_line(surface, web_portal_url(host), y, detail_font, theme.HINT)


def _draw_pending(surface, callsign: str, top: int, bottom: int):
    title_font = draw.load_font(theme.FONT_TITLE, bold=True)
    body_font = draw.load_font(theme.FONT_BODY)
    detail_font = draw.load_font(theme.FONT_DETAIL)

    y = top + theme.s(8)
    y = common.draw_logo(surface, {"callsign": callsign}, y)
    y = draw.draw_center_line(surface, callsign, y, title_font, theme.LABEL)
    y += theme.s(10)
    if y + body_font.get_height() <= bottom:
        y = draw.draw_center_line(surface, "Waiting for flight data", y, body_font, theme.MUTED)
        y += theme.s(8)
    if y + detail_font.get_height() <= bottom:
        y = draw.draw_center_line(surface, "Starts when flight goes live", y, detail_font, theme.HINT)


def _finish_marquee_frame():
    global _marquee_animating
    for key in list(_marquee_states):
        if key not in _marquee_active_keys:
            del _marquee_states[key]
    _marquee_active_keys.clear()
    _marquee_animating = bool(_marquee_states)


def draw_tracked(
    surface,
    tracked_data,
    callsign: str | None = None,
    scroll_offset: int = 0,
) -> int:
    global _marquee_active_keys
    _marquee_active_keys = set()
    del scroll_offset  # tracked page does not scroll vertically

    draw.fill_background(surface)
    raw_callsign = (callsign or load_tracked_callsign() or "").strip().upper()
    display_id = raw_callsign
    if tracked_data:
        display_id = display_flight_id_for_flight(tracked_data)
    trail = ["Radar", "Track"]
    if display_id and display_id != "—":
        trail.append(display_id)
    nav.draw_breadcrumb(surface, trail)

    top = nav.content_top_y()
    title_font = draw.load_font(theme.s(20), bold=True)
    body_font = draw.load_font(theme.s(16))
    detail_font = draw.load_font(theme.s(15))
    content_bottom = nav.content_bottom_y(footer_y_offset=_TRACKED_FOOTER_Y_OFFSET)
    footer_kw = {
        "y_offset": _TRACKED_FOOTER_Y_OFFSET,
        "button_size": _TRACKED_FOOTER_BUTTON_SIZE,
        "button_gap": _TRACKED_FOOTER_BUTTON_GAP,
        "pin_active": is_pinned(),
    }
    footer = list(footer_button_kinds(tracked_data))

    if not raw_callsign:
        _draw_empty(surface, top, content_bottom)
        nav.draw_footer_buttons(surface, footer, **footer_kw)
        _finish_marquee_frame()
        return 0

    if not tracked_data:
        _draw_pending(surface, raw_callsign, top, content_bottom)
        nav.draw_footer_buttons(surface, footer, **footer_kw)
        _finish_marquee_frame()
        return 0

    stats_rows = _build_stats_rows(tracked_data)
    y = top
    y = common.draw_logo(surface, tracked_data, y, logo_h=theme.s(30))
    y = _draw_route_header(surface, tracked_data, y, title_font, body_font)
    y = _draw_aircraft_type(surface, tracked_data, y, detail_font)
    y += _TRACKED_LOWER_BLOCK_Y_OFFSET
    if not tracked_data.get("is_scheduled"):
        y = _draw_progress_bar(surface, tracked_data, y)
        y += theme.s(4)
    else:
        y += theme.s(4)
    if stats_rows:
        _draw_stats_rows_at(
            surface,
            stats_rows,
            y,
            detail_font,
            clip_top=top,
            clip_bottom=content_bottom,
        )
    nav.draw_footer_buttons(surface, footer, **footer_kw)
    _finish_marquee_frame()
    return 0
