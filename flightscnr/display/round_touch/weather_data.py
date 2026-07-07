"""Shared Tomorrow.io weather cache for clock and forecast screens."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

_CACHE: dict = {"ts": 0.0, "payload": None, "date": None}
_CACHE_TTL_S = 1800
_FAIL_RETRY_S = 120


def _today() -> date:
    return datetime.now().date()


def _interval_local_date(start: str) -> date | None:
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone().date()
        return dt.date()
    except ValueError:
        return None


def _weather_code_label(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "—"
    mapping = {
        1000: "Clear",
        1100: "Mostly clear",
        1101: "Partly cloudy",
        1102: "Mostly cloudy",
        1001: "Cloudy",
        4000: "Drizzle",
        4200: "Light rain",
        4001: "Rain",
        4201: "Heavy rain",
        5000: "Snow",
        5001: "Flurries",
        5100: "Light snow",
        5101: "Heavy snow",
        6000: "Freezing drizzle",
        6001: "Freezing rain",
        7000: "Ice pellets",
        8000: "Thunderstorm",
        2100: "Light fog",
        2000: "Fog",
        3000: "Light wind",
        3001: "Wind",
        3002: "Strong wind",
    }
    return mapping.get(code, "Weather")


def _fmt_time(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "—"
        if "T" in text:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone()
                return dt.strftime("%H:%M")
            except ValueError:
                pass
    try:
        ts = int(value)
        if ts > 1_000_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return "—"


def _parse_days(intervals: list, max_days: int = 3) -> list[dict]:
    days = []
    today = _today()
    for item in intervals:
        start = item.get("startTime") or ""
        day_date = _interval_local_date(start)
        if day_date is not None and day_date < today:
            continue
        values = item.get("values") or {}
        if day_date is not None:
            label = "Today" if day_date == today else day_date.strftime("%a")
        else:
            label = f"Day {len(days) + 1}"
        days.append(
            {
                "label": label,
                "temp_min": values.get("temperatureMin"),
                "temp_max": values.get("temperatureMax"),
                "weather_code": values.get("weatherCodeFullDay"),
                "weather_label": _weather_code_label(values.get("weatherCodeFullDay")),
                "precip_pct": values.get("precipitationProbabilityAvg"),
                "sunrise": _fmt_time(values.get("sunriseTime")),
                "sunset": _fmt_time(values.get("sunsetTime")),
            }
        )
        if len(days) >= max_days:
            break
    return days


def refresh(force: bool = False) -> dict | None:
    """Fetch temperature + forecast when clock/forecast screens are open."""
    global _CACHE
    now = time.time()
    try:
        from weather_prefs import temperature_units, unit_symbol
        from utilities.temperature import grab_forecast, grab_temperature_and_humidity
    except ImportError:
        try:
            from config import TEMPERATURE_UNITS
        except ImportError:
            TEMPERATURE_UNITS = "metric"

        def unit_symbol() -> str:
            return "F" if TEMPERATURE_UNITS == "imperial" else "C"

        def temperature_units() -> str:
            return "imperial" if TEMPERATURE_UNITS == "imperial" else "metric"

        from utilities.temperature import grab_forecast, grab_temperature_and_humidity

    units = unit_symbol()
    today = _today()
    cached = _CACHE.get("payload")
    if not force and cached and cached.get("unit") == units and _CACHE.get("date") == today:
        ttl = _CACHE_TTL_S if cached.get("ready") else _FAIL_RETRY_S
        if now - _CACHE["ts"] < ttl:
            return cached

    temp_hum = grab_temperature_and_humidity()
    intervals = grab_forecast("display")
    no_temp = (
        temp_hum is None
        or (
            isinstance(temp_hum, tuple)
            and len(temp_hum) >= 2
            and temp_hum[0] is None
            and temp_hum[1] is None
        )
    )
    if no_temp and not intervals:
        # Avoid retry storms when provider is rate-limiting or temporarily unavailable.
        payload = _CACHE.get("payload") or {
            "temp": None,
            "humidity": None,
            "unit": unit_symbol(),
            "days": [],
            "sunrise": "—",
            "sunset": "—",
            "weather_label": "—",
            "ready": False,
        }
        _CACHE["ts"] = now
        _CACHE["date"] = today
        _CACHE["payload"] = payload
        return payload

    temp, humidity = temp_hum if temp_hum else (None, None)
    days = _parse_days(intervals or [])
    current_code = days[0].get("weather_code") if days else None
    payload = {
        "temp": temp,
        "humidity": humidity,
        "unit": unit_symbol(),
        "days": days,
        "sunrise": days[0].get("sunrise") if days else "—",
        "sunset": days[0].get("sunset") if days else "—",
        "weather_label": _weather_code_label(current_code),
        "ready": temp is not None or bool(days),
    }
    _CACHE["ts"] = now
    _CACHE["date"] = today
    _CACHE["payload"] = payload
    return payload


def snapshot() -> dict | None:
    return _CACHE["payload"]


def invalidate_cache() -> None:
    global _CACHE
    _CACHE = {"ts": 0.0, "payload": None, "date": None}


def refresh_for_location_change() -> dict | None:
    """Fetch weather immediately after the radar center moves."""
    invalidate_cache()
    try:
        from utilities.temperature import reset_for_location_change

        reset_for_location_change()
    except ImportError:
        pass
    logger.info("Refreshing weather for new radar center")
    return refresh(force=True)
