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
PAGE_OPTIONS = 2
PAGE_COLORS = 3
PAGE_COUNT = 4

FOOTER_BUTTONS = ("prev", "next", "radar")

# Display + Options were one tall page; split so both fit the round viewport.
# Brightness is last and drawn as a drag slider (not a tap-cycle row).
DISPLAY_ACTIONS = (
    "facing",
    "recenter",
    "compass",
    "range_rings",
    "sweep",
    "units",
    "range",
    "brightness",
)
OPTIONS_ACTIONS = (
    "traffic",
    "min_height",
    "max_height",
    "map_style",
    "vfr_opacity",
    "precipitation",
    "idle_clock",
)


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
    elif page == PAGE_OPTIONS:
        trail.append("Options")
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
    swatch_size = theme.s(18)
    swatch_gap = theme.s(8)
    max_label_w = max(body_font.size(name)[0] for name in color_presets.THEME_NAMES)
    row_h = max(swatch_size, body_font.get_height()) + theme.s(4)
    block_w = swatch_size + swatch_gap + max_label_w
    return swatch_size, row_h, max_label_w, block_w


def _theme_slider_metrics() -> tuple[int, int, int, int]:
    """track_w, row_h, label_w, value_w for RGB rows."""
    body_font = _display_font()
    label_w = max(body_font.size(ch)[0] for ch in ("R", "G", "B"))
    value_w = body_font.size("255")[0]
    track_w = theme.s(140)
    row_h = body_font.get_height() + theme.s(6)
    return track_w, row_h, label_w, value_w


def _theme_section_gaps() -> tuple[int, int, int]:
    """top_pad, preset→custom gap, custom heading height."""
    return theme.s(2), theme.s(6), theme.s(18)


def _theme_content_height() -> int:
    _, preset_h, _, _ = _theme_row_metrics()
    _, slider_h, _, _ = _theme_slider_metrics()
    top_pad, section_gap, heading_h = _theme_section_gaps()
    return (
        top_pad
        + color_presets.THEME_COUNT * preset_h
        + section_gap
        + heading_h
        + 3 * slider_h
        + theme.s(4)
    )


def _theme_layout(scroll_offset: int) -> tuple[int, int, int]:
    top = nav.content_top_y(has_dots=True)
    _, row_h, _, _ = _theme_row_metrics()
    top_pad, _, _ = _theme_section_gaps()
    return top + top_pad - scroll_offset, row_h, color_presets.THEME_COUNT


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


def _theme_slider_geometry(scroll_offset: int = 0) -> list[tuple[pygame.Rect, int, int]]:
    """Per-channel (hit_rect, track_x, track_w)."""
    top = nav.content_top_y(has_dots=True)
    _, preset_h, _, _ = _theme_row_metrics()
    track_w, slider_h, label_w, value_w = _theme_slider_metrics()
    top_pad, section_gap, heading_h = _theme_section_gaps()
    gap = theme.s(8)
    y0 = (
        top
        + top_pad
        + color_presets.THEME_COUNT * preset_h
        + section_gap
        + heading_h
        - scroll_offset
    )
    block_w = label_w + gap + track_w + gap + value_w
    track_x = theme.CENTER_X - block_w // 2 + label_w + gap
    hit_pad = theme.s(8)
    out: list[tuple[pygame.Rect, int, int]] = []
    for i in range(3):
        ry = y0 + i * slider_h
        hit = pygame.Rect(
            track_x - hit_pad,
            int(ry),
            track_w + 2 * hit_pad,
            slider_h,
        )
        out.append((hit, track_x, track_w))
    return out


def theme_slider_at(x: int, y: int, scroll_offset: int = 0) -> int | None:
    """Return 0/1/2 for R/G/B if (x,y) hits a slider row, else None."""
    for i, (hit, _, _) in enumerate(_theme_slider_geometry(scroll_offset)):
        if hit.collidepoint(x, y):
            return i
    return None


def theme_slider_value_at(x: int, channel: int, scroll_offset: int = 0) -> int | None:
    """Map screen x on slider *channel* to 0–255."""
    rows = _theme_slider_geometry(scroll_offset)
    if channel < 0 or channel >= len(rows):
        return None
    _, track_x, track_w = rows[channel]
    t = (x - track_x) / max(1, track_w)
    return max(0, min(255, int(round(t * 255))))


def _display_font():
    """Match flight-detail body size so more Display rows fit the round screen."""
    return draw.load_font(theme.s(14))


def _settings_row_page(page: int) -> bool:
    return page in (PAGE_DISPLAY, PAGE_OPTIONS)


