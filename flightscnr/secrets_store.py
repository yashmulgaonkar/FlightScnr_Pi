"""
Load API keys from user-friendly sources (no shell / .env required).

Priority (highest wins):
  1. Environment variables (e.g. /etc/flightscnr.env via systemd)
  2. Web portal file: /var/lib/flightscnr/secrets.json
  3. Project file: config.h in the repo root

Call bootstrap_secrets() before reading keys in config.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_H_PATH = os.path.join(_REPO_ROOT, "config.h")
DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
SECRETS_JSON_PATH = os.path.join(DATA_DIR, "secrets.json")

MANAGED_KEYS = (
    "FR24_API_KEY",
    "TOMORROW_API_KEY",
    "AIRLABS_API_KEY",
    "AISSTREAM_API_KEY",
    "FLIGHTAWARE_API_KEY",
    "HOME_LAT",
    "HOME_LON",
)

# Non-secret keys from config.h that should become env vars when unset.
CONFIG_H_SETTINGS = MANAGED_KEYS + (
    "SHOW_AIRLINE_LOGOS",
    "VESSEL_SHORT_TAGS",
    "VESSEL_HIDE_PARKED",
    "VESSEL_HIERARCHY",
    "VESSEL_DENSITY_MODE",
    "VESSEL_PARKED_SOG_KT",
    "DUMP1090_ENABLED",
    "DUMP1090_URL",
)

TOGGLE_KEYS = (
    "USE_FR24_API",
    "USE_TOMORROW_WEATHER",
    "USE_AIRLABS_API",
    "USE_AISSTREAM_API",
    "USE_FLIGHTAWARE_API",
)

# Non-secret data-source settings stored alongside secrets.json.
SOURCE_SETTING_KEYS = (
    "DUMP1090_ENABLED",
    "DUMP1090_URL",
)


def _to_bool(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "t")

_DEFINE_RE = re.compile(
    r'^\s*#\s*define\s+([A-Z_][A-Z0-9_]*)\s+("([^"]*)"|\'([^\']*)\'|(\S+))',
    re.IGNORECASE,
)
_ASSIGN_RE = re.compile(
    r'^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(.+?))\s*;?\s*$',
)


def _strip_inline_comment(value: str) -> str:
    if "//" in value:
        value = value.split("//", 1)[0]
    return value.strip().rstrip(";")


def parse_config_h(text: str) -> dict[str, str]:
    """Parse config.h — supports // comments, #define KEY \"val\", and KEY = val."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("/*") or line.startswith("*"):
            continue

        m = _DEFINE_RE.match(line)
        if m:
            key = m.group(1).upper()
            value = m.group(3) or m.group(4) or m.group(5) or ""
            out[key] = _strip_inline_comment(value)
            continue

        m = _ASSIGN_RE.match(line)
        if m:
            key = m.group(1).upper()
            value = m.group(2) or m.group(3) or m.group(4) or ""
            out[key] = _strip_inline_comment(value)
    return out


def load_config_h() -> dict[str, str]:
    try:
        with open(CONFIG_H_PATH, encoding="utf-8") as fh:
            return parse_config_h(fh.read())
    except OSError:
        return {}


