#!/bin/bash
# install-pi.sh — Install or update FlightScnr Pi on a Raspberry Pi.
#
# Requires: Raspberry Pi OS with desktop (X11 on :0), round touch LCD, network.
#
# First install (after clone):
#   git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
#   cd ~/FlightScnr_Pi
#   sudo bash install-pi.sh
#
# Update (git pull + re-sync, skips apt for speed):
#   bash ~/FlightScnr_Pi/install-pi.sh update
#
# Usage:
#   sudo bash install-pi.sh [install] [--no-start] [--skip-apt]
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
    VENV_DIR="$REPO_ROOT/flightscnr-venv"

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
        plymouth plymouth-themes \
        unzip git curl
    log_ok "System packages ready"
}

install_boot_splash() {
    # Custom Plymouth splash + desktop wallpaper + hide firmware rainbow splash.
    local src="$APP_DIR/assets/boot/splash.png"
    local pix_dir="/usr/share/plymouth/themes/pix"
    local pix_splash="$pix_dir/splash.png"
    local wall_dir="/usr/share/rpd-wallpaper"
    local wall_splash="$wall_dir/flightscnr.png"
    local config=""
    local cmdline=""
    local tmp_splash=""

    log_step "Boot splash & wallpaper (FlightScnr)"

    if [ ! -f "$src" ]; then
        log_warn "Missing $src — skipped boot splash / wallpaper install"
        return 0
    fi

    # Prefer Bookworm firmware partition layout.
    if [ -f /boot/firmware/config.txt ]; then
        config="/boot/firmware/config.txt"
        cmdline="/boot/firmware/cmdline.txt"
    elif [ -f /boot/config.txt ]; then
        config="/boot/config.txt"
        cmdline="/boot/cmdline.txt"
    fi

    # Pi panel is usually rotated vs the art (DISPLAY_ROTATION); Plymouth / the
    # desktop greeter have no FlightScnr rotation, so bake a 90° CW copy once.
    tmp_splash="$(mktemp /tmp/flightscnr-plymouth-splash.XXXXXX.png)"
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$src" "$tmp_splash" <<'PYROT'
import sys
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    Path(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes())
else:
    Image.open(sys.argv[1]).rotate(-90, expand=False).save(sys.argv[2], optimize=True)
PYROT
    else
        cp -f "$src" "$tmp_splash"
    fi

    if [ -d "$pix_dir" ]; then
        if [ -f "$pix_splash" ] && [ ! -f "$pix_dir/splash.png.stock" ]; then
            cp -a "$pix_splash" "$pix_dir/splash.png.stock"
        fi
        install -m 0644 "$tmp_splash" "$pix_splash"
        log_ok "Installed Plymouth splash from assets/boot/splash.png (rotated 90° CW for panel)"

        if command -v plymouth-set-default-theme >/dev/null 2>&1; then
            plymouth-set-default-theme pix >/dev/null 2>&1 || true
            if command -v update-initramfs >/dev/null 2>&1; then
                update-initramfs -u >/dev/null 2>&1 || log_warn "update-initramfs failed (splash may need a reboot once)"
            fi
            log_ok "Plymouth theme set to pix"
        fi
    else
        log_warn "Plymouth pix theme not found — skipped boot splash install"
    fi

    # Desktop wallpaper — same image as Plymouth.
    if [ -d "$wall_dir" ] || mkdir -p "$wall_dir" 2>/dev/null; then
        install -m 0644 "$tmp_splash" "$wall_splash"
        local conf
        for conf in \
            /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf \
            /home/pi/.config/pcmanfm/LXDE-pi/desktop-items-0.conf
        do
            mkdir -p "$(dirname "$conf")"
            if [ -f "$conf" ]; then
                if grep -qE '^\s*wallpaper=' "$conf"; then
                    sed -i "s|^[[:space:]]*wallpaper=.*|wallpaper=$wall_splash|" "$conf"
                else
                    printf 'wallpaper=%s\n' "$wall_splash" >> "$conf"
                fi
                if ! grep -qE '^\s*wallpaper_mode=' "$conf"; then
                    printf 'wallpaper_mode=crop\n' >> "$conf"
                fi
            else
                printf '[*]\nwallpaper_mode=crop\nwallpaper_common=1\nwallpaper=%s\n' "$wall_splash" > "$conf"
            fi
            if [[ "$conf" == /home/pi/* ]]; then
                chown -R pi:pi "$(dirname "$conf")" 2>/dev/null || true
            fi
        done
        # Refresh live desktop if a session is up (best-effort).
        if id pi >/dev/null 2>&1; then
            local pi_uid
            pi_uid="$(id -u pi)"
            sudo -u pi env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="/run/user/$pi_uid" \
                pcmanfm --set-wallpaper="$wall_splash" --wallpaper-mode=crop \
                >/dev/null 2>&1 || true
        fi
        log_ok "Desktop wallpaper set to FlightScnr splash ($wall_splash)"
    else
        log_warn "Could not create $wall_dir — skipped wallpaper install"
    fi

    rm -f "$tmp_splash"

    if [ -n "$config" ]; then
        if grep -qE '^\s*disable_splash=' "$config"; then
            sed -i 's/^\s*disable_splash=.*/disable_splash=1/' "$config"
        else
            printf '\n# FlightScnr Pi — hide firmware rainbow splash\ndisable_splash=1\n' >> "$config"
        fi
        log_ok "Firmware splash disabled ($config)"
    else
        log_warn "Could not find config.txt — firmware splash unchanged"
    fi

    if [ -n "$cmdline" ] && [ -f "$cmdline" ]; then
        # Keep quiet splash for Plymouth; add if missing.
        if ! grep -qw splash "$cmdline"; then
            # cmdline is a single line
            sed -i 's/$/ splash/' "$cmdline"
        fi
        if ! grep -qw quiet "$cmdline"; then
            sed -i 's/$/ quiet/' "$cmdline"
        fi
        log_ok "Kernel cmdline keeps quiet splash"
    fi
}

install_ui_fonts() {
    local inter_dir="$APP_DIR/fonts/inter"

    log_step "UI font (Inter)"

    mkdir -p "$inter_dir"
    if [ ! -f "$inter_dir/Inter-Regular.ttf" ] || [ ! -f "$inter_dir/Inter-Bold.ttf" ]; then
        local tmp
        tmp=$(mktemp -d)
        if curl -fsSL -o "$tmp/Inter.zip" \
            "https://github.com/yashmulgaonkar/inter/releases/download/v4.1/Inter-4.1.zip"; then
            unzip -qo -j "$tmp/Inter.zip" \
                "extras/ttf/Inter-Regular.ttf" "extras/ttf/Inter-Bold.ttf" \
                -d "$inter_dir"
            log_ok "Inter fonts ready"
        else
            log_warn "Could not download Inter fonts — UI may fall back to DejaVu"
        fi
        rm -rf "$tmp"
    else
        log_ok "Inter fonts ready"
    fi
}

install_aircraft_icons() {
    local src_repo="https://github.com/yashmulgaonkar/adsb-tracker"
    local dest="$APP_DIR/assets/aircraft/icons"
    local stamp="$dest/.installed"

    log_step "Aircraft radar icons"
    mkdir -p "$dest"

    # Prefer icons shipped in the repo (or already customized locally).
    if [ -f "$dest/medium-jet.png" ] && [ -f "$dest/aircraft-icons.json" ]; then
        log_ok "Aircraft icons already present ($dest)"
        return 0
    fi

    if [ -f "$stamp" ] && [ -f "$dest/medium-jet.png" ] && [ -f "$dest/aircraft-icons.json" ]; then
        log_ok "Aircraft icons already present ($dest)"
        return 0
    fi

    local tmp
    tmp=$(mktemp -d)
    if git clone --depth 1 "$src_repo" "$tmp/repo" >/dev/null 2>&1; then
        cp "$tmp/repo/public/assets/icons/"*.png "$dest/" 2>/dev/null || true
        cp "$tmp/repo/public/assets/icons/aircraft-icons.json" "$dest/" 2>/dev/null || true
        date -Iseconds > "$stamp"
        log_ok "Downloaded aircraft icons to assets/aircraft/icons"
    else
        log_warn "Could not download aircraft icons — radar will use vector fallback shapes"
    fi
    rm -rf "$tmp"
}

install_weather_icons() {
    local dest="$APP_DIR/assets/weather/png"
    local sun_dest="$APP_DIR/assets/weather/sun"
    local stamp="$dest/.installed"

    log_step "Tomorrow.io weather icons"
    mkdir -p "$dest" "$sun_dest"

    if [ -f "$stamp" ] && [ "$(find "$dest" -maxdepth 1 -name '*_large.png' | wc -l)" -ge 100 ] \
        && [ -f "$sun_dest/sunrise-dark@2x.png" ] && [ -f "$sun_dest/sunset-dark@2x.png" ]; then
        log_ok "Weather icons already present ($dest)"
        return 0
    fi

    local tmp
    tmp=$(mktemp -d)
    if git clone --depth 1 https://github.com/Tomorrow-IO-API/tomorrow-weather-codes.git "$tmp/repo" >/dev/null 2>&1; then
        cp "$tmp/repo/V2_icons/large/png/"*_large.png "$dest/" 2>/dev/null || true
        rm -f "$dest/"*@2x.png
        cp "$tmp/repo/V2_icons/small/sunset-sunrise/png/sunrise-dark@2x.png" "$sun_dest/" 2>/dev/null || true
        cp "$tmp/repo/V2_icons/small/sunset-sunrise/png/sunset-dark@2x.png" "$sun_dest/" 2>/dev/null || true
        date -Iseconds > "$stamp"
        log_ok "Downloaded Tomorrow.io icons to assets/weather/png"
    else
        log_warn "Could not download weather icons — clock/forecast will use fallback shapes"
    fi
    rm -rf "$tmp"
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

verify_python_deps() {
    log_step "Verifying Python dependencies"
    if "$VENV_DIR/bin/python" -c "import pygame, fr24, flask, httpx" >/dev/null 2>&1; then
        log_ok "Core imports OK (pygame, fr24, flask, httpx)"
        return 0
    fi
    log_warn "Import check failed — service may not start; review pip output above"
    return 1
}

setup_data_dir() {
    log_step "Runtime data directory"
    install -d -m 0755 "$DATA_DIR"
    install -d -m 0755 "$DATA_DIR/maps"
    chown -R "$REPO_OWNER:$REPO_OWNER" "$DATA_DIR"
    log_ok "$DATA_DIR ready (owned by $REPO_OWNER)"
}

setup_config_h() {
    local example="$REPO_ROOT/config.h.example"
    local dest="$REPO_ROOT/config.h"

    if [ -f "$dest" ]; then
        log_ok "config.h present — edit API keys or use the web portal"
        return 0
    fi

    if [ ! -f "$example" ]; then
        log_warn "config.h.example missing — use web portal or $ENV_DEST"
        return 0
    fi

    log_step "Creating config.h from template"
    cp "$example" "$dest"
    chown "$REPO_OWNER:$REPO_OWNER" "$dest"
    chmod 0644 "$dest"
    log_ok "Created config.h from config.h.example"
}

setup_env_file() {
    if [ -f "$ENV_DEST" ]; then
        log_ok "$ENV_DEST already exists — keeping current configuration"
        # Bookworm labwc/Xwayland pointer-emulates touch (MOUSE* only). An old
        # TOUCH_USE_FINGER_EVENTS=True install silently drops every tap (#14).
        if grep -qE '^[[:space:]]*TOUCH_USE_FINGER_EVENTS=(True|true|1|yes|on)[[:space:]]*$' "$ENV_DEST"; then
            if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
                sed -i 's/^[[:space:]]*TOUCH_USE_FINGER_EVENTS=.*/TOUCH_USE_FINGER_EVENTS=False/' "$ENV_DEST"
                log_ok "Set TOUCH_USE_FINGER_EVENTS=False for Wayland/Xwayland touch (issue #14)"
            else
                log_warn "TOUCH_USE_FINGER_EVENTS is True — if taps do nothing under Xwayland, set it False in $ENV_DEST"
            fi
        fi
    else
        log_step "Creating $ENV_DEST"
        if [ -f "$REPO_ROOT/.env" ]; then
            cp "$REPO_ROOT/.env" "$ENV_DEST"
            log_ok "Copied .env → $ENV_DEST"
        else
            cp "$REPO_ROOT/.env.example" "$ENV_DEST"
            log_ok "Copied .env.example → $ENV_DEST"
        fi
        chown root:root "$ENV_DEST"
        chmod 0600 "$ENV_DEST"
    fi

    setup_config_h
}

install_systemd_service() {
    local service_src="$SETUP_DIR/flightscnr.service"
    local xauthority="${REPO_OWNER_HOME}/.Xauthority"
    local runtime_dir="/run/user/${REPO_OWNER_UID}"

    log_step "Installing systemd service (persists across reboot)"
    sed \
        -e "s|__REPO_DIR__|$REPO_ROOT|g" \
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
    chmod 755 "$SETUP_DIR/portal-update.sh" 2>/dev/null || true
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

install_update_sudoers() {
    local src="$SETUP_DIR/sudoers-flightscnr-update"
    local dest="/etc/sudoers.d/flightscnr-update"
    local update_script="$SETUP_DIR/portal-update.sh"

    if [ ! -f "$src" ]; then
        log_warn "sudoers template missing — portal updates may require manual sudo"
        return 0
    fi

    log_step "Portal update permissions"
    chmod 0755 "$update_script"
    sed \
        -e "s|__REPO_OWNER__|$REPO_OWNER|g" \
        -e "s|__UPDATE_SCRIPT__|$update_script|g" \
        "$src" > "$dest"
    chmod 0440 "$dest"
    if visudo -cf "$dest" >/dev/null 2>&1; then
        log_ok "Installed $dest (passwordless portal updates for $REPO_OWNER)"
    else
        log_warn "sudoers validation failed — removed $dest"
        rm -f "$dest"
    fi
}

cmd_install() {
    local no_start=0
    local skip_apt=0
    for arg in "$@"; do
        case "$arg" in
            --no-start) no_start=1 ;;
            --skip-apt) skip_apt=1 ;;
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

    if [ "$skip_apt" -eq 0 ]; then
        install_apt_packages
    else
        log_ok "Skipped apt packages (--skip-apt)"
    fi
    install_ui_fonts
    install_weather_icons
    install_aircraft_icons
    install_boot_splash
    extract_logos
    setup_venv
    verify_python_deps || true
    setup_data_dir
    setup_env_file
    install_systemd_service
    install_update_sudoers
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
    echo "  Config:    nano $REPO_ROOT/config.h"
    echo "             OR web portal → API Keys (http://raspberrypi.local)"
    echo "             (advanced: sudo nano $ENV_DEST)"
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
        exec sudo bash "$REPO_ROOT/install-pi.sh" install --skip-apt
    else
        cmd_install --skip-apt
    fi
}

usage() {
    cat <<EOF
Usage:
  sudo bash install-pi.sh [install] [--no-start] [--skip-apt]
      First install or full re-sync (includes apt packages)
  bash install-pi.sh update
      git pull + re-sync + restart (skips apt for speed)

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