def _row_actions(page: int) -> tuple[str, ...]:
    if page == PAGE_DISPLAY:
        return DISPLAY_ACTIONS
    if page == PAGE_OPTIONS:
        return OPTIONS_ACTIONS
    return ()


def _display_layout(page: int, scroll_offset: int = 0) -> tuple[int, int, int]:
    top = nav.content_top_y(has_dots=True)
    body_font = _display_font()
    row_y = top + theme.s(4) - scroll_offset
    row_h = body_font.get_height() + theme.s(6)
    return row_y, row_h, len(_row_actions(page))


def display_row_at(x: int, y: int, page: int, scroll_offset: int = 0) -> int | None:
    if not _settings_row_page(page):
        return None
    row_y, row_h, count = _display_layout(page, scroll_offset)
    body_font = _display_font()
    top = nav.content_top_y(has_dots=True)
    bottom = nav.content_bottom_y()
    actions = _row_actions(page)
    for i in range(count):
        if actions[i] in ("brightness", "vfr_opacity"):
            continue
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


def _brightness_slider_metrics() -> tuple[int, int, int, int]:
    """track_w, row_h, label_w, value_w for the Display brightness slider."""
    body_font = _display_font()
    label_w = body_font.size("Brightness")[0]
    value_w = body_font.size("100%")[0]
    track_w = theme.s(120)
    row_h = body_font.get_height() + theme.s(8)
    return track_w, row_h, label_w, value_w


def brightness_row_index() -> int:
    try:
        return DISPLAY_ACTIONS.index("brightness")
    except ValueError:
        return len(DISPLAY_ACTIONS) - 1


def _brightness_slider_geometry(scroll_offset: int = 0) -> tuple[pygame.Rect, int, int] | None:
    """(hit_rect, track_x, track_w) for the Display brightness slider."""
    if "brightness" not in DISPLAY_ACTIONS:
        return None
    row_y, row_h, _ = _display_layout(PAGE_DISPLAY, scroll_offset)
    track_w, slider_h, label_w, value_w = _brightness_slider_metrics()
    gap = theme.s(8)
    idx = brightness_row_index()
    # Align slider with the brightness slot; allow a slightly taller hit target.
    ry = row_y + idx * row_h
    block_w = label_w + gap + track_w + gap + value_w
    left_x = theme.CENTER_X - block_w // 2
    track_x = left_x + label_w + gap
    hit_pad = theme.s(8)
    hit = pygame.Rect(
        track_x - hit_pad,
        int(ry - theme.s(2)),
        track_w + 2 * hit_pad,
        max(row_h, slider_h) + theme.s(4),
    )
    return hit, track_x, track_w


def brightness_slider_at(x: int, y: int, scroll_offset: int = 0) -> bool:
    geom = _brightness_slider_geometry(scroll_offset)
    if geom is None:
        return False
    hit, _, _ = geom
    return hit.collidepoint(x, y)


def brightness_slider_value_at(x: int, scroll_offset: int = 0) -> int | None:
    """Map screen x on the brightness track to BRIGHTNESS_MIN–100."""
    geom = _brightness_slider_geometry(scroll_offset)
    if geom is None:
        return None
    _, track_x, track_w = geom
    lo = settings.BRIGHTNESS_MIN_PERCENT
    hi = settings.BRIGHTNESS_MAX_PERCENT
    t = (x - track_x) / max(1, track_w)
    span = hi - lo
    return max(lo, min(hi, int(round(lo + t * span))))


def _vfr_opacity_slider_metrics() -> tuple[int, int, int, int]:
    """track_w, row_h, label_w, value_w for the Options VFR opacity slider."""
    body_font = _display_font()
    label_w = body_font.size("VFR opacity")[0]
    value_w = body_font.size("100%")[0]
    track_w = theme.s(100)
    row_h = body_font.get_height() + theme.s(8)
    return track_w, row_h, label_w, value_w


def vfr_opacity_row_index() -> int:
    try:
        return OPTIONS_ACTIONS.index("vfr_opacity")
    except ValueError:
        return -1


