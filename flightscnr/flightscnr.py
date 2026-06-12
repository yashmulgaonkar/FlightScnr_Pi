#!/usr/bin/python3
import subprocess
import os
import sys
import logging

# Configure logging for systemd (no timestamps — journald adds them)
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("flightscnr")


def validate_config():
    """Check that required configuration is present and log status."""
    from config import (
        FR24_API_KEY, TOMORROW_API_KEY,
        ZONE_HOME, LOCATION_HOME, TEMPERATURE_LOCATION,
        location_configured, LOCATION_SOURCE, SEARCH_RADIUS_NM,
    )

    logger.info("=" * 50)
    logger.info("FlightScnr Pi — Starting up")
    logger.info("=" * 50)

    errors = []

    # --- API Keys ---
    if FR24_API_KEY:
        masked = FR24_API_KEY[:8] + "..." + FR24_API_KEY[-4:]
        logger.info(f"  ✓ FR24_API_KEY: {masked}")
    else:
        errors.append("FR24_API_KEY")
        logger.warning(
            "  ⚠ FR24_API_KEY is NOT SET — ADS-B-only mode "
            "(no routes, flight details, or tracked flights)"
        )

    if TOMORROW_API_KEY:
        masked = TOMORROW_API_KEY[:4] + "..." + TOMORROW_API_KEY[-4:]
        logger.info(f"  ✓ TOMORROW_API_KEY: {masked}")
    else:
        errors.append("TOMORROW_API_KEY")
        logger.warning("  ⚠ TOMORROW_API_KEY is NOT SET — clock weather will not work")

    # --- Location ---
    if location_configured():
        logger.info(f"  ✓ Home: {LOCATION_HOME[0]:.4f}, {LOCATION_HOME[1]:.4f}")
        logger.info(f"  ✓ Zone: N={ZONE_HOME['tl_y']:.4f}, S={ZONE_HOME['br_y']:.4f}, "
                    f"W={ZONE_HOME['tl_x']:.4f}, E={ZONE_HOME['br_x']:.4f}")
        if LOCATION_SOURCE == "home_radius":
            logger.info(f"  ✓ Zone auto-built from HOME_LAT/LON ({SEARCH_RADIUS_NM:g}nm radius)")
        if TEMPERATURE_LOCATION:
            logger.info(f"  ✓ Weather location: {TEMPERATURE_LOCATION}")
        else:
            logger.warning("  ⚠ TEMPERATURE_LOCATION not set — weather will not work")
    else:
        errors.append("LOCATION")
        logger.error("  ✗ Location NOT SET — set HOME_LAT/HOME_LON or zone corners")
        logger.error("    Edit /etc/flightscnr.env and restart")

    # --- Summary ---
    if errors:
        logger.warning(f"  Incomplete config: {', '.join(errors)}")
        logger.warning("  Set them in /etc/flightscnr.env and restart")
    else:
        logger.info("  All prerequisites OK")

    logger.info("=" * 50)
    return len(errors) == 0


if __name__ == "__main__":
    # Get directory of this script (flightscnr.py)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Validate configuration before starting
    validate_config()

    # Build path to web/app.py
    app_path = os.path.join(base_dir, "web", "app.py")

    # Start Flask server in background (use same interpreter as this process)
    subprocess.Popen([sys.executable, app_path])

    # Start round touch display loop
    from display import Display
    display = Display()
    display.run()
