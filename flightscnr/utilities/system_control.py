"""Portal-triggered reboot and shutdown."""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("flightscnr.system")


def _run_power_command(command: str) -> list[str]:
    if os.geteuid() == 0:
        return ["/bin/bash", "-c", f"sleep 1.5 && {command}"]
    return ["/bin/bash", "-c", f"sleep 1.5 && sudo -n {command}"]


def _start_power_action(action: str, command: str, *, message: str | None = None) -> dict:
    try:
        subprocess.Popen(
            _run_power_command(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        logger.warning("Could not start %s: %s", action, exc)
        return {"ok": False, "message": f"Could not {action}: {exc}"}

    if message is None:
        message = f"{action.capitalize()} scheduled. This device will go offline shortly."
    return {"ok": True, "message": message}


def request_reboot() -> dict:
    return _start_power_action("reboot", "systemctl reboot")


def request_shutdown() -> dict:
    return _start_power_action("shutdown", "systemctl poweroff")


def request_app_restart() -> dict:
    return _start_power_action(
        "restart",
        "systemctl restart flightscnr",
        message="FlightScnr is restarting. The display and portal will reconnect shortly.",
    )
