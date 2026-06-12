"""Navigation chrome — breadcrumbs, page dots, scroll regions, footer hints."""

from __future__ import annotations

import math

import pygame

from display.round_touch import buttons, draw, theme

# Footer button chrome (radar-green palette)
_BTN_FILL = (8, 38, 14)
_BTN_FILL_ACCENT = (12, 52, 22)
_BTN_BORDER = theme.GRID
_BTN_BORDER_ACCENT = theme.SWEEP
_BTN_ICON = theme.LABEL
_BTN_ICON_ACCENT = theme.SWEEP

# Settings sub-page labels (must match info.py page constants)
SETTINGS_PAGES = ("Main", "Display", "Theme")


class ScrollState:
    def __init__(self):
        self.offset = 0
        self.max_offset = 0

    def reset(self):
        self.offset = 0
        self.max_offset = 0

    def clamp(self):
        self.offset = max(0, min(self.offset, self.max_offset))

    def step(self, delta: int):
        self.offset += delta
        self.clamp()


def _top_y() -> int:
    # Top of the round dial — stay off the rim where horizontal space is tight.
    return theme.CENTER_Y - int(theme.VISIBLE_RADIUS * 0.75) # 0.68, 0.72 higher, 0.62 lower


def _footer_top_y() -> int:
    return theme.CENTER_Y + int(theme.VISIBLE_RADIUS * 0.59)


def _footer_button_height() -> int:
    return theme.s(40)


def _footer_band(y_offset: int = 0, button_height: int | None = None) -> tuple[int, int]:
    """Return (top_y, band_height) for the footer button row."""
    btn_h = button_height or _footer_button_height()
    pad = theme.s(6)
    center_y = theme.CENTER_Y + int(theme.VISIBLE_RADIUS * 0.62) + y_offset
    top = center_y - btn_h // 2 - pad // 2
    return top, btn_h + pad


def _max_text_width(y: int, font_height: int) -> int:
    return max(40, draw.circle_half_width_at_row(y, font_height) * 2 - theme.s(8))


def _fit_breadcrumb_parts(parts: list[str], font: pygame.font.Font, max_w: int) -> list[str]:
    sep = " › "
    if not parts:
        return parts
    for start in range(len(parts)):
        trial = parts[start:]
        while trial:
            line = sep.join(trial)
            if font.size(line)[0] <= max_w:
                return trial
            if len(trial) <= 1:
                return [draw.fit_text(trial[0], font, max_w)]
            trial = trial[1:]
    return [draw.fit_text(parts[-1], font, max_w)]


def content_top_y(has_dots: bool = False) -> int:
    if has_dots:
        return _top_y() + theme.s(28) + theme.s(10)
    return _top_y() + theme.s(36)


def content_bottom_y(footer_y_offset: int = 0) -> int:
    top, _ = _footer_band(footer_y_offset)
    return top - theme.s(10)


def scroll_step() -> int:
    return theme.s(36)


def draw_breadcrumb(
    surface: pygame.Surface,
    parts: list[str],
    *,
    active_color=None,
):
    if not parts:
        return
    active = active_color if active_color is not None else theme.SWEEP
    font = draw.load_font(theme.FONT_DETAIL)
    sep_str = " › "
    sep = font.render(sep_str, True, theme.HINT)
    y = _top_y()
    h = font.get_height()
    max_w = _max_text_width(y, h)
    display = _fit_breadcrumb_parts(parts, font, max_w)

    rendered = []
    total_w = 0
    for i, part in enumerate(display):
        color = active if i == len(display) - 1 else theme.MUTED
        used = total_w + (sep.get_width() if rendered else 0)
        remaining = max(20, max_w - used)
        text = draw.fit_text(part, font, remaining)
        img = font.render(text, True, color)
        rendered.append(img)
        total_w += img.get_width()
        if i < len(display) - 1:
            total_w += sep.get_width()

    if total_w > max_w:
        line = draw.fit_text(sep_str.join(parts), font, max_w)
        img = font.render(line, True, theme.MUTED)
        surface.blit(img, img.get_rect(midtop=(theme.CENTER_X, y)))
        return

    x = theme.CENTER_X - total_w // 2
    for i, img in enumerate(rendered):
        surface.blit(img, (x, y))
        x += img.get_width()
        if i < len(rendered) - 1:
            surface.blit(sep, (x, y))
            x += sep.get_width()


