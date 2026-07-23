"""On-demand route enrichment for the flight detail screen.

Also used indirectly via overhead.py for pinned pre-departure tracked flights.
One AirLabs lookup per uncached callsign when flight detail is opened and
origin/destination are missing; FlightAware AeroAPI is a capped fallback.
"""

from __future__ import annotations

from utilities.airlabs import get_flight_schedule


def lookup_callsign(flight: dict | None) -> str:
    flight = flight or {}
    return (flight.get("callsign") or flight.get("flight_number") or "").strip().upper()


def _missing_route(value) -> bool:
    text = (value or "").strip()
    return not text or text == "—"


def needs_route_enrichment(flight: dict | None) -> bool:
    """True when the open flight detail row lacks a usable route."""
    if not flight:
        return False
    return _missing_route(flight.get("origin")) or _missing_route(flight.get("destination"))


def _from_airlabs(callsign: str) -> dict | None:
    sched = get_flight_schedule(callsign)
    if not sched:
        return None
    origin = sched.get("origin") or ""
    destination = sched.get("destination") or ""
    if not origin and not destination:
        return None
    return {
        "origin": origin,
        "destination": destination,
        "dep_time": sched.get("dep_time") or "",
        "arr_time": sched.get("arr_time") or "",
        "schedule_status": sched.get("status") or "",
        "route_source": "airlabs",
    }


def _from_flightaware(flight: dict, callsign: str) -> dict | None:
    try:
        from utilities.flightaware_client import lookup_route
    except Exception:
        return None
    # Prefer callsign; try registration when callsign is empty/unhelpful.
    candidates = []
    if callsign:
        candidates.append(callsign)
    reg = (flight.get("registration") or "").strip().upper()
    if reg and reg not in candidates:
        candidates.append(reg)
    for ident in candidates:
        result = lookup_route(ident)
        if not result:
            continue
        return {
            "origin": result.get("origin") or "",
            "destination": result.get("destination") or "",
            "dep_time": result.get("dep_time") or "",
            "arr_time": result.get("arr_time") or "",
            "schedule_status": result.get("status") or "",
            "route_source": "flightaware",
        }
    return None


def fetch_route_enrichment(flight: dict) -> dict | None:
    """AirLabs first, then FlightAware AeroAPI when still missing a route."""
    callsign = lookup_callsign(flight)
    if not callsign and not (flight.get("registration") or "").strip():
        return None

    airlabs = _from_airlabs(callsign) if callsign else None
    if airlabs and not (
        _missing_route(airlabs.get("origin")) and _missing_route(airlabs.get("destination"))
    ):
        # Accept if at least one end of the route is filled.
        if not _missing_route(airlabs.get("origin")) or not _missing_route(
            airlabs.get("destination")
        ):
            # If both ends present, done. If only one, still try FA to fill gaps.
            if not _missing_route(airlabs.get("origin")) and not _missing_route(
                airlabs.get("destination")
            ):
                return airlabs

    fa = _from_flightaware(flight, callsign)
    if not airlabs:
        return fa
    if not fa:
        return airlabs

    # Merge: fill missing ends from FA, keep AirLabs times when present.
    merged = dict(airlabs)
    for key in ("origin", "destination"):
        if _missing_route(merged.get(key)) and not _missing_route(fa.get(key)):
            merged[key] = fa[key]
            merged["route_source"] = "airlabs+flightaware"
    for key in ("dep_time", "arr_time", "schedule_status"):
        if not merged.get(key) and fa.get(key):
            merged[key] = fa[key]
    return merged


def merge_route_enrichment(flight: dict, cache: dict[str, dict]) -> dict:
    """Overlay cached enrichment onto a flight dict for display."""
    callsign = lookup_callsign(flight)
    enr = cache.get(callsign) if callsign else None
    if not enr:
        return flight
    merged = dict(flight)
    for key in ("origin", "destination", "dep_time", "arr_time", "schedule_status", "route_source"):
        value = enr.get(key)
        if not value:
            continue
        if key in ("origin", "destination"):
            if _missing_route(merged.get(key)):
                merged[key] = value
        elif not merged.get(key):
            merged[key] = value
    return merged
