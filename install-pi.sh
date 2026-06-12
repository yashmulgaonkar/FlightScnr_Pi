#!/bin/bash
# install-pi.sh — Install or update FlightScnr Pi on a Raspberry Pi.
#
# First install (after clone):
#   git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
#   cd ~/FlightScnr_Pi
#   sudo bash install-pi.sh
#
# Update (git pull + re-sync):
#   bash ~/FlightScnr_Pi/install-pi.sh update
#
# Usage:
#   sudo bash install-pi.sh [install] [--no-start]
#   bash install-pi.sh update
#
set -euo pipefail

ENV_DEST="/etc/flightscnr.env"
DATA_DIR="/var/lib/flightscnr"
SERVICE_NAME="flightscnr.service"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}"

REPO_ROOT=""
SETUP_DIR=""
APP_DIR=""
VENV_DIR=""
REPO_OWNER=""
REPO_OWNER_HOME=""
REPO_OWNER_UID=""

setup_paths() {
    REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
    SETUP_DIR="$REPO_ROOT/flightscnr/setup"
    APP_DIR="$REPO_ROOT/flightscnr"
    VENV_DIR="$REPO_ROOT/.venv"

    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        REPO_OWNER="$SUDO_USER"
    else
        REPO_OWNER="$(stat -c '%U' "$REPO_ROOT")"
    fi

    REPO_OWNER_HOME="$(getent passwd "$REPO_OWNER" | cut -d: -f6)"
    REPO_OWNER_UID="$(id -u "$REPO_OWNER")"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "This command must be run as root (use: sudo bash $0 $*)" >&2
        exit 1
    fi
}

log_step() { echo ""; echo "==> $*"; }
log_ok()   { echo "    ✓ $*"; }
log_warn() { echo "    ⚠ $*"; }

install_apt_packages() {
    log_step "Installing system packages"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        build-essential \
        python3-pip python3-venv python3-dev \
        python3-setuptools python3-wheel \
        libsdl2-2.0-0 libsdl2-dev libfreetype6-dev \
        libjpeg-dev zlib1g-dev \
        fonts-dejavu-core \
        unzip git curl
    log_ok "System packages ready"
}

extract_logos() {
    local logo_zip="$REPO_ROOT/logo.zip"
    local logo_dir="$REPO_ROOT/logo"
    local logos_link="$APP_DIR/logos"

    if [ ! -f "$logo_zip" ]; then
        log_warn "logo.zip not found — airline logos will be skipped"
        return 0
    fi

    if [ ! -d "$logo_dir" ] || [ "$logo_zip" -nt "$logo_dir" ]; then
        log_step "Extracting airline logos"
        unzip -qo "$logo_zip" -d "$REPO_ROOT"
        chmod -R a+r "$logo_dir"
        log_ok "Logos extracted to logo/"
    fi

    rm -f "$logos_link"
    ln -sfn ../logo "$logos_link"
    log_ok "Linked flightscnr/logos → ../logo"
}

setup_venv() {
    log_step "Python virtual environment"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv --system-site-packages "$VENV_DIR"
        log_ok "Created $VENV_DIR"
    else
        log_ok "Using existing $VENV_DIR"
    fi

    "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
    "$VENV_DIR/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt"
    log_ok "Python dependencies installed"
}

setup_data_dir() {
    log_step "Runtime data directory"
    install -d -m 0755 "$DATA_DIR"
    install -d -m 0755 "$DATA_DIR/maps"
    chown -R "$REPO_OWNER:$REPO_OWNER" "$DATA_DIR"
    log_ok "$DATA_DIR ready (owned by $REPO_OWNER)"
}

setup_env_file() {
    if [ -f "$ENV_DEST" ]; then
        log_ok "$ENV_DEST already exists — keeping current configuration"
        return 0
    fi

    log_step "Creating $ENV_DEST"
    if [ -f "$REPO_ROOT/.env" ]; then
        cp "$REPO_ROOT/.env" "$ENV_DEST"
        log_ok "Copied .env → $ENV_DEST"
    else
        cp "$REPO_ROOT/.env.example" "$ENV_DEST"
        log_ok "Copied .env.example → $ENV_DEST"
        echo ""
        echo "  !! Edit $ENV_DEST and add your API keys before starting the service !!"
        echo "     sudo nano $ENV_DEST"
        echo ""
    fi

    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
}