def _vfr_opacity_slider_geometry(scroll_offset: int = 0) -> tuple[pygame.Rect, int, int] | None:
    """(hit_rect, track_x, track_w) for the Options VFR opacity slider."""
    if "vfr_opacity" not in OPTIONS_ACTIONS:
        return None
    row_y, row_h, _ = _display_layout(PAGE_OPTIONS, scroll_offset)
    track_w, slider_h, label_w, value_w = _vfr_opacity_slider_metrics()
    gap = theme.s(8)
    idx = vfr_opacity_row_index()
    if idx < 0:
        return None
    ry = row_y + idx * row_h
    block_w = label_w + gap + track_w + gap + value_w
    left_x = theme.CENTER_X - block_w // 2
    track_x = left_x + label_w + gap
    hit_pad = theme.s(8)
    hit = pygame.Rect(
        track_x - hit_pad,
        int(ry - theme.s(2)),
        track_w + 2 * hit_pad,
        max(row_h, slider_h) + theme.s(4),
    )
    return hit, track_x, track_w


def vfr_opacity_slider_at(x: int, y: int, scroll_offset: int = 0) -> bool:
    geom = _vfr_opacity_slider_geometry(scroll_offset)
    if geom is None:
        return False
    hit, _, _ = geom
    return hit.collidepoint(x, y)


def vfr_opacity_slider_value_at(x: int, scroll_offset: int = 0) -> int | None:
    """Map screen x on the VFR opacity track to VFR_OPACITY_MIN–100."""
    geom = _vfr_opacity_slider_geometry(scroll_offset)
    if geom is None:
        return None
    _, track_x, track_w = geom
    lo = settings.VFR_OPACITY_MIN_PERCENT
    hi = settings.VFR_OPACITY_MAX_PERCENT
    t = (x - track_x) / max(1, track_w)
    span = hi - lo
    return max(lo, min(hi, int(round(lo + t * span))))


def display_action_at(page: int, row: int) -> str | None:
    actions = _row_actions(page)
    if 0 <= row < len(actions):
        return actions[row]
    return None


def _display_row_labels() -> list[str]:
    units = settings.distance_units()
    rose = "on" if settings.show_compass_rose() else "off"
    rings = "on" if settings.show_range_rings() else "off"
    facing = settings.facing_label()
    sweep = "on" if settings.show_sweep_line() else "off"
    # Brightness is drawn as a slider; placeholder keeps row count aligned.
    return [
        f"Facing: {facing}",
        "Set radar center",
        f"Compass Rose: {rose}",
        f"Range rings: {rings}",
        f"Sweep line: {sweep}",
        f"Units: {units}",
        f"Range: {settings.scale_label()}",
        "",  # brightness slider
    ]


def _options_row_labels() -> list[str]:
    precip = "on" if settings.show_precipitation() else "off"
    idle = "on" if settings.auto_idle_clock_enabled() else "off"
    return [
        f"Traffic: {settings.traffic_mode_label()}",
        f"Min height: {settings.min_height_ft()} ft",
        f"Max height: {settings.max_height_ft()} ft",
        f"Map: {settings.map_style_label()}",
        "",  # VFR opacity slider
        f"Precipitation: {precip}",
        f"Idle clock: {idle}",
    ]


def _draw_settings_rows(
    surface,
    rows: list[str],
    scroll_offset: int,
    display_focus: int,
    top: int,
    bottom: int,
    *,
    draw_brightness_slider: bool = False,
    draw_vfr_opacity_slider: bool = False,
) -> int:
    body_font = _display_font()
    row_y = top + theme.s(4) - scroll_offset
    row_h = body_font.get_height() + theme.s(6)
    total_h = theme.s(4) + len(rows) * row_h
    max_scroll = max(0, total_h - (bottom - top))
    brightness_idx = brightness_row_index() if draw_brightness_slider else -1
    vfr_idx = vfr_opacity_row_index() if draw_vfr_opacity_slider else -1
    for i, line in enumerate(rows):
        ry = row_y + i * row_h
        if ry + body_font.get_height() < top or ry > bottom:
            continue
        if draw_brightness_slider and i == brightness_idx:
            _draw_brightness_slider_row(surface, int(ry), display_focus == i)
            continue
        if draw_vfr_opacity_slider and i == vfr_idx:
            _draw_vfr_opacity_slider_row(surface, int(ry), display_focus == i)
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
    return max_scroll


