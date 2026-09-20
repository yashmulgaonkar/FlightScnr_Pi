#!/bin/bash
# Portal-triggered update or install re-sync, then restart flightscnr.service.
# User presets live outside the repo (/var/lib/flightscnr, /etc/flightscnr.env).
#
# Modes:
#   (default)  git pull via install-pi.sh update --no-start
#   resync     re-run install-pi.sh install --skip-apt --no-start (no pull)
#
# Restart is deferred (systemd-run / sleep fallback) so this script can write
# update-status.json + drop the lock BEFORE systemctl restart. Restarting the
# service from inside its own cgroup with KillMode=mixed would SIGKILL this
# script and leave the portal stuck on "Update in progress…".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="/var/lib/flightscnr"
STATUS_FILE="$DATA_DIR/update-status.json"
LOCK_FILE="$DATA_DIR/update.lock"
LOG_FILE="$DATA_DIR/update.log"
RESTART_DELAY_S="${FLIGHTSCNR_UPDATE_RESTART_DELAY_S:-2}"

UPDATE_MODE="${1:-update}"
case "$UPDATE_MODE" in
    update|resync) ;;
    *)
        echo "Unknown mode: $UPDATE_MODE (use update|resync)" >&2
        exit 1
        ;;
esac

# Detach from the web portal process (new session / nohup). Still stays in the
# flightscnr.service cgroup — deferred restart below is what avoids self-kill.
# stdout/stderr go only to LOG_FILE here; the worker must not also tee -a the
# same file or every line is duplicated (issue #77 user logs).
if [ -z "${FLIGHTSCNR_PORTAL_UPDATE:-}" ]; then
    export FLIGHTSCNR_PORTAL_UPDATE=1
    mkdir -p "$DATA_DIR"
    nohup "$0" "$UPDATE_MODE" >>"$LOG_FILE" 2>&1 </dev/null &
    exit 0
fi

write_status() {
    local state="$1"
    local message="${2:-}"
    mkdir -p "$DATA_DIR"
    python3 - "$STATUS_FILE" "$state" "$message" <<'PY'
import json, sys
from datetime import datetime, timezone

path, state, message = sys.argv[1:4]
payload = {
    "state": state,
    "message": message,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
PY
}

release_lock() {
    exec 9>&- || true
    rm -f "$LOCK_FILE"
}

schedule_service_restart() {
    # Prefer a transient systemd timer so restart runs outside this unit's cgroup.
    local unit="flightscnr-portal-restart-$$"
    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run \
            --quiet \
            --collect \
            --unit="$unit" \
            --on-active="${RESTART_DELAY_S}s" \
            /bin/systemctl restart flightscnr.service
        then
            echo "Scheduled flightscnr restart in ${RESTART_DELAY_S}s ($unit)"
            return 0
        fi
        echo "systemd-run scheduling failed — falling back to background sleep"
    fi
    # Fallback still works for portal status (already written) even if this
    # sleeper is later swept by KillMode=mixed during the restart.
    nohup bash -c "sleep ${RESTART_DELAY_S}; systemctl restart flightscnr.service" \
        >>"$LOG_FILE" 2>&1 </dev/null &
    echo "Scheduled flightscnr restart in ${RESTART_DELAY_S}s (sleep fallback, pid $!)"
}

schedule_x11_reboot() {
    # install-pi.sh leaves this flag when LightDM was switched to X11 for
    # pinch-to-zoom; a service restart alone cannot apply the new session.
    local flag="$DATA_DIR/need-reboot-for-x11"
    local progress="$DATA_DIR/reboot-in-progress"
    local delay_s="${FLIGHTSCNR_X11_REBOOT_DELAY_S:-8}"
    local unit="flightscnr-portal-x11-reboot-$$"

    [ -f "$flag" ] || return 1
    if [ "${FLIGHTSCNR_NO_AUTO_REBOOT:-}" = "1" ]; then
        echo "X11 reboot needed ($flag) but FLIGHTSCNR_NO_AUTO_REBOOT=1 — skipping"
        return 1
    fi

    # On-screen modal in the display app while we wait for the reboot.
    mkdir -p "$DATA_DIR"
    printf 'x11\n' >"$progress"
    chmod 644 "$progress" 2>/dev/null || true

    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run \
            --quiet \
            --collect \
            --unit="$unit" \
            --on-active="${delay_s}s" \
            /bin/systemctl reboot
        then
            echo "Scheduled reboot for X11 / pinch-zoom in ${delay_s}s ($unit)"
            return 0
        fi
        echo "systemd-run reboot schedule failed — falling back to background sleep"
    fi
    nohup bash -c "sleep ${delay_s}; systemctl reboot" \
        >>"$LOG_FILE" 2>&1 </dev/null &
    echo "Scheduled reboot for X11 / pinch-zoom in ${delay_s}s (sleep fallback, pid $!)"
    return 0
}

fail_cleanup() {
    local code=$?
    trap - EXIT
    write_status "failed" "Update failed (exit $code). See $LOG_FILE"
    release_lock
    # git pull may already have advanced VERSION on disk while install aborted.
    # Restart so the splash matches the checkout; auto-resync can finish install
    # steps on the next boot of the service (issue #77 exit-2 reports).
    schedule_service_restart || true
    exit "$code"
}

mkdir -p "$DATA_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Update already running" >&2
    exit 1
fi

echo $$ >"$LOCK_FILE"
trap fail_cleanup EXIT

echo ""
echo "==> Portal ${UPDATE_MODE} $(date -Iseconds)"
echo "    Repo: $REPO_ROOT"

if [ ! -f "$REPO_ROOT/install-pi.sh" ]; then
    echo "install-pi.sh not found"
    exit 1
fi

# Sync code/deps/unit without restarting from inside this cgroup.
# FLIGHTSCNR_SKIP_RESTART is a belt-and-suspenders guard for start_service().
export FLIGHTSCNR_SKIP_RESTART=1

if [ "$UPDATE_MODE" = "resync" ]; then
    write_status "running" "Finishing install (re-sync)… Do not turn off."
    bash "$REPO_ROOT/install-pi.sh" install --skip-apt --no-start
    success_msg="Install re-sync finished. Restarting display…"
else
    write_status "running" "Pulling latest changes…"
    bash "$REPO_ROOT/install-pi.sh" update --no-start
    success_msg="Update finished successfully. Restarting display…"
fi

# Status + lock must be cleared before restart/reboot can kill this cgroup member.
trap - EXIT
if [ -f "$DATA_DIR/need-reboot-for-x11" ]; then
    success_msg="Desktop switched to X11 for pinch-to-zoom. Rebooting…"
fi
write_status "success" "$success_msg"
release_lock
if schedule_x11_reboot; then
    :
else
    schedule_service_restart
fi
exit 0