install_systemd_service() {
    local service_src="$SETUP_DIR/flightscnr.service"
    local xauthority="${REPO_OWNER_HOME}/.Xauthority"
    local runtime_dir="/run/user/${REPO_OWNER_UID}"

    log_step "Installing systemd service (persists across reboot)"
    sed \
        -e "s|__REPO_DIR__|$REPO_ROOT|g" \
        -e "s|__DESKTOP_USER__|$REPO_OWNER|g" \
        -e "s|__XAUTHORITY__|$xauthority|g" \
        -e "s|__XDG_RUNTIME_DIR__|$runtime_dir|g" \
        "$service_src" > "$SERVICE_DEST"
    chmod 0644 "$SERVICE_DEST"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    log_ok "Copied to $SERVICE_DEST"
    log_ok "Enabled for boot (graphical.target)"
}

fix_repo_permissions() {
    log_step "Repository permissions"
    chown -R "$REPO_OWNER:$REPO_OWNER" "$REPO_ROOT"
    find "$REPO_ROOT" -type d -exec chmod 755 {} +
    find "$REPO_ROOT" -type f -exec chmod 644 {} +
    chmod 755 "$REPO_ROOT/install-pi.sh"
    chmod 755 "$VENV_DIR/bin/"* 2>/dev/null || true
    log_ok "Repo owned by $REPO_OWNER"
}

start_service() {
    log_step "Starting $SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_ok "Service is running"
    else
        echo "    ✗ Service failed to start. Check: sudo journalctl -u $SERVICE_NAME -n 30" >&2
        return 1
    fi
}

cmd_install() {
    local no_start=0
    for arg in "$@"; do
        case "$arg" in
            --no-start) no_start=1 ;;
            *) echo "Unknown option: $arg" >&2; exit 1 ;;
        esac
    done

    require_root
    setup_paths

    echo "============================================"
    echo "  FlightScnr Pi — Install / Sync"
    echo "============================================"
    echo "  Repo:    $REPO_ROOT"
    echo "  Owner:   $REPO_OWNER"
    echo "  Data:    $DATA_DIR"
    echo "============================================"

    install_apt_packages
    extract_logos
    setup_venv
    setup_data_dir
    setup_env_file
    install_systemd_service
    fix_repo_permissions

    if [ "$no_start" -eq 0 ]; then
        start_service
    else
        log_ok "Skipped service start (--no-start)"
    fi

    echo ""
    echo "============================================"
    echo "  Done"
    echo "============================================"
    echo ""
    echo "  Service:   sudo systemctl status flightscnr"
    echo "  Logs:      sudo journalctl -u flightscnr -f"
    echo "  Config:    sudo nano /etc/flightscnr.env"
    echo "  Reboot:    starts automatically (systemctl is-enabled flightscnr)"
    echo "  Update:    bash $REPO_ROOT/install-pi.sh update"
    echo ""
}

cmd_update() {
    setup_paths

    echo "============================================"
    echo "  FlightScnr Pi — Update"
    echo "============================================"
    echo "  Repo: $REPO_ROOT"
    echo ""

    if [ ! -d "$REPO_ROOT/.git" ]; then
        echo "Not a git repository: $REPO_ROOT" >&2
        exit 1
    fi

    log_step "Pulling latest changes"
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        sudo -u "$SUDO_USER" git -C "$REPO_ROOT" pull --ff-only
    elif [ "$(id -u)" -eq 0 ]; then
        sudo -u "$REPO_OWNER" git -C "$REPO_ROOT" pull --ff-only
    else
        git -C "$REPO_ROOT" pull --ff-only
    fi
    log_ok "Git pull complete ($(git -C "$REPO_ROOT" log --oneline -1))"

    if [ "$(id -u)" -ne 0 ]; then
        echo ""
        echo "Re-syncing install (needs root)..."
        exec sudo bash "$REPO_ROOT/install-pi.sh" install
    else
        cmd_install
    fi
}

usage() {
    cat <<EOF
Usage:
  sudo bash install-pi.sh [install] [--no-start]   First install or re-sync
  bash install-pi.sh update                        git pull + re-sync + restart

EOF
}

# --- main ---
case "${1:-install}" in
    install)
        shift
        cmd_install "$@"
        ;;
    update)
        cmd_update
        ;;
    -h|--help|help)
        usage
        ;;
    --no-start)
        cmd_install --no-start
        ;;
    *)
        echo "Unknown command: $1" >&2
        usage
        exit 1
        ;;
esac
