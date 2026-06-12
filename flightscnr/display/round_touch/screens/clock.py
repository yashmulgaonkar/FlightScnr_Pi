"""Clock screen with optional weather."""

from datetime import datetime
import time

import pygame

try:
    from config import TEMPERATURE_UNITS
except ImportError:
    TEMPERATURE_UNITS = "metric"

from display.round_touch import draw, nav, settings, theme

_weather_cache = {"temp": None, "ts": 0}


def _fetch_temperature():
    now = time.time()
    if now - _weather_cache["ts"] < 1800 and _weather_cache["temp"] is not None:
        return _weather_cache["temp"]
    try:
        result = __import__("utilities.temperature", fromlist=["grab_temperature_and_humidity"]).grab_temperature_and_humidity()
        if result and result[0] is not None:
            _weather_cache["temp"] = result[0]
            _weather_cache["ts"] = now
            return result[0]
    except Exception:
        pass
    return _weather_cache["temp"]


def _time_strings(now: datetime | None = None):
    now = now or datetime.now()
    if settings.use_12hr_clock():
        time_str = now.strftime("%I:%M").lstrip("0") or "12"
        ampm = now.strftime("%p")
    else:
        time_str = now.strftime("%H:%M")
        ampm = ""
    return time_str, ampm


def _ampm_top_y(time_font, ampm_font, time_y: int) -> int:
    """Align AM/PM baseline with the clock digits."""
    return time_y + time_font.get_ascent() - ampm_font.get_ascent()


def _draw_time_block(surface, y: int) -> int:
    """Draw the large time readout; return y below the block."""
    time_font = draw.load_font(theme.FONT_CLOCK, bold=True)
    ampm_font = draw.load_font(theme.FONT_CLOCK_AMPM, bold=True)
    time_str, ampm = _time_strings()

    if ampm:
        gap = theme.s(10)
        time_img = time_font.render(time_str, True, theme.SWEEP)
        ampm_img = ampm_font.render(ampm, True, theme.SWEEP)
        total_w = time_img.get_width() + gap + ampm_img.get_width()
        x = theme.CENTER_X - total_w // 2
        time_y = y
        ampm_y = _ampm_top_y(time_font, ampm_font, time_y)
        surface.blit(time_img, (x, time_y))
        surface.blit(ampm_img, (x + time_img.get_width() + gap, ampm_y))
        block_bottom = max(time_y + time_img.get_height(), ampm_y + ampm_img.get_height())
        return block_bottom + theme.s(14)

    rendered = time_font.render(time_str, True, theme.SWEEP)
    rect = rendered.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(rendered, rect)
    return rect.bottom + theme.s(14)


def time_tap_rect() -> pygame.Rect:
    """Tap target for the large clock readout."""
    time_font = draw.load_font(theme.FONT_CLOCK, bold=True)
    ampm_font = draw.load_font(theme.FONT_CLOCK_AMPM, bold=True)
    time_str, ampm = _time_strings()
    y = nav.content_top_y() + theme.s(8)

    if ampm:
        gap = theme.s(8)
        time_w = time_font.size(time_str)[0]
        ampm_w = ampm_font.size(ampm)[0]
        total_w = time_w + gap + ampm_w
        x = theme.CENTER_X - total_w // 2
        height = time_font.get_height()
        return pygame.Rect(x, y, total_w, height)

    rendered = time_font.render(time_str, True, theme.SWEEP)
    return rendered.get_rect(midtop=(theme.CENTER_X, y))


FOOTER_BUTTONS = ("radar",)


def tap_footer_action(x: int, y: int) -> str | None:
    idx = nav.tap_footer_button(x, y, len(FOOTER_BUTTONS))
    if idx is None:
        return None
    return FOOTER_BUTTONS[idx]


def tap_on_time(x: int, y: int) -> bool:
    return time_tap_rect().collidepoint(x, y)


def draw_clock(surface):
    draw.fill_background(surface)
    nav.draw_breadcrumb(surface, ["Radar", "Clock"])
    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))

    now = datetime.now()
    date_str = now.strftime("%a %b %d, %Y")
    tz_name = time.tzname[0] if time.tzname else "Local"

    temp = _fetch_temperature()
    temp_line = ""
    if temp is not None:
        unit = "°F" if TEMPERATURE_UNITS == "imperial" else "°C"
        temp_line = f"{int(round(temp))}{unit}"

    body_font = draw.load_font(theme.FONT_BODY)
    detail_font = draw.load_font(theme.FONT_DETAIL)

    y = _draw_time_block(surface, nav.content_top_y() + theme.s(8))
    y = draw.draw_center_line(surface, date_str, y, body_font, theme.LABEL)
    y += theme.s(4)
    y = draw.draw_center_line(surface, tz_name, y, detail_font, theme.HINT)
    if temp_line:
        y += theme.s(8)
        draw.draw_center_line(surface, temp_line, y, body_font, theme.ROUTE)
