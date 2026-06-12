"""Resolve marketing airline ICAO codes for logo display.

Regional operators (e.g. SkyWest SKW) often fly under major brands (United UAL,
Delta DAL). Logos should follow the ticketed airline, not the operating callsign.
"""

from __future__ import annotations

# Operators that fly for multiple marketing brands.
AMBIGUOUS_REGIONALS = {
    "RPA", "SKW", "ENY", "JIA", "EDV", "GJS", "CPZ", "ASQ", "PDT", "JZA",
    "CLH", "LHX", "DLA", "HOP", "KLC", "CFE", "ANE", "BCY", "EAI", "FCM", "GER",
}

# Marketing IATA prefix → brand display name
MARKETING_BRANDS = {
    "UA": "United Airlines",
    "AA": "American Airlines",
    "DL": "Delta Air Lines",
    "AS": "Alaska Airlines",
    "WN": "Southwest Airlines",
    "B6": "JetBlue Airways",
    "NK": "Spirit Airlines",
    "F9": "Frontier Airlines",
    "LH": "Lufthansa",
    "BA": "British Airways",
    "AF": "Air France",
    "KL": "KLM",
    "IB": "Iberia",
    "SK": "SAS",
    "EI": "Aer Lingus",
    "AY": "Finnair",
    "AC": "Air Canada",
}

# Marketing IATA prefix → ICAO code (logo filename)
IATA_TO_ICAO = {
    "AA": "AAL",
    "UA": "UAL",
    "DL": "DAL",
    "AS": "ASA",
    "WN": "SWA",
    "B6": "JBU",
    "NK": "NKS",
    "F9": "FFT",
    "LH": "DLH",
    "BA": "BAW",
    "AF": "AFR",
    "KL": "KLM",
    "IB": "IBE",
    "SK": "SAS",
    "EI": "EIN",
    "AY": "FIN",
    "AC": "ACA",
}


def _normalize(code: str) -> str:
    return (code or "").strip().upper()


def _iata_prefix(flight_id: str) -> str | None:
    """Return a 2-letter IATA prefix from IDs like UA5599."""
    fid = _normalize(flight_id)
    if len(fid) >= 3 and fid[:2].isalpha() and fid[2:3].isdigit():
        return fid[:2]
    return None


def _icao_prefix(flight_id: str) -> str | None:
    """Return a 3-letter ICAO prefix from IDs like UAL5599."""
    fid = _normalize(flight_id)
    if len(fid) >= 4 and fid[:3].isalpha() and fid[3:4].isdigit():
        return fid[:3]
    return None


def _marketing_icao_from_flight_id(flight_id: str) -> str | None:
    iata = _iata_prefix(flight_id)
    if iata:
        return IATA_TO_ICAO.get(iata)
    icao = _icao_prefix(flight_id)
    if icao and icao not in AMBIGUOUS_REGIONALS:
        return icao
    return None


def marketing_brand_name(flight_id: str) -> str:
    iata = _iata_prefix(flight_id)
    if iata:
        return MARKETING_BRANDS.get(iata, "")
    return ""


def display_flight_id(
    *,
    flight_number: str = "",
    callsign: str = "",
) -> str:
    """Return the passenger-facing flight ID (e.g. UA5796), not the operator callsign (SKW5796)."""
    fn = _normalize(flight_number)
    cs = _normalize(callsign)
    if fn:
        operator = _icao_prefix(cs) if cs else None
        if operator in AMBIGUOUS_REGIONALS:
            return fn
        if fn != cs and (_iata_prefix(fn) or _marketing_icao_from_flight_id(fn)):
            return fn
    return cs or fn or "—"


def display_flight_id_for_flight(flight: dict) -> str:
    return display_flight_id(
        flight_number=flight.get("flight_number") or flight.get("number") or "",
        callsign=flight.get("callsign") or "",
    )


def resolve_logo_icao(
    *,
    operator_icao: str = "",
    flight_number: str = "",
    callsign: str = "",
    airline_icao: str = "",
) -> str:
    """Pick the ICAO code used to load an airline logo PNG."""
    explicit = _normalize(airline_icao)
    if explicit and explicit not in AMBIGUOUS_REGIONALS and explicit != "N/A":
        return explicit

    operator = _normalize(operator_icao)
    if not operator:
        cs = _normalize(callsign)
        if len(cs) >= 3 and cs[:3].isalpha():
            operator = cs[:3]

    flight_num = _normalize(flight_number)
    if flight_num:
        marketing = _marketing_icao_from_flight_id(flight_num)
        if marketing:
            return marketing

    cs = _normalize(callsign)
    if cs:
        marketing = _marketing_icao_from_flight_id(cs)
        if marketing:
            return marketing

    if operator and operator not in ("", "N/A"):
        return operator

    return "default"
