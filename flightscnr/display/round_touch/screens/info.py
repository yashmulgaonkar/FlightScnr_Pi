"""Settings / info screens."""

import socket

import pygame

try:
    from config import (
        AIRLABS_API_KEY,
        AISSTREAM_API_KEY,
        FR24_API_KEY,
        LOCATION_HOME,
        web_portal_url,
    )
except ImportError:
    FR24_API_KEY = ""
    AIRLABS_API_KEY = ""
    AISSTREAM_API_KEY = ""
    LOCATION_HOME = [0.0, 0.0]

    def web_portal_url(hostname: str) -> str:
        name = (hostname or "raspberrypi").split(".")[0].strip() or "raspberrypi"
        return f"http://{name}.local"

from display.round_touch import color_presets, draw, nav, settings, theme

PAGE_MAIN = 0
PAGE_DISPLAY = 1
PAGE_COLORS = 2
PAGE_COUNT = 3

FOOTER_BUTTONS = ("prev", "next", "radar")

DISPLAY_ROW_COUNT = 8


def _hostname():
    return socket.gethostname().split(".")[0]


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "Not connected"


def _route_api_line(name: str, key: str) -> str:
    if not key:
        return f"{name}: no key"
    return f"{name}: active"


def _breadcrumb(page: int) -> list[str]:
    trail = ["Radar", "Settings"]
    if page == PAGE_DISPLAY:
        trail.append("Display")
    elif page == PAGE_COLORS:
        trail.append("Theme")
    return trail


def prev_page(page: int) -> int | None:
    if page > PAGE_MAIN:
        return page - 1
    return None


def next_page(page: int) -> int | None:
    if page < PAGE_COLORS:
        return page + 1
    return None


def tap_footer_action(x: int, y: int) -> str | None:
    idx = nav.tap_footer_button(x, y, len(FOOTER_BUTTONS))
    if idx is None:
        return None
    return FOOTER_BUTTONS[idx]


def _theme_row_metrics() -> tuple[int, int, int, int]:
    body_font = _display_font()
    swatch_size = theme.s(20)
    swatch_gap = theme.s(10)
    max_label_w = max(body_font.size(name)[0] for name in color_presets.THEME_NAMES)
    row_h = max(swatch_size, body_font.get_height()) + theme.s(6)
    block_w = swatch_size + swatch_gap + max_label_w
    return swatch_size, row_h, max_label_w, block_w


def _theme_layout(scroll_offset: int) -> tuple[int, int, int]:
    top = nav.content_top_y(has_dots=True)
    _, row_h, _, _ = _theme_row_metrics()
    return top + theme.s(4) - scroll_offset, row_h, color_presets.THEME_COUNT


def theme_row_at(x: int, y: int, scroll_offset: int = 0) -> int | None:
    row_y, row_h, count = _theme_layout(scroll_offset)
    _, _, _, block_w = _theme_row_metrics()
    swatch_x = theme.CENTER_X - block_w // 2
    for i in range(count):
        ry = row_y + i * row_h
        rect = pygame.Rect(swatch_x, int(ry), block_w, row_h)
        if rect.collidepoint(x, y):
            return i
    return None


def _display_font():
    """Match flight-detail body size so more Display rows fit the round screen."""
    return draw.load_font(theme.s(14))


def _display_layout(scroll_offset: int = 0) -> tuple[int, int, int]:
    top = nav.content_top_y(has_dots=True)
    body_font = _display_font()
    row_y = top + theme.s(4) - scroll_offset
    row_h = body_font.get_height() + theme.s(6)
    return row_y, row_h, DISPLAY_ROW_COUNT


def display_row_at(x: int, y: int, scroll_offset: int = 0) -> int | None:
    row_y, row_h, count = _display_layout(scroll_offset)
    body_font = _display_font()
    top = nav.content_top_y(has_dots=True)
    bottom = nav.content_bottom_y()
    for i in range(count):
        ry = row_y + i * row_h
        if ry + body_font.get_height() < top or ry > bottom:
            continue
        half = draw.circle_half_width_at_row(int(ry), body_font.get_height())
        rect = pygame.Rect(
            theme.CENTER_X - half,
            ry - theme.s(2),
            half * 2,
            body_font.get_height() + theme.s(4),
        )
        if rect.collidepoint(x, y):
            return i
    return None


