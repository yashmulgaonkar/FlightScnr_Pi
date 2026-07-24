"""Wildfire detail screen — CAL FIRE counties / acres / containment + map."""

from __future__ import annotations

from display.round_touch import aircraft_photos, draw, geo, nav, theme, wildfire_overlay
from display.round_touch.screens import common

FOOTER_BUTTONS = ("prev", "next", "radar")
FOOTER_EMPTY = ("radar",)


def footer_labels(fires) -> tuple[str, ...]:
    return FOOTER_BUTTONS if fires else FOOTER_EMPTY


def tap_footer_action(x: int, y: int, fires) -> str | None:
    labels = footer_labels(fires)
    idx = nav.tap_footer_button(x, y, len(labels))
    if idx is None:
        return None
    if not fires:
        return "radar"
    return ("prev", "next", "radar")[idx]


def _fmt_acres(acres) -> str:
    try:
        val = float(acres)
    except (TypeError, ValueError):
        return "—"
    if val >= 1000:
        return f"{val:,.0f} acres"
    if val >= 10:
        return f"{val:.0f} acres"
    return f"{val:.1f} acres"


def _fmt_containment(pct) -> str:
    try:
        val = float(pct)
    except (TypeError, ValueError):
        return "—"
    return f"{val:.0f}% contained"


def _fmt_started(started: str | None) -> str | None:
    if not started:
        return None
    text = started.strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    return f"Started {text}"


def _fire_rows(fire: dict, title_font, body_font, detail_font) -> list[tuple[str, object, tuple]]:
    name = (fire.get("name") or "Wildfire").strip()
    county = (fire.get("county") or "").strip()
    rows: list[tuple[str, object, tuple]] = [
        (name, title_font, theme.LABEL),
    ]
    if county:
        label = "Counties" if ("," in county or "&" in county) else "County"
        rows.append((f"{label}: {county}", body_font, theme.MUTED))
    else:
        rows.append(("Counties: —", body_font, theme.MUTED))

    acres = _fmt_acres(fire.get("acres"))
    contained = _fmt_containment(fire.get("containment"))
    if acres != "—" or contained != "—":
        rows.append((f"{acres} · {contained}", body_font, theme.LABEL))

    started = _fmt_started(fire.get("started"))
    if started:
        rows.append((started, detail_font, theme.MUTED))

    location = (fire.get("location") or "").strip()
    if location:
        rows.append((location, detail_font, theme.MUTED))

    try:
        dist = common.format_local_distance(
            geo.local_offset_km(fire["lat"], fire["lon"])[2]
        )
        rows.append((dist, detail_font, theme.MUTED))
    except Exception:
        pass

    admin = (fire.get("admin_unit") or "").strip()
    if admin:
        rows.append((admin, detail_font, theme.HINT))

    source = fire.get("source")
    if source == "calfire":
        rows.append(("CAL FIRE", detail_font, theme.HINT))
    elif source == "firms":
        conf = (fire.get("confidence") or "").strip()
        bit = f"NASA FIRMS · {conf}" if conf else "NASA FIRMS"
        rows.append((bit, detail_font, theme.HINT))

    return rows


def draw_fire_detail(surface, fires, selected_index, scroll_offset: int = 0) -> int:
    draw.fill_background(surface)
    title_font = draw.load_font(theme.s(18), bold=True)
    body_font = draw.load_font(theme.s(14))
    detail_font = draw.load_font(theme.s(13))
    chrome_top = nav.content_top_y(has_dots=True)
    line_gap = theme.s(1)
    bottom = nav.content_bottom_y()

    if not fires:
        nav.draw_breadcrumb(surface, ["Radar", "Fire"])
        nav.draw_footer_buttons(surface, list(FOOTER_EMPTY))
        common.draw_center_row(surface, "No wildfires", chrome_top, body_font, theme.MUTED)
        return 0

    idx = max(0, min(selected_index, len(fires) - 1))
    fire = fires[idx]
    crumb = (fire.get("name") or "Fire").strip()
    nav.draw_breadcrumb(surface, ["Radar", "Fire", crumb])
    nav.draw_page_dots(surface, idx, len(fires), active_color=theme.LABEL)

    map_path = (fire.get("map_path") or "").strip()
    has_map = bool(map_path)
    rows = _fire_rows(fire, title_font, body_font, detail_font)

    header_h = theme.s(112) if has_map else theme.s(36)
    rows_h = sum(font.get_height() + line_gap for _, font, _ in rows) - line_gap
    total_h = header_h + theme.s(4) + rows_h
    max_scroll = max(0, total_h - (bottom - chrome_top))

    y = chrome_top - scroll_offset
    if has_map:
        max_h = theme.s(108)
        max_w = int(theme.VISIBLE_RADIUS * 1.45)
        photo = aircraft_photos.load_photo_surface(
            map_path, max_h, max_w=max_w, radius=theme.s(8)
        )
        if photo is not None:
            rect = photo.get_rect(midtop=(theme.CENTER_X, int(y)))
            if rect.bottom > chrome_top and rect.top < bottom:
                surface.blit(photo, rect)
            y = rect.bottom + theme.s(3)
        else:
            y = _draw_fire_icon_header(surface, int(y))
    else:
        y = _draw_fire_icon_header(surface, int(y))

    for text, font, color in rows:
        h = font.get_height()
        if y >= chrome_top and y + h <= bottom:
            common.draw_center_row(surface, text, int(y), font, color)
        y += h + line_gap

    nav.draw_footer_buttons(surface, list(FOOTER_BUTTONS))
    return max_scroll


def _draw_fire_icon_header(surface, y: int) -> int:
    icon = wildfire_overlay.fire_icon(theme.s(28))
    if icon is None:
        return y
    rect = icon.get_rect(midtop=(theme.CENTER_X, y))
    surface.blit(icon, rect)
    return y + rect.height + theme.s(3)
