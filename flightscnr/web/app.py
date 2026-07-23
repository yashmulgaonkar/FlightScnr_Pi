#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request, redirect
import json
import os
import sys

# Ensure the parent directory is on sys.path so `config` and `utilities` resolve
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import (
    WEB_PORT,
    format_location_home,
    location_configured,
    parse_lat_lon_pair,
    reload_location_override,
    set_location_home,
)
from utilities.fr24_client import FR24Client

# Singleton FR24Client shared across all web requests (shares cache + rate limiter)
_fr24_client = FR24Client()

# /web is the folder that this file lives in
WEB_DIR = os.path.dirname(__file__)

app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static")
)

# Writable data directory (same as overhead.py uses)
DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
CLOSEST_FILE  = os.path.join(DATA_DIR, "close.txt")
FARTHEST_FILE = os.path.join(DATA_DIR, "farthest.txt")
TRACKED_FILE  = os.path.join(DATA_DIR, "tracked_flight.json")
MAPS_DIR      = os.path.join(DATA_DIR, "maps")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load {path}: {e}")
        return default


def _counter_file() -> str:
    return os.path.join(DATA_DIR, "flight_counter.json")


def _normalize_counter_log(raw) -> dict:
    """Return date-keyed counter log (handles legacy flat format)."""
    if not isinstance(raw, dict) or not raw:
        return {}
    if "date" in raw and "callsigns" in raw:
        day = raw["date"]
        return {
            day: {
                "date": day,
                "count": raw.get("count", len(raw.get("callsigns", []))),
                "flights": [
                    {"callsign": c, "time": "00:00:00", "hour": 0}
                    for c in raw.get("callsigns", [])
                ],
                "first_seen": "",
                "last_seen": "",
            }
        }
    return raw


def _load_counter_log() -> dict:
    return _normalize_counter_log(load_json(_counter_file(), {}))


def _counter_summary(log: dict) -> list[dict]:
    summary = []
    for day, data in sorted(log.items()):
        by_hour = [0] * 24
        for flight in data.get("flights", []):
            by_hour[flight.get("hour", 0)] += 1
        summary.append({
            "date": day,
            "count": data.get("count", len(data.get("flights", []))),
            "by_hour": by_hour,
            "first_seen": data.get("first_seen", ""),
            "last_seen": data.get("last_seen", ""),
        })
    return summary


def lookup_flight(callsign):
    """
    Try to find a live flight by callsign, flight number, or registration.
    Returns a dict with found=True/False and flight info if found.
    """
    original = callsign.strip().upper()
    callsign = original

    from utilities.aircraft_alert import looks_like_registration
    from utilities.overhead import IATA_TO_ICAO

    # Convert IATA (UA353) to ICAO (UAL353) — skip for tail numbers
    if not looks_like_registration(callsign):
        if len(callsign) >= 3 and callsign[:2] in IATA_TO_ICAO and callsign[2:3].isdigit():
            icao_prefix = IATA_TO_ICAO.get(callsign[:2])
            if icao_prefix:
                callsign = icao_prefix + callsign[2:]

    try:
        api = _fr24_client

        match = None
        if looks_like_registration(original):
            match = api.find_by_registration(original)
            if not match:
                match = api.find_by_callsign(callsign)
        else:
            match = api.find_by_callsign(callsign)
            if not match:
                match = api.find_by_registration(original)

        if not match:
            return {"found": False}

        airline = ""
        origin = "???"
        destination = "???"
        resolved_cs = (match.callsign or "").strip().upper() or callsign
        registration = (match.registration or "").strip().upper()
        number = match.number or resolved_cs
        # Keep the user's tail number as the track token when they entered a reg.
        track_as = original if looks_like_registration(original) else resolved_cs

        # Details are nice-to-have — a live position match is enough to track.
        try:
            details = api.get_flight_details(match)
            match.set_flight_details(details)
            airline = match.airline_name or ""
            origin = match.origin_airport_iata or "???"
            destination = match.destination_airport_iata or "???"
            number = match.number or resolved_cs
            registration = (match.registration or registration).strip().upper()
            resolved_cs = (match.callsign or "").strip().upper() or resolved_cs
        except Exception as detail_exc:
            print(f"Lookup details unavailable for {track_as}: {detail_exc}")
            airline = match.airline_name or ""
            origin = match.origin_airport_iata or "???"
            destination = match.destination_airport_iata or "???"

        summary = f"{airline} {number} {origin}→{destination}".strip()
        if registration and looks_like_registration(original):
            summary = f"{registration} · {summary}".strip(" ·")

        return {
            "found": True,
            "callsign": resolved_cs,
            "registration": registration,
            "track_as": track_as,
            "number": number,
            "airline": airline,
            "origin": origin,
            "destination": destination,
            "summary": summary,
        }

    except Exception as e:
        print(f"Lookup error: {e}")
        return {"found": False, "error": str(e)}


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(WEB_DIR, "static"), "favicon.ico", mimetype="image/x-icon")