def draw_info(surface, page: int, scroll_offset: int = 0, display_focus: int = 0) -> int:
    draw.fill_background(surface)
    nav.draw_breadcrumb(surface, _breadcrumb(page))
    nav.draw_page_dots(surface, page, len(nav.SETTINGS_PAGES))

    body_font = _display_font()
    top = nav.content_top_y(has_dots=True)
    bottom = nav.content_bottom_y()
    max_scroll = 0

    if page == PAGE_MAIN:
        lines = [
            f"IP: {_local_ip()}",
            f"Host: {_hostname()}.local",
            f"Lat: {LOCATION_HOME[0]:.5f}",
            f"Lon: {LOCATION_HOME[1]:.5f}",
            f"Min height: {settings.min_height_ft()} ft",
            f"Web: {web_portal_url(_hostname())}",
            _route_api_line("FR24", FR24_API_KEY),
            _route_api_line("AirLabs", AIRLABS_API_KEY),
            _route_api_line("AIS", AISSTREAM_API_KEY),
        ]
        detail_font = draw.load_font(theme.s(13))
        gap = theme.s(2)
        body_top = top + theme.s(4)
        max_scroll = nav.draw_lines_scrolled(
            surface,
            lines,
            detail_font,
            theme.MUTED,
            scroll_offset,
            start_y=body_top,
            top=body_top,
            bottom=bottom,
            gap=gap,
        )

    elif page == PAGE_DISPLAY:
        units = settings.distance_units()
        rose = "on" if settings.show_compass_rose() else "off"
        sweep = "on" if settings.show_sweep_line() else "off"
        idle = "on" if settings.auto_idle_clock_enabled() else "off"
        traffic = settings.traffic_mode_label()
        # Traffic first — otherwise it clips off the round viewport.
        rows = [
            f"Traffic: {traffic}",
            f"Brightness: {settings.brightness_percent()}%",
            f"Units: {units}",
            f"Range: {settings.scale_label()}",
            f"Compass Rose: {rose}",
            f"Min height: {settings.min_height_ft()} ft",
            f"Sweep line: {sweep}",
            f"Idle clock: {idle}",
        ]
        row_y, row_h, _ = _display_layout(scroll_offset)
        total_h = theme.s(4) + len(rows) * row_h
        max_scroll = max(0, total_h - (bottom - top))
        for i, line in enumerate(rows):
            ry = row_y + i * row_h
            if ry + body_font.get_height() < top or ry > bottom:
                continue
            text_w, text_h = body_font.size(line)
            pad_x = theme.s(10)
            pad_y = theme.s(3)
            # Hug the label — full-circle width looked like a weird tall bar.
            rect = pygame.Rect(
                theme.CENTER_X - text_w // 2 - pad_x,
                ry - pad_y,
                text_w + pad_x * 2,
                text_h + pad_y * 2,
            )
            if i == display_focus:
                pygame.draw.rect(surface, theme.GRID, rect, max(1, theme.s(1)))
            draw.draw_center_line(surface, line, int(ry), body_font, theme.MUTED)

    else:
        active = settings.theme_index()
        swatch_size, row_h, max_label_w, block_w = _theme_row_metrics()
        swatch_gap = theme.s(10)
        total_h = theme.s(4) + color_presets.THEME_COUNT * row_h
        max_scroll = max(0, total_h - (bottom - top))

        y = top + theme.s(4) - scroll_offset
        swatch_x = theme.CENTER_X - block_w // 2
        label_x = swatch_x + swatch_size + swatch_gap
        text_h = body_font.get_height()

        for i, name in enumerate(color_presets.THEME_NAMES):
            ry = y + i * row_h
            if ry + row_h < top or ry > bottom:
                continue
            palette = color_presets.THEMES[i]
            accent = palette["sweep"]
            row_rect = pygame.Rect(swatch_x, int(ry), block_w, row_h)
            if i == active:
                pygame.draw.rect(surface, theme.GRID, row_rect, 1)
            swatch_y = int(ry + (row_h - swatch_size) // 2)
            text_y = int(ry + (row_h - text_h) // 2)
            swatch_rect = pygame.Rect(swatch_x, swatch_y, swatch_size, swatch_size)
            label = body_font.render(name, True, theme.LABEL if i == active else theme.MUTED)
            surface.blit(label, (label_x, text_y))
            pygame.draw.rect(surface, accent, swatch_rect)
            pygame.draw.rect(surface, palette["grid"], swatch_rect, max(1, theme.s(2)))

    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))
    return max_scroll
