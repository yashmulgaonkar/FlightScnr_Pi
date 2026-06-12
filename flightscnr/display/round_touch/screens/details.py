"""About / boot splash screen."""

from display.round_touch import draw, nav, theme

VERSION = "1.0.0"
FOOTER_BUTTONS = ("radar",)


def tap_footer_action(x: int, y: int) -> str | None:
    idx = nav.tap_footer_button(x, y, len(FOOTER_BUTTONS))
    if idx is None:
        return None
    return FOOTER_BUTTONS[idx]


def draw_details(surface, boot_splash=False, scroll_offset: int = 0) -> int:
    draw.fill_background(surface)
    body_font = draw.load_font(theme.FONT_BODY)
    top = nav.content_top_y()

    if boot_splash:
        y = theme.CENTER_Y - theme.s(60)
        y = draw.draw_center_line(surface, f"FlightScnr Pi v{VERSION}", y, body_font, theme.LABEL)
        y = draw.draw_center_line(surface, "UI by FlightScnr", y, body_font, theme.MUTED)
        draw.draw_center_line(surface, "Yash Mulgaonkar", y, body_font, theme.MUTED)
        return 0

    nav.draw_breadcrumb(surface, ["Radar", "About"])
    lines = [
        f"FlightScnr Pi v{VERSION}",
        "UI by FlightScnr",
        "Yash Mulgaonkar",
    ]
    max_scroll = nav.draw_lines_scrolled(surface, lines, body_font, theme.LABEL, scroll_offset, start_y=top)
    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))
    return max_scroll