def _wifi_portal_active() -> bool:
    try:
        from utilities import wifi_setup

        return wifi_setup.setup_mode_active() or wifi_setup.needs_wifi_setup()
    except Exception:
        return False


# Phone OS captive-portal probes are covered by the blanket redirect below.


@app.before_request
def _captive_wifi_gateway():
    if not _wifi_portal_active():
        return None
    path = request.path or "/"
    if path.startswith("/wifi") or path.startswith("/static") or path == "/favicon.ico":
        return None
    if request.method in ("GET", "HEAD"):
        return redirect("/wifi")
    return None


@app.get("/wifi")
def wifi_setup_page():
    return render_template("wifi_setup.html")


@app.get("/wifi/status.json")
def wifi_status_json():
    from utilities import wifi_setup

    creds = wifi_setup.get_ap_credentials()
    return jsonify(
        {
            "setup_active": wifi_setup.setup_mode_active(),
            "needs_setup": wifi_setup.needs_wifi_setup(),
            "client_connected": wifi_setup.active_client_wifi(),
            "ap_ssid": creds.ssid,
            "portal_url": creds.portal_url,
            "status": wifi_setup.status_message(),
            "error": wifi_setup.last_error(),
        }
    )


@app.get("/wifi/networks.json")
def wifi_networks_json():
    from utilities import wifi_setup

    rescan = str(request.args.get("rescan", "1")).lower() not in ("0", "false", "no")
    return jsonify({"networks": wifi_setup.list_wifi_networks(rescan=rescan)})


@app.post("/wifi/connect")
def wifi_connect():
    from utilities import wifi_setup

    data = request.get_json(silent=True) or {}
    ssid = str(data.get("ssid") or "").strip()
    password = str(data.get("password") or "")
    ok, message = wifi_setup.connect_to_wifi(ssid, password)
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message}), code


@app.get("/")
def index():
    if _wifi_portal_active():
        return redirect("/wifi")
    return render_template("index.html")


@app.get("/closest/json")
def closest_json():
    return jsonify(load_json(CLOSEST_FILE, []))


@app.get("/farthest/json")
def farthest_json():
    return jsonify(load_json(FARTHEST_FILE, []))


@app.get("/closest")
def closest_page():
    return render_template("closest_map.html")


@app.get("/farthest")
def farthest_page():
    return render_template("farthest_map.html")


@app.get("/tracked/json")
def tracked_json():
    return jsonify(load_json(TRACKED_FILE, {"callsign": ""}))


