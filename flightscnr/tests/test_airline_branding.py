import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utilities.airline_branding import display_flight_id, resolve_logo_icao


def test_skywest_united_flight_number():
    assert resolve_logo_icao(
        operator_icao="SKW",
        flight_number="UA5599",
        callsign="SKW5599",
    ) == "UAL"


def test_skywest_delta_flight_number():
    assert resolve_logo_icao(
        operator_icao="SKW",
        flight_number="DL1234",
        callsign="SKW1234",
    ) == "DAL"


def test_united_callsign():
    assert resolve_logo_icao(
        operator_icao="UAL",
        flight_number="UAL1095",
        callsign="UAL1095",
    ) == "UAL"


def test_iata_callsign():
    assert resolve_logo_icao(
        operator_icao="",
        flight_number="",
        callsign="UA353",
    ) == "UAL"


def test_display_flight_id_skywest_united():
    assert display_flight_id(flight_number="UA5796", callsign="SKW5796") == "UA5796"


def test_display_flight_id_direct_carrier():
    assert display_flight_id(flight_number="UAL1684", callsign="UAL1684") == "UAL1684"