def _draw_brightness_slider_row(surface, ry: int, focused: bool) -> None:
    body_font = _display_font()
    track_w, slider_h, label_w, value_w = _brightness_slider_metrics()
    gap = theme.s(8)
    pct = settings.brightness_percent()
    lo = settings.BRIGHTNESS_MIN_PERCENT
    hi = settings.BRIGHTNESS_MAX_PERCENT
    block_w = label_w + gap + track_w + gap + value_w
    left_x = theme.CENTER_X - block_w // 2
    track_x = left_x + label_w + gap
    text_h = body_font.get_height()
    row_h = max(slider_h, text_h + theme.s(6))
    if focused:
        pad = theme.s(4)
        focus = pygame.Rect(
            left_x - pad,
            ry - pad,
            block_w + pad * 2,
            row_h + pad,
        )
        pygame.draw.rect(surface, theme.GRID, focus, max(1, theme.s(1)))
    label = body_font.render("Brightness", True, theme.MUTED)
    surface.blit(label, (left_x, int(ry + (row_h - text_h) // 2)))
    track_cy = int(ry + row_h // 2)
    track_rect = pygame.Rect(track_x, track_cy - max(2, theme.s(2)), track_w, max(4, theme.s(4)))
    pygame.draw.rect(surface, theme.HINT, track_rect, border_radius=theme.s(2))
    t = (pct - lo) / max(1, hi - lo)
    fill_w = int(round(t * track_w))
    if fill_w > 0:
        fill_rect = pygame.Rect(track_x, track_rect.y, fill_w, track_rect.height)
        pygame.draw.rect(surface, theme.SWEEP, fill_rect, border_radius=theme.s(2))
    knob_x = track_x + fill_w
    knob_r = max(5, theme.s(6))
    pygame.draw.circle(surface, theme.SWEEP, (knob_x, track_cy), knob_r)
    pygame.draw.circle(surface, theme.LABEL, (knob_x, track_cy), knob_r, max(1, theme.s(1)))
    value = body_font.render(f"{pct}%", True, theme.MUTED)
    surface.blit(
        value,
        (
            track_x + track_w + gap,
            int(ry + (row_h - text_h) // 2),
        ),
    )


def _draw_vfr_opacity_slider_row(surface, ry: int, focused: bool) -> None:
    body_font = _display_font()
    track_w, slider_h, label_w, value_w = _vfr_opacity_slider_metrics()
    gap = theme.s(8)
    pct = settings.vfr_map_opacity()
    lo = settings.VFR_OPACITY_MIN_PERCENT
    hi = settings.VFR_OPACITY_MAX_PERCENT
    block_w = label_w + gap + track_w + gap + value_w
    left_x = theme.CENTER_X - block_w // 2
    track_x = left_x + label_w + gap
    text_h = body_font.get_height()
    row_h = max(slider_h, text_h + theme.s(6))
    if focused:
        pad = theme.s(4)
        focus = pygame.Rect(
            left_x - pad,
            ry - pad,
            block_w + pad * 2,
            row_h + pad,
        )
        pygame.draw.rect(surface, theme.GRID, focus, max(1, theme.s(1)))
    label = body_font.render("VFR opacity", True, theme.MUTED)
    surface.blit(label, (left_x, int(ry + (row_h - text_h) // 2)))
    track_cy = int(ry + row_h // 2)
    track_rect = pygame.Rect(track_x, track_cy - max(2, theme.s(2)), track_w, max(4, theme.s(4)))
    pygame.draw.rect(surface, theme.HINT, track_rect, border_radius=theme.s(2))
    t = (pct - lo) / max(1, hi - lo)
    fill_w = int(round(t * track_w))
    if fill_w > 0:
        fill_rect = pygame.Rect(track_x, track_rect.y, fill_w, track_rect.height)
        pygame.draw.rect(surface, theme.SWEEP, fill_rect, border_radius=theme.s(2))
    knob_x = track_x + fill_w
    knob_r = max(5, theme.s(6))
    pygame.draw.circle(surface, theme.SWEEP, (knob_x, track_cy), knob_r)
    pygame.draw.circle(surface, theme.LABEL, (knob_x, track_cy), knob_r, max(1, theme.s(1)))
    value = body_font.render(f"{pct}%", True, theme.MUTED)
    surface.blit(
        value,
        (
            track_x + track_w + gap,
            int(ry + (row_h - text_h) // 2),
        ),
    )


def draw_info(surface, page: int, scroll_offset: int = 0, display_focus: int = 0) -> int:
    draw.fill_background(surface)
    nav.draw_breadcrumb(surface, _breadcrumb(page))
    nav.draw_page_dots(surface, page, len(nav.SETTINGS_PAGES))

    body_font = _display_font()
    top = nav.content_top_y(has_dots=True)
    bottom = nav.content_bottom_y()
    max_scroll = 0

    if page == PAGE_MAIN:
        try:
            from utilities.system_stats import format_lines as _system_stat_lines

            sys_lines = _system_stat_lines()
        except Exception:
            sys_lines = ["CPU: —", "RAM: —", "Temp: —"]
        lines = [
            f"IP: {_local_ip()}",
            f"Host: {_hostname()}.local",
            f"Web: {web_portal_url(_hostname())}",
            *sys_lines,
            f"Lat: {LOCATION_HOME[0]:.5f}",
            f"Lon: {LOCATION_HOME[1]:.5f}",
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
        max_scroll = _draw_settings_rows(
            surface,
            _display_row_labels(),
            scroll_offset,
            display_focus,
            top,
            bottom,
            draw_brightness_slider=True,
        )

    elif page == PAGE_OPTIONS:
        max_scroll = _draw_settings_rows(
            surface,
            _options_row_labels(),
            scroll_offset,
            display_focus,
            top,
            bottom,
            draw_vfr_opacity_slider=True,
        )

    else:
        active = settings.theme_index()
        custom = settings.theme_custom()
        rgb = settings.theme_rgb()
        swatch_size, row_h, max_label_w, block_w = _theme_row_metrics()
        swatch_gap = theme.s(8)
        track_w, slider_h, label_w, value_w = _theme_slider_metrics()
        top_pad, section_gap, heading_h = _theme_section_gaps()
        total_h = _theme_content_height()
        max_scroll = max(0, total_h - (bottom - top))

        y = top + top_pad - scroll_offset
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
            if i == active and not custom:
                pygame.draw.rect(surface, theme.GRID, row_rect, 1)
            swatch_y = int(ry + (row_h - swatch_size) // 2)
            text_y = int(ry + (row_h - text_h) // 2)
            swatch_rect = pygame.Rect(swatch_x, swatch_y, swatch_size, swatch_size)
            label = body_font.render(
                name, True, theme.LABEL if (i == active and not custom) else theme.MUTED
            )
            surface.blit(label, (label_x, text_y))
            pygame.draw.rect(surface, accent, swatch_rect)
            pygame.draw.rect(surface, palette["grid"], swatch_rect, max(1, theme.s(2)))

        # Custom RGB section under the presets.
        slider_gap = theme.s(8)
        section_y = y + color_presets.THEME_COUNT * row_h + section_gap
        heading = body_font.render(
            "Custom", True, theme.LABEL if custom else theme.MUTED
        )
        heading_x = theme.CENTER_X - heading.get_width() // 2
        if section_y + heading_h >= top and section_y <= bottom:
            surface.blit(heading, (heading_x, int(section_y + (heading_h - text_h) // 2)))
            # Live preview swatch next to the heading.
            preview = pygame.Rect(
                heading_x + heading.get_width() + theme.s(8),
                int(section_y + (heading_h - swatch_size) // 2),
                swatch_size,
                swatch_size,
            )
            pygame.draw.rect(surface, rgb, preview)
            pygame.draw.rect(surface, theme.GRID, preview, max(1, theme.s(1)))

        slider_y0 = section_y + heading_h
        block_w_s = label_w + slider_gap + track_w + slider_gap + value_w
        left_x = theme.CENTER_X - block_w_s // 2
        track_x = left_x + label_w + slider_gap
        channel_colors = ((220, 64, 64), (64, 180, 64), (64, 120, 220))
        channel_labels = ("R", "G", "B")
        for i, (ch, col) in enumerate(zip(channel_labels, channel_colors)):
            ry = slider_y0 + i * slider_h
            if ry + slider_h < top or ry > bottom:
                continue
            label = body_font.render(ch, True, theme.MUTED)
            surface.blit(
                label,
                (left_x, int(ry + (slider_h - text_h) // 2)),
            )
            track_cy = int(ry + slider_h // 2)
            track_rect = pygame.Rect(track_x, track_cy - max(2, theme.s(2)), track_w, max(4, theme.s(4)))
            pygame.draw.rect(surface, theme.HINT, track_rect, border_radius=theme.s(2))
            fill_w = int(round((rgb[i] / 255.0) * track_w))
            if fill_w > 0:
                fill_rect = pygame.Rect(track_x, track_rect.y, fill_w, track_rect.height)
                pygame.draw.rect(surface, col, fill_rect, border_radius=theme.s(2))
            knob_x = track_x + fill_w
            knob_r = max(5, theme.s(6))
            pygame.draw.circle(surface, col, (knob_x, track_cy), knob_r)
            pygame.draw.circle(surface, theme.LABEL, (knob_x, track_cy), knob_r, max(1, theme.s(1)))
            value = body_font.render(str(rgb[i]), True, theme.MUTED)
            surface.blit(
                value,
                (
                    track_x + track_w + slider_gap,
                    int(ry + (slider_h - text_h) // 2),
                ),
            )

    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))
    return max_scroll
