"""Drawing helpers for round FlightScnr-style screens."""

import math
import pygame

from display.round_touch import theme


_font_cache = {}


def load_font(size: int, bold=False) -> pygame.font.Font:
    from display.round_touch.ui_fonts import resolve_font_path

    key = (size, bold)
    if key not in _font_cache:
        path = resolve_font_path(bold=bold)
        if path:
            font = pygame.font.Font(path, size)
        else:
            fallback = pygame.font.match_font("dejavusans", bold=bold)
            if fallback:
                font = pygame.font.Font(fallback, size)
            else:
                font = pygame.font.SysFont(None, size, bold=bold)
        _font_cache[key] = font
    return _font_cache[key]


def circle_half_width_at_row(row_y: int, row_h: int) -> int:
    r = theme.VISIBLE_RADIUS
    if r <= 0 or row_h <= 0:
        return 0
    row_center = row_y + row_h // 2
    dy = row_center - theme.CENTER_Y
    if abs(dy) >= r:
        return 0
    half = math.sqrt(r * r - dy * dy)
    usable = int(half) - theme.s(6)
    return max(0, usable)


def fit_text(text: str, font: pygame.font.Font, max_width: int) -> str:
    if max_width <= 0 or not text:
        return text
    if font.size(text)[0] <= max_width:
        return text
    for n in range(len(text), 0, -1):
        trial = text[:n] + "…"
        if font.size(trial)[0] <= max_width:
            return trial
    return "…"


def draw_center_line(
    surface: pygame.Surface,
    text: str,
    y: int,
    font: pygame.font.Font,
    color,
    bg=None,
) -> int:
    h = font.get_height()
    max_w = circle_half_width_at_row(y, h) * 2
    line = fit_text(text, font, max_w)
    rendered = font.render(line, True, color, bg)
    rect = rendered.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(rendered, rect)
    return y + h + theme.s(4)


def draw_dashed_circle(surface, center, radius, color, width=2):
    """Draw a smooth dashed ring by sampling the arc every ~2 px."""
    if radius <= 0:
        return

    dash = max(1.0, float(theme.GRID_DASH_LEN))
    gap = max(1.0, float(theme.GRID_DASH_GAP))
    pattern = dash + gap
    cx, cy = center

    # Fine angular steps keep the ring circular instead of polygonal.
    steps = max(360, int(math.ceil(2 * math.pi * radius / 2.0)))
    angle_step = (2 * math.pi) / steps
    arc_step = angle_step * radius

    run = []
    arc_pos = 0.0

    def flush():
        if len(run) >= 2:
            pygame.draw.lines(surface, color, False, run, width)
        run.clear()

    for i in range(steps + 1):
        angle = i * angle_step
        in_dash = (arc_pos % pattern) < dash
        pt = (int(cx + radius * math.cos(angle)), int(cy + radius * math.sin(angle)))
        if in_dash:
            run.append(pt)
        elif run:
            flush()
        arc_pos += arc_step

    flush()


def draw_dashed_line(surface, start, end, color, width=2):
    """Draw a dashed line between two points using the grid dash pattern."""
    x0, y0 = start
    x1, y1 = end
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return

    dash = max(1.0, float(theme.GRID_DASH_LEN))
    gap = max(1.0, float(theme.GRID_DASH_GAP))
    pattern = dash + gap
    dx = (x1 - x0) / length
    dy = (y1 - y0) / length

    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        if seg_end > pos:
            pygame.draw.line(
                surface,
                color,
                (int(x0 + dx * pos), int(y0 + dy * pos)),
                (int(x0 + dx * seg_end), int(y0 + dy * seg_end)),
                width,
            )
        pos += pattern


def draw_sweep_line(surface, angle_deg: float, color, width=2):
    cx, cy = theme.CENTER_X, theme.CENTER_Y
    rad = math.radians(angle_deg - 90)
    x1 = int(cx + theme.SWEEP_RADIUS * math.cos(rad))
    y1 = int(cy + theme.SWEEP_RADIUS * math.sin(rad))
    pygame.draw.line(surface, color, (cx, cy), (x1, y1), width)


def draw_error(surface: pygame.Surface, message: str):
    """Show a persistent error screen instead of closing the display."""
    fill_background(surface)
    title = load_font(theme.FONT_TITLE, bold=True)
    body = load_font(theme.FONT_BODY)
    detail = load_font(theme.FONT_DETAIL)
    y = theme.CENTER_Y - theme.s(100)
    y = draw_center_line(surface, "Display Error", y, title, theme.TAG_ALT_DESCEND)
    y += theme.s(12)
    for line in _wrap_message(message, 40):
        y = draw_center_line(surface, line, y, body, theme.LABEL)
    y += theme.s(12)
    draw_center_line(surface, "Tap or swipe to return to radar", y, detail, theme.HINT)
    y += theme.s(8)
    draw_center_line(surface, "Check: journalctl -u flightscnr -f", y, detail, theme.MUTED)


def _wrap_message(text: str, width: int):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text[:width]]


def fill_background(surface: pygame.Surface):
    surface.fill(theme.BG)


def draw_timeout_ring(surface: pygame.Surface, remaining_fraction: float) -> None:
    """Countdown ring on the visible perimeter. 1.0 = full time left, 0.0 = expired."""
    remaining_fraction = max(0.0, min(1.0, remaining_fraction))
    if remaining_fraction <= 0:
        return

    cx, cy = theme.CENTER_X, theme.CENTER_Y
    width = max(2, theme.s(3))
    r = theme.VISIBLE_RADIUS - width // 2 - theme.s(2)

    pygame.draw.circle(surface, theme.SWEEP_TRAIL, (cx, cy), r, width)

    if remaining_fraction >= 0.999:
        pygame.draw.circle(surface, theme.SWEEP, (cx, cy), r, width)
        return

    start = -math.pi / 2
    sweep = 2 * math.pi * remaining_fraction
    steps = max(24, int(r * sweep / 2))
    points = [
        (cx + int(r * math.cos(start + sweep * i / steps)), cy + int(r * math.sin(start + sweep * i / steps)))
        for i in range(steps + 1)
    ]
    if len(points) >= 2:
        pygame.draw.lines(surface, theme.SWEEP, False, points, width)


_bezel_overlay = None
_bezel_key = None


def apply_round_bezel(surface: pygame.Surface):
    """Mask corners outside the round visible area, then draw the extent ring."""
    global _bezel_overlay, _bezel_key
    size = surface.get_size()
    key = (size, theme.CENTER_X, theme.CENTER_Y, theme.VISIBLE_RADIUS, theme.BG)
    if _bezel_overlay is None or _bezel_key != key:
        _bezel_overlay = pygame.Surface(size, pygame.SRCALPHA)
        _bezel_overlay.fill((*theme.BG, 255))
        pygame.draw.circle(
            _bezel_overlay,
            (0, 0, 0, 0),
            (theme.CENTER_X, theme.CENTER_Y),
            theme.VISIBLE_RADIUS,
        )
        _bezel_key = key
    surface.blit(_bezel_overlay, (0, 0))
    draw_visible_extent(surface)


def draw_visible_extent(surface: pygame.Surface):
    """White circle marking the edge of the round visible area."""
    pygame.draw.circle(
        surface,
        theme.LABEL,
        (theme.CENTER_X, theme.CENTER_Y),
        theme.VISIBLE_RADIUS,
        max(1, theme.s(2)),
    )