def draw_page_dots(
    surface: pygame.Surface,
    active: int,
    total: int,
    y: int | None = None,
    *,
    active_color=None,
):
    if total <= 1:
        return
    active_dot = active_color if active_color is not None else theme.SWEEP
    if y is None:
        y = _top_y() + theme.s(30)
    gap = theme.s(14)
    r = max(2, theme.s(4))
    span = (total - 1) * gap
    x0 = theme.CENTER_X - span // 2
    for i in range(total):
        cx = x0 + i * gap
        color = active_dot if i == active else theme.PAGE_DOT_INACTIVE
        pygame.draw.circle(surface, color, (cx, y), r)


def draw_footer(surface: pygame.Surface, hints: list[str]):
    if not hints:
        return
    font = draw.load_font(theme.FONT_DETAIL)
    y = _footer_top_y()
    h = font.get_height()
    max_w = _max_text_width(y, h)
    slot_w = max_w // len(hints)
    rendered = []
    for hint in hints:
        text = draw.fit_text(hint, font, max(20, slot_w - theme.s(4)))
        rendered.append(font.render(text, True, theme.HINT))
    total_w = sum(img.get_width() for img in rendered)
    spacing = max(theme.s(8), (max_w - total_w) // max(1, len(hints) - 1))
    x = theme.CENTER_X - (total_w + spacing * (len(hints) - 1)) // 2
    for img in rendered:
        surface.blit(img, (x, y))
        x += img.get_width() + spacing


def footer_button_rects(
    button_count: int,
    *,
    y_offset: int = 0,
    button_size: int | None = None,
    button_gap: int | None = None,
) -> list[pygame.Rect]:
    """Equal-width footer tap targets, left to right."""
    if button_count <= 0:
        return []
    top, band_h = _footer_band(y_offset, button_size)
    btn_h = button_size or _footer_button_height()
    gap = button_gap if button_gap is not None else theme.s(10)
    y = top + (band_h - btn_h) // 2
    max_w = _max_text_width(y + btn_h // 2, btn_h)
    total_gap = gap * max(0, button_count - 1)
    if button_size:
        btn_w = btn_h
        total_w = btn_w * button_count + total_gap
        if total_w > max_w:
            btn_w = max(theme.s(28), (max_w - total_gap) // button_count)
            btn_h = btn_w
    else:
        btn_w = (max_w - total_gap) // button_count
        btn_w = min(btn_w, theme.s(78))
    total_w = btn_w * button_count + total_gap
    x0 = theme.CENTER_X - total_w // 2
    return [
        pygame.Rect(x0 + i * (btn_w + gap), y, btn_w, btn_h)
        for i in range(button_count)
    ]


def _draw_round_button(
    surface: pygame.Surface,
    rect: pygame.Rect,
    *,
    accent: bool = False,
):
    radius = max(theme.s(8), rect.height // 4)
    fill = _BTN_FILL_ACCENT if accent else _BTN_FILL
    border = _BTN_BORDER_ACCENT if accent else _BTN_BORDER
    width = max(1, theme.s(2) if accent else theme.s(1))

    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    pygame.draw.rect(surface, border, rect, width=width, border_radius=radius)

    shine = rect.inflate(-theme.s(6), -theme.s(10))
    if shine.width > 0 and shine.height > 0:
        shine_color = (18, 70, 28) if accent else (14, 52, 22)
        pygame.draw.rect(surface, shine_color, shine, border_radius=max(2, radius - theme.s(2)))


def _draw_nav_arrow(surface: pygame.Surface, center: tuple[int, int], size: int, color, left: bool):
    """Solid triangular arrow — tip left for prev, tip right for next."""
    cx, cy = center
    half_h = size
    reach = size + theme.s(2)
    if left:
        pts = [(cx - reach, cy), (cx + reach // 2, cy - half_h), (cx + reach // 2, cy + half_h)]
    else:
        pts = [(cx + reach, cy), (cx - reach // 2, cy - half_h), (cx - reach // 2, cy + half_h)]
    pygame.draw.polygon(surface, color, pts)


def _draw_radar_icon(surface: pygame.Surface, center: tuple[int, int], radius: int, color):
    cx, cy = center
    r = max(4, radius)
    pygame.draw.circle(surface, color, (cx, cy), r, max(1, theme.s(2)))
    pygame.draw.circle(surface, _BTN_BORDER, (cx, cy), max(2, r * 2 // 3), 1)
    pygame.draw.line(surface, _BTN_BORDER, (cx - r, cy), (cx + r, cy), 1)
    pygame.draw.line(surface, _BTN_BORDER, (cx, cy - r), (cx, cy + r), 1)
    sweep_rad = math.radians(-35)
    sx = cx + int(r * math.cos(sweep_rad))
    sy = cy + int(r * math.sin(sweep_rad))
    pygame.draw.line(surface, theme.SWEEP, (cx, cy), (sx, sy), max(2, theme.s(2)))
    blip_x = cx + r // 3
    blip_y = cy - r // 4
    pygame.draw.circle(surface, theme.AIRCRAFT, (blip_x, blip_y), max(2, theme.s(3)))


def _draw_pin_icon(
    surface: pygame.Surface,
    center: tuple[int, int],
    size: int,
    color,
    *,
    active: bool = False,
):
    """Map-pin icon — filled head and point, readable at small sizes."""
    del color
    cx, cy = center
    s = max(11, size)
    head_r = max(4, int(s * 0.4))
    head_cy = cy - int(s * 0.28)
    tip_y = cy + int(s * 0.46)
    w = max(1, theme.s(2))

    if active:
        fill = theme.SWEEP
        pygame.draw.circle(surface, fill, (cx, head_cy), head_r)
        pts = [
            (cx - head_r + 1, head_cy + head_r - 1),
            (cx + head_r - 1, head_cy + head_r - 1),
            (cx, tip_y),
        ]
        pygame.draw.polygon(surface, fill, pts)
        pygame.draw.circle(surface, _BTN_FILL, (cx, head_cy), max(2, head_r // 3))
        return

    fill = _BTN_FILL_ACCENT
    outline = theme.SWEEP
    pygame.draw.circle(surface, fill, (cx, head_cy), head_r)
    pygame.draw.circle(surface, outline, (cx, head_cy), head_r, w)
    pts = [
        (cx - head_r + 1, head_cy + head_r - 1),
        (cx + head_r - 1, head_cy + head_r - 1),
        (cx, tip_y),
    ]
    pygame.draw.polygon(surface, fill, pts)
    pygame.draw.polygon(surface, outline, pts, w)
    pygame.draw.circle(surface, fill, (cx, head_cy), max(2, head_r // 4))
    pygame.draw.circle(surface, outline, (cx, head_cy), max(2, head_r // 4), 1)


def _draw_footer_button(
    surface: pygame.Surface, rect: pygame.Rect, kind: str, *, active: bool = False
):
    draw_w, draw_h = buttons.button_draw_size(kind, rect.width, rect.height)
    png = buttons.load_button_surface(
        kind,
        draw_w,
        draw_h,
        active=active,
    )
    if png is not None:
        surface.blit(png, png.get_rect(center=rect.center))
        return

    accent = kind == "radar" or (kind == "pin" and active)
    _draw_round_button(surface, rect, accent=accent)
    icon_color = _BTN_ICON_ACCENT if accent else _BTN_ICON
    label_font = draw.load_font(theme.s(11))
    labels = {"prev": "PREV", "next": "NEXT", "radar": "RADAR", "pin": "PIN IT"}
    label = labels.get(kind, kind.upper())

    icon_cy = rect.centery - theme.s(6)
    icon_size = theme.s(10) if kind == "pin" else theme.s(7)
    if kind == "prev":
        _draw_nav_arrow(surface, (rect.centerx, icon_cy), icon_size, icon_color, left=True)
    elif kind == "next":
        _draw_nav_arrow(surface, (rect.centerx, icon_cy), icon_size, icon_color, left=False)
    elif kind == "radar":
        _draw_radar_icon(surface, (rect.centerx, icon_cy), icon_size, icon_color)
    elif kind == "pin":
        _draw_pin_icon(
            surface,
            (rect.centerx, icon_cy),
            icon_size,
            theme.SWEEP,
            active=active,
        )

    label_color = theme.SWEEP if accent else theme.HINT
    text = draw.fit_text(label, label_font, rect.width - theme.s(6))
    rendered = label_font.render(text, True, label_color)
    surface.blit(rendered, rendered.get_rect(midtop=(rect.centerx, icon_cy + theme.s(10))))


def draw_footer_buttons(
    surface: pygame.Surface,
    kinds: list[str],
    *,
    y_offset: int = 0,
    button_size: int | None = None,
    button_gap: int | None = None,
    pin_active: bool = False,
):
    """Draw tappable footer buttons. Kinds: prev, next, radar, pin."""
    if not kinds:
        return
    rects = footer_button_rects(
        len(kinds),
        y_offset=y_offset,
        button_size=button_size,
        button_gap=button_gap,
    )
    for kind, rect in zip(kinds, rects):
        _draw_footer_button(
            surface,
            rect,
            kind,
            active=(kind == "pin" and pin_active),
        )


def tap_footer_button(
    x: int,
    y: int,
    button_count: int,
    *,
    y_offset: int = 0,
    button_size: int | None = None,
    button_gap: int | None = None,
) -> int | None:
    """Return tapped footer button index (0=left), or None."""
    rects = footer_button_rects(
        button_count,
        y_offset=y_offset,
        button_size=button_size,
        button_gap=button_gap,
    )
    for i, rect in enumerate(rects):
        if rect.collidepoint(x, y):
            return i
    return None


def breadcrumb_rect() -> pygame.Rect:
    font = draw.load_font(theme.FONT_DETAIL)
    y = _top_y()
    h = font.get_height()
    half_w = draw.circle_half_width_at_row(y, h)
    return pygame.Rect(
        theme.CENTER_X - half_w,
        y - theme.s(4),
        half_w * 2,
        h + theme.s(8),
    )


def tap_breadcrumb(x: int, y: int) -> bool:
    """Tap the breadcrumb bar to go back toward Radar."""
    return breadcrumb_rect().collidepoint(x, y)


def measure_lines(lines: list[str], font: pygame.font.Font, gap: int | None = None) -> int:
    if not lines:
        return 0
    gap = theme.s(4) if gap is None else gap
    return len(lines) * (font.get_height() + gap) - gap


def draw_lines_scrolled(
    surface: pygame.Surface,
    lines: list[str],
    font: pygame.font.Font,
    color,
    scroll_offset: int,
    *,
    start_y: int | None = None,
    top: int | None = None,
    bottom: int | None = None,
    gap: int | None = None,
    center: bool = True,
) -> int:
    """Draw lines in the content band; return max scroll offset."""
    gap = theme.s(4) if gap is None else gap
    top = content_top_y() if top is None else top
    bottom = content_bottom_y() if bottom is None else bottom
    start_y = top if start_y is None else start_y
    viewport_h = max(0, bottom - top)
    total_h = measure_lines(lines, font, gap)
    max_scroll = max(0, total_h - viewport_h)

    y = start_y - scroll_offset
    row_h = font.get_height() + gap
    for line in lines:
        if top - row_h <= y <= bottom:
            if center:
                draw.draw_center_line(surface, line, y, font, color)
            else:
                max_w = draw.circle_half_width_at_row(y, font.get_height()) * 2
                text = draw.fit_text(line, font, max_w)
                rendered = font.render(text, True, color)
                surface.blit(rendered, rendered.get_rect(midtop=(theme.CENTER_X, y)))
        y += row_h
    return max_scroll