@app.post("/tracked/lookup")
def tracked_lookup():
    """Live lookup — check if a flight is currently findable before saving."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"found": False, "error": "Invalid request"}), 400
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})
    result = lookup_flight(callsign)
    return jsonify(result)


@app.get("/location/json")
def location_json():
    reload_location_override()
    if not location_configured():
        return jsonify({"location": "", "configured": False})
    return jsonify({
        "location": format_location_home(),
        "configured": True,
    })


@app.post("/location/set")
def location_set():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"message": "Invalid request"}), 400
    raw = data.get("location", "").strip()
    if not raw:
        return jsonify({"message": "Enter coordinates as latitude, longitude"}), 400
    try:
        lat, lon = parse_lat_lon_pair(raw)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    try:
        set_location_home(lat, lon)
        # Force weather + timezone for the new center (display also refreshes
        # when it picks up location.json).
        try:
            from display.round_touch import weather_data

            weather_data.after_radar_center_changed(lat, lon)
        except Exception:
            print("Weather/timezone refresh after location save failed")
        try:
            from display.round_touch import map_bg, rainviewer_overlay

            map_bg.invalidate()
            map_bg.request_background()
            rainviewer_overlay.invalidate()
            rainviewer_overlay.request_overlay()
        except Exception:
            print("Map/precip refresh after location save failed")
        return jsonify({
            "message": f"Radar center saved: {format_location_home()}",
            "location": format_location_home(),
        })
    except Exception as e:
        return jsonify({"message": f"Error saving location: {e}"}), 500


@app.post("/tracked/set")
def tracked_set():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"message": "Invalid request"}), 400
    callsign = data.get("callsign", "").strip().upper()[:12]
    try:
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump({"callsign": callsign}, f)
        try:
            os.chmod(TRACKED_FILE, 0o666)
        except OSError:
            pass
        msg = f"Now tracking {callsign}." if callsign else "Tracking cleared."
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"message": f"Error saving: {e}"}), 500


@app.post("/route/search")
def route_search():
    """Search for live flights by origin→destination using gRPC server-side filter."""
    import re
    data = request.get_json(force=True)
    if not data:
        return jsonify({"flights": [], "error": "Invalid request"}), 400
    origin = data.get("origin", "").strip().upper()
    destination = data.get("destination", "").strip().upper()
    if not origin or not destination:
        return jsonify({"flights": [], "error": "Origin and destination required"}), 400
    if not re.match(r'^[A-Z]{3,4}$', origin) or not re.match(r'^[A-Z]{3,4}$', destination):
        return jsonify({"flights": [], "error": "Airport codes must be 3-4 letters"}), 400
    try:
        matches = _fr24_client.find_by_route(origin, destination)
        results = []
        for m in matches[:50]:  # limit to 50 results
            results.append({
                "callsign": m.callsign or "N/A",
                "number": m.number or m.callsign or "N/A",
                "airline": m.airline_name or "",
                "aircraft": m.aircraft_code or "N/A",
                "altitude": m.altitude or 0,
                "speed": m.ground_speed or 0,
            })
        return jsonify({"flights": results})
    except Exception as e:
        print(f"Route search error: {e}")
        return jsonify({"flights": [], "error": str(e)}), 500


@app.get("/stats")
def stats_page():
    """Flight counter stats dashboard."""
    return render_template("stats.html")


@app.get("/stats/<date>")
def stats_day_page(date):
    """Per-day stats drill-down."""
    return render_template("stats_day.html", date=date)


@app.get("/counter")
def flight_counter():
    """Full flight counter log (date-keyed)."""
    return jsonify(_load_counter_log())


@app.get("/counter/summary")
def flight_counter_summary():
    """Daily summary stats for the statistics dashboard."""
    return jsonify(_counter_summary(_load_counter_log()))


@app.get("/airport-code")
def airport_code():
    """Nearest airport / journey code for local vs flyover stats."""
    reload_location_override()
    try:
        from config import JOURNEY_CODE_SELECTED, LOCATION_HOME
        code = (JOURNEY_CODE_SELECTED or "").strip().upper()
        lat, lon = LOCATION_HOME[0], LOCATION_HOME[1]
    except Exception:
        code = ""
        lat, lon = None, None

    location_name = ""
    if lat is not None and lon is not None:
        try:
            import requests as _req
            r = _req.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 13},
                headers={"User-Agent": "FlightScnrPi/1.0"},
                timeout=5,
            )
            if r.status_code == 200:
                addr = r.json().get("address", {})
                neighbourhood = (
                    addr.get("neighbourhood")
                    or addr.get("suburb")
                    or addr.get("quarter")
                    or addr.get("village")
                )
                city = addr.get("city") or addr.get("town") or addr.get("county")
                if neighbourhood and city:
                    location_name = f"{neighbourhood}, {city}"
                elif city:
                    location_name = city
        except Exception as e:
            print(f"Reverse geocode failed: {e}")

    return jsonify({"code": code, "name": location_name})


@app.get("/alerts/json")
def alerts_json():
    from display.round_touch import alert_prefs

    alert_prefs.reload()
    return jsonify(
        {
            "alert_military": alert_prefs.military_enabled(),
            "alert_emergency": alert_prefs.emergency_enabled(),
            "alert_hide_non_alerted": alert_prefs.hide_non_alerted(),
            "alert_watch": alert_prefs.watch_blob(),
            "alert_watch_types": alert_prefs.watch_types_blob(),
        }
    )


@app.post("/alerts")
def alerts_save():
    from display.round_touch import alert_prefs

    data = request.get_json(silent=True) or {}
    alert_prefs.update(
        alert_military=bool(data.get("alert_military", False)),
        alert_emergency=bool(data.get("alert_emergency", False)),
        alert_hide_non_alerted=bool(data.get("alert_hide_non_alerted", False)),
        alert_watch=str(data.get("alert_watch", "") or ""),
        alert_watch_types=str(data.get("alert_watch_types", "") or ""),
    )
    return jsonify({"ok": True})


@app.get("/api-keys/json")
def api_keys_json():
    from secrets_store import secrets_status

    return jsonify(secrets_status())


@app.post("/api-keys")
def api_keys_save():
    from secrets_store import request_service_restart, save_secrets_from_portal, secrets_status

    data = request.get_json(silent=True) or {}
    save_secrets_from_portal(data)
    restarted = False
    if data.get("restart"):
        restarted = request_service_restart()
    return jsonify({
        "ok": True,
        "restarted": restarted,
        "keys": secrets_status(),
        "message": (
            "API keys saved and app restarted."
            if restarted
            else "API keys saved. Restart the app to apply on the display: sudo systemctl restart flightscnr"
        ),
    })


@app.get("/weather/json")
def weather_json():
    import weather_prefs

    weather_prefs.reload()
    units = weather_prefs.temperature_units()
    return jsonify(
        {
            "temperature_units": units,
            "label": weather_prefs.portal_label(),
            "symbol": weather_prefs.unit_symbol(),
        }
    )


@app.post("/weather")
def weather_save():
    import weather_prefs

    data = request.get_json(silent=True) or {}
    raw = data.get("temperature_units") or data.get("units")
    if raw is None:
        return jsonify({"message": "temperature_units is required"}), 400
    weather_prefs.update(temperature_units_value=str(raw))
    return jsonify(
        {
            "ok": True,
            "temperature_units": weather_prefs.temperature_units(),
            "label": weather_prefs.portal_label(),
            "symbol": weather_prefs.unit_symbol(),
            "message": f"Weather units set to {weather_prefs.portal_label()}.",
        }
    )


@app.get("/display/json")
def display_json():
    from display.round_touch import settings

    return jsonify(
        {
            "brightness_percent": settings.brightness_percent(),
            "flight_detail_timeout_s": settings.flight_detail_timeout_s(),
            "clock_timeout_s": settings.clock_timeout_s(),
            "auto_idle_clock": settings.auto_idle_clock_enabled(),
        }
    )


@app.post("/display")
def display_save():
    from display.round_touch import settings

    data = request.get_json(silent=True) or {}
    if "brightness_percent" in data:
        try:
            settings.set_brightness_percent(int(data.get("brightness_percent")))
        except (TypeError, ValueError):
            return jsonify({"message": "brightness_percent must be a number"}), 400
    if "auto_idle_clock" in data:
        settings.set_auto_idle_clock_enabled(bool(data.get("auto_idle_clock")))
    if "flight_detail_timeout_s" in data:
        settings.set_flight_detail_timeout_s(data.get("flight_detail_timeout_s"))
    if "clock_timeout_s" in data:
        settings.set_clock_timeout_s(data.get("clock_timeout_s"))
    return jsonify(
        {
            "ok": True,
            "brightness_percent": settings.brightness_percent(),
            "flight_detail_timeout_s": settings.flight_detail_timeout_s(),
            "clock_timeout_s": settings.clock_timeout_s(),
            "auto_idle_clock": settings.auto_idle_clock_enabled(),
            "message": "Display settings saved.",
        }
    )


@app.post("/settings/reload")
def settings_reload():
    """Signal the on-device display to re-apply settings / location from disk."""
    from display.round_touch import settings

    settings.request_reload()
    try:
        reload_location_override()
    except Exception:
        pass
    return jsonify(
        {
            "message": "Display will reload settings within about a second.",
        }
    )


@app.get("/radar/json")
def radar_json():
    from display.round_touch import color_presets, scale, settings

    idx = settings.scale_index()
    units = settings.distance_units()
    return jsonify(
        {
            "distance_units": units,
            "scale_index": idx,
            "range_value": scale.format_display_value(idx, units),
            "range_presets_mi": list(scale.PRESET_STATUTE_MILES),
            "min_height_ft": settings.min_height_ft(),
            "max_height_ft": settings.max_height_ft(),
            "theme_index": settings.theme_index(),
            "theme_options": list(color_presets.THEME_NAMES),
            "show_compass_rose": settings.show_compass_rose(),
            "show_range_rings": settings.show_range_rings(),
            "show_aircraft_tag": settings.show_aircraft_tag(),
            "facing_deg": settings.facing_deg(),
            "show_sweep_line": settings.show_sweep_line(),
            "show_precipitation": settings.show_precipitation(),
            "traffic_mode": settings.traffic_mode(),
            "ais_enabled": settings.ais_enabled(),
            "map_style": settings.map_style(),
            "map_style_options": list(settings.MAP_STYLES),
            "vfr_map_opacity": settings.vfr_map_opacity(),
        }
    )


@app.post("/radar")
def radar_save():
    from display.round_touch import map_bg, rainviewer_overlay, scale, settings

    data = request.get_json(silent=True) or {}
    if "distance_units" in data:
        settings.set_distance_units(data.get("distance_units"))
    units = settings.distance_units()
    if "range_value" in data:
        raw = str(data.get("range_value", "")).strip()
        try:
            value = float(raw)
        except ValueError:
            return jsonify({"ok": False, "message": "Range must be a number."}), 400
        if value <= 0:
            return jsonify({"ok": False, "message": "Range must be greater than zero."}), 400
        idx = scale.index_for_value(value, units)
        settings.set_scale_index(idx)
        scale.select(idx)
        map_bg.request_background()
        rainviewer_overlay.request_overlay()
    elif "scale_index" in data:
        settings.set_scale_index(int(data.get("scale_index")))
        scale.select(settings.scale_index())
        map_bg.request_background()
        rainviewer_overlay.request_overlay()
    if "min_height_ft" in data:
        settings.set_min_height_ft(int(data.get("min_height_ft")))
    if "max_height_ft" in data:
        settings.set_max_height_ft(int(data.get("max_height_ft")))
    if "theme_index" in data:
        settings.set_theme_index(int(data.get("theme_index")))
    if "show_compass_rose" in data:
        settings.set_show_compass_rose(bool(data.get("show_compass_rose")))
    if "show_range_rings" in data:
        settings.set_show_range_rings(bool(data.get("show_range_rings")))
    if "show_aircraft_tag" in data:
        settings.set_show_aircraft_tag(bool(data.get("show_aircraft_tag")))
    if "facing_deg" in data:
        settings.set_facing_deg(data.get("facing_deg"))
    if "show_sweep_line" in data:
        settings.set_show_sweep_line(bool(data.get("show_sweep_line")))
    if "show_precipitation" in data:
        settings.set_show_precipitation(bool(data.get("show_precipitation")))
        rainviewer_overlay.invalidate()
        if settings.show_precipitation():
            rainviewer_overlay.request_overlay()
    if "map_style" in data:
        settings.set_map_style(str(data.get("map_style") or ""))
    if "vfr_map_opacity" in data:
        try:
            settings.set_vfr_map_opacity(int(data.get("vfr_map_opacity")))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "message": "vfr_map_opacity must be a number"}), 400
    if "traffic_mode" in data:
        settings.set_traffic_mode(str(data.get("traffic_mode") or ""))
    elif "ais_enabled" in data:
        settings.set_ais_enabled(bool(data.get("ais_enabled")))
    settings.request_reload()
    return jsonify({"ok": True, "message": "Radar settings saved."})


@app.get("/off-hours/json")
def off_hours_json():
    from display.round_touch import off_hours

    return jsonify(off_hours.prefs())


@app.post("/off-hours")
def off_hours_save():
    from display.round_touch import off_hours

    data = request.get_json(silent=True) or {}
    updated = off_hours.update_prefs(
        enabled=data.get("enabled"),
        start=data.get("start"),
        end=data.get("end"),
        mode=data.get("mode"),
        dim_percent=data.get("dim_percent"),
        force_clock=data.get("force_clock"),
    )
    # Apply brightness immediately from the web save path so changes take
    # effect even before the display loop's next pass.
    try:
        from display.round_touch import backlight, settings

        backlight.apply_percent(
            off_hours.effective_brightness_percent(settings.brightness_percent())
        )
    except Exception:
        pass
    return jsonify(
        {
            "ok": True,
            **updated,
            "message": "Off-hours schedule saved.",
        }
    )


# Serve map files from the data directory
@app.get("/maps/<path:filename>")
def maps(filename):
    return send_from_directory(MAPS_DIR, filename)


@app.get("/updates/json")
def updates_json():
    from utilities import updater

    return jsonify(updater.check_for_update())


@app.post("/updates/check")
def updates_check():
    from utilities import updater

    return jsonify(updater.check_for_update(force=True))


@app.get("/updates/status")
def updates_status():
    from utilities import updater

    return jsonify(updater.update_status())


@app.post("/updates/apply")
def updates_apply():
    from utilities import updater

    return jsonify(updater.start_update())


@app.post("/system/reboot")
def system_reboot():
    from utilities import system_control

    return jsonify(system_control.request_reboot())


@app.post("/system/shutdown")
def system_shutdown():
    from utilities import system_control

    return jsonify(system_control.request_shutdown())


@app.post("/system/restart-app")
def system_restart_app():
    from utilities import system_control

    return jsonify(system_control.request_app_restart())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False)
