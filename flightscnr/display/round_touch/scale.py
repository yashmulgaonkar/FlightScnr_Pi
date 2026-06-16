"""Radar range scale bands (FlightScnr radar_scale.h)."""

STATUTE_MILE_KM = 1.609344
LABEL_TO_COVERAGE = 4.0 / 3.0

SCALE_BANDS = [
    {"label_km": 2.0 * STATUTE_MILE_KM, "coverage_km": 2.0 * STATUTE_MILE_KM * LABEL_TO_COVERAGE},
    {"label_km": 4.0 * STATUTE_MILE_KM, "coverage_km": 4.0 * STATUTE_MILE_KM * LABEL_TO_COVERAGE},
    {"label_km": 6.0 * STATUTE_MILE_KM, "coverage_km": 6.0 * STATUTE_MILE_KM * LABEL_TO_COVERAGE},
    {"label_km": 8.0 * STATUTE_MILE_KM, "coverage_km": 8.0 * STATUTE_MILE_KM * LABEL_TO_COVERAGE},
]

_active_index = 1


def active_band():
    return SCALE_BANDS[_active_index]


def active_index():
    return _active_index


def cycle_next():
    """Advance to the next range band, wrapping to the smallest."""
    global _active_index
    _active_index = (_active_index + 1) % len(SCALE_BANDS)


def select(index: int):
    global _active_index
    _active_index = max(0, min(index, len(SCALE_BANDS) - 1))


def format_scale_tag(label_km: float, use_miles: bool) -> str:
    if use_miles:
        miles = label_km / STATUTE_MILE_KM
        if abs(miles - round(miles)) < 0.05:
            return f"{int(round(miles))}mi"
        return f"{miles:.1f}mi"
    if label_km >= 10:
        return f"{int(round(label_km))}km"
    return f"{label_km:.1f}km"


def format_active_tag(use_miles: bool) -> str:
    return format_scale_tag(active_band()["label_km"], use_miles)


def format_band_tag(index: int, use_miles: bool) -> str:
    idx = max(0, min(int(index), len(SCALE_BANDS) - 1))
    return format_scale_tag(SCALE_BANDS[idx]["label_km"], use_miles)


def index_for_radius_nm(radius_nm: float) -> int:
    """Scale band index that fits the configured search radius."""
    radius_km = radius_nm * 1.852
    best = len(SCALE_BANDS) - 1
    for i, band in enumerate(SCALE_BANDS):
        if band["coverage_km"] >= radius_km:
            best = i
            break
    return best