def load_secrets_json() -> dict[str, str]:
    try:
        with open(SECRETS_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key in MANAGED_KEYS + SOURCE_SETTING_KEYS:
        raw = data.get(key) or data.get(key.lower())
        if raw is not None and str(raw).strip():
            out[key] = str(raw).strip()
        elif key == "DUMP1090_ENABLED" and raw is not None:
            # Allow explicit False to be stored / restored.
            out[key] = str(raw).strip() or "False"
    return out


def _load_secrets_file_raw() -> dict:
    """Full secrets.json document (keys + toggles + source settings)."""
    try:
        with open(SECRETS_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_toggles() -> dict[str, bool]:
    defaults = {
        "USE_FR24_API": True,
        "USE_TOMORROW_WEATHER": True,
        "USE_AIRLABS_API": True,
        "USE_AISSTREAM_API": True,
        "USE_FLIGHTAWARE_API": False,
    }
    try:
        with open(SECRETS_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    out = dict(defaults)
    for key in TOGGLE_KEYS:
        out[key] = _to_bool(data.get(key), defaults[key])
    return out


def api_enabled(key_name: str) -> bool:
    toggles = load_toggles()
    mapping = {
        "FR24_API_KEY": "USE_FR24_API",
        "TOMORROW_API_KEY": "USE_TOMORROW_WEATHER",
        "AIRLABS_API_KEY": "USE_AIRLABS_API",
        "AISSTREAM_API_KEY": "USE_AISSTREAM_API",
        "FLIGHTAWARE_API_KEY": "USE_FLIGHTAWARE_API",
    }
    toggle_key = mapping.get(key_name)
    if not toggle_key:
        return True
    return bool(toggles.get(toggle_key, True))


def _merged_secrets() -> dict[str, str]:
    merged = load_config_h()
    merged.update(load_secrets_json())
    return merged


def bootstrap_secrets() -> None:
    """Apply config.h + secrets.json to os.environ when env vars are unset."""
    for key, value in _merged_secrets().items():
        if key in CONFIG_H_SETTINGS and value and not os.environ.get(key, "").strip():
            os.environ[key] = value
    # DUMP1090_ENABLED may be False — still apply when unset in the process env.
    merged = _merged_secrets()
    if "DUMP1090_ENABLED" in merged and not os.environ.get("DUMP1090_ENABLED", "").strip():
        os.environ["DUMP1090_ENABLED"] = merged["DUMP1090_ENABLED"]


def dump1090_settings() -> dict:
    """Current dump1090 portal/settings values."""
    bootstrap_secrets()
    file_vals = load_secrets_json()
    enabled = _to_bool(
        os.environ.get("DUMP1090_ENABLED")
        or file_vals.get("DUMP1090_ENABLED")
        or "False",
        False,
    )
    url = (
        os.environ.get("DUMP1090_URL")
        or file_vals.get("DUMP1090_URL")
        or "http://127.0.0.1:8080/data/aircraft.json"
    ).strip()
    return {
        "DUMP1090_ENABLED": enabled,
        "DUMP1090_URL": url or "http://127.0.0.1:8080/data/aircraft.json",
    }


def apply_dump1090_to_runtime(enabled: bool, url: str) -> None:
    """Update process env + config module so the next overhead cycle picks this up."""
    url = (url or "").strip() or "http://127.0.0.1:8080/data/aircraft.json"
    os.environ["DUMP1090_ENABLED"] = "True" if enabled else "False"
    os.environ["DUMP1090_URL"] = url
    try:
        import config as cfg

        cfg.DUMP1090_ENABLED = bool(enabled)
        cfg.DUMP1090_URL = url
    except Exception:
        pass


def mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


def secrets_status() -> dict:
    """Status for web portal (masked values, source hints)."""
    bootstrap_secrets()
    merged = _merged_secrets()
    toggles = load_toggles()
    status = {}
    for key in MANAGED_KEYS:
        env_val = os.environ.get(key, "").strip()
        file_val = merged.get(key, "").strip()
        active = env_val or file_val
        source = "unset"
        if env_val:
            source = "environment"
        elif file_val and os.path.isfile(SECRETS_JSON_PATH) and key in load_secrets_json():
            source = "web portal"
        elif file_val and os.path.isfile(CONFIG_H_PATH):
            source = "config.h"
        status[key] = {
            "configured": bool(active),
            "masked": mask_secret(active),
            "source": source,
            "enabled": api_enabled(key),
        }
    status["toggles"] = toggles
    status["config_h_path"] = CONFIG_H_PATH
    status["secrets_json_path"] = SECRETS_JSON_PATH
    status["dump1090"] = dump1090_settings()
    try:
        from utilities.flightaware_client import usage_status

        status["flightaware_usage"] = usage_status()
    except Exception:
        status["flightaware_usage"] = None
    return status


def save_secrets_from_portal(payload: dict) -> dict[str, str]:
    """
    Save API keys from web portal. Empty string keeps the previous value
    unless clear_missing=True in payload.
    """
    current = load_secrets_json()
    clear = bool(payload.get("clear_missing"))
    updated: dict[str, str] = dict(_load_secrets_file_raw())
    # Ensure managed keys from the filtered loader are present.
    updated.update(current)

    field_map = {
        "fr24_api_key": "FR24_API_KEY",
        "tomorrow_api_key": "TOMORROW_API_KEY",
        "airlabs_api_key": "AIRLABS_API_KEY",
        "aisstream_api_key": "AISSTREAM_API_KEY",
        "flightaware_api_key": "FLIGHTAWARE_API_KEY",
    }
    for form_key, env_key in field_map.items():
        if form_key not in payload:
            continue
        raw = str(payload.get(form_key) or "").strip()
        if raw:
            updated[env_key] = raw
            os.environ[env_key] = raw
        elif clear:
            updated.pop(env_key, None)
            os.environ.pop(env_key, None)

    toggle_map = {
        "use_fr24_api": "USE_FR24_API",
        "use_tomorrow_weather": "USE_TOMORROW_WEATHER",
        "use_airlabs_api": "USE_AIRLABS_API",
        "use_aisstream_api": "USE_AISSTREAM_API",
        "use_flightaware_api": "USE_FLIGHTAWARE_API",
    }
    for form_key, key in toggle_map.items():
        if form_key in payload:
            updated[key] = _to_bool(payload.get(form_key), True)

    if "dump1090_enabled" in payload or "dump1090_url" in payload:
        enabled = _to_bool(
            payload.get("dump1090_enabled", updated.get("DUMP1090_ENABLED", False)),
            False,
        )
        url = str(
            payload.get(
                "dump1090_url",
                updated.get("DUMP1090_URL", "http://127.0.0.1:8080/data/aircraft.json"),
            )
            or ""
        ).strip() or "http://127.0.0.1:8080/data/aircraft.json"
        updated["DUMP1090_ENABLED"] = "True" if enabled else "False"
        updated["DUMP1090_URL"] = url
        apply_dump1090_to_runtime(enabled, url)

    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SECRETS_JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(updated, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, SECRETS_JSON_PATH)
    try:
        os.chmod(SECRETS_JSON_PATH, 0o600)
    except OSError:
        pass
    # Re-inject FR24 env for fr24 package if already imported
    try:
        from utilities import fr24_client

        fr24_client._ensure_env_credentials()
    except Exception:
        pass
    try:
        from utilities.ais_client import sync_ais_client

        sync_ais_client()
    except Exception:
        pass
    # Keep config.FLIGHTAWARE_API_KEY in sync for already-imported modules.
    if "FLIGHTAWARE_API_KEY" in updated:
        try:
            import config as cfg

            cfg.FLIGHTAWARE_API_KEY = updated["FLIGHTAWARE_API_KEY"]
        except Exception:
            pass
    return updated


def request_service_restart() -> bool:
    """Restart flightscnr so the display picks up new keys."""
    try:
        subprocess.run(
            ["systemctl", "restart", "flightscnr"],
            check=False,
            timeout=15,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not restart flightscnr service: %s", exc)
        return False
