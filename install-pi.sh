#!/bin/bash
# install-pi.sh — Install or update FlightScnr Pi on a Raspberry Pi.
#
# Requires: Raspberry Pi OS with desktop (X11 on :0), round touch LCD, network.
# Fresh installs prefer the X11 session (rpd-x) over labwc/Wayland so SDL gets
# real multi-touch (pinch-to-zoom). SDL_VIDEODRIVER=x11 stays as today.
#
# First install (after clone):
#   git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
#   cd ~/FlightScnr_Pi
#   sudo bash install-pi.sh
#
# Update (git pull + re-sync, skips apt for speed):
#   bash ~/FlightScnr_Pi/install-pi.sh update
#
# After an OTA from builds that still ran install in-process (pre-re-exec),
# the app auto-re-syncs install-pi.sh once (stamp mismatch) so users do not
# need a second Update click. Reboot if LightDM switched to rpd-x.
#
# Usage:
#   sudo bash install-pi.sh [install] [--no-start] [--skip-apt]
#   bash install-pi.sh update
#
# If apt fails with "MergeList" / "no Package: header" (corrupt local index):
#   sudo rm -rf /var/lib/apt/lists/*
#   sudo apt-get clean
#   sudo apt-get update
#   sudo bash install-pi.sh
# Or re-run install-pi.sh — it clears lists once and retries automatically.
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
BOOT_CONFIG=""
BOOT_CMDLINE=""
# Set by prefer_x11_session when LightDM was switched off labwc/Wayland.
NEED_REBOOT_FOR_X11=0

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

    local update_log
    update_log="$(mktemp /tmp/flightscnr-apt-update.XXXXXX)"
    if ! apt-get update -qq >"$update_log" 2>&1; then
        cat "$update_log" >&2
        if grep -qiE \
            'MergeList|no Package: header|package lists or status file could not be parsed' \
            "$update_log"
        then
            log_warn "Corrupt apt package lists detected — clearing /var/lib/apt/lists and retrying once"
            rm -rf /var/lib/apt/lists/*
            mkdir -p /var/lib/apt/lists/partial
            apt-get clean || true
            apt-get update -qq
        else
            rm -f "$update_log"
            echo "apt-get update failed (see output above)" >&2
            exit 1
        fi
    fi
    rm -f "$update_log"

    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        build-essential \
        python3-pip python3-venv python3-dev \
        python3-setuptools python3-wheel \
        libsdl2-2.0-0 libsdl2-dev libfreetype6-dev \
        libjpeg-dev zlib1g-dev \
        fonts-dejavu-core \
        plymouth plymouth-themes \
        unzip git curl \
        mpv \
        bluez \
        libspa-0.2-bluetooth \
        pulseaudio-utils
    log_ok "System packages ready"
}

resolve_boot_paths() {
    # Prefer Bookworm firmware partition layout.
    BOOT_CONFIG=""
    BOOT_CMDLINE=""
    if [ -f /boot/firmware/config.txt ]; then
        BOOT_CONFIG="/boot/firmware/config.txt"
        BOOT_CMDLINE="/boot/firmware/cmdline.txt"
    elif [ -f /boot/config.txt ]; then
        BOOT_CONFIG="/boot/config.txt"
        BOOT_CMDLINE="/boot/cmdline.txt"
    fi
}

configure_display_720x720() {
    # Waveshare 4″ DSI (C) is natively 720×720. Persist that mode for labwc
    # (kanshi) and X11 (dispsetup.sh). Never abort install if outputs differ.
    local mode="720x720"
    local kanshi_body
    local dispsetup
    local home_dir owner
    local applied=0

    log_step "Display resolution ${mode}"

    kanshi_body=$(cat <<EOF
# Managed by FlightScnr install-pi.sh — round Waveshare DSI panel.
profile {
    output DSI-1 enable mode ${mode} position 0,0 scale 1
}
profile {
    output DSI-2 enable mode ${mode} position 0,0 scale 1
}
EOF
)

    for owner in "$REPO_OWNER" pi root; do
        home_dir="$(getent passwd "$owner" | cut -d: -f6)"
        [ -n "$home_dir" ] || continue
        mkdir -p "${home_dir}/.config/kanshi"
        if printf '%s\n' "$kanshi_body" > "${home_dir}/.config/kanshi/config"; then
            chown -R "$owner:$owner" "${home_dir}/.config/kanshi" 2>/dev/null || true
            applied=1
        else
            log_warn "Could not write ${home_dir}/.config/kanshi/config (continuing)"
        fi
    done

    # System fallback used by greeter / first boot before a user config exists.
    if mkdir -p /etc/xdg/kanshi 2>/dev/null; then
        if printf '%s\n' "$kanshi_body" > /etc/xdg/kanshi/config 2>/dev/null; then
            applied=1
        fi
    fi

    # Raspberry Pi Desktop X11 screen layout hook (Screen Configuration / arandr).
    dispsetup="/usr/share/dispsetup.sh"
    if cat > "$dispsetup" <<EOF
#!/bin/sh
# Managed by FlightScnr install-pi.sh — force round panel mode on X11.
for out in DSI-1 DSI-2; do
    xrandr --output "\$out" --mode ${mode} --primary 2>/dev/null || true
done
exit 0
EOF
    then
        chmod 0755 "$dispsetup" 2>/dev/null || true
        applied=1
    else
        log_warn "Could not write $dispsetup (continuing)"
    fi

    # Best-effort apply to a live session (no failure if compositor is down).
    if command -v wlr-randr >/dev/null 2>&1; then
        for sock_dir in /run/user/*; do
            [ -d "$sock_dir" ] || continue
            for wd in wayland-1 wayland-0; do
                [ -S "${sock_dir}/${wd}" ] || continue
                for out in DSI-1 DSI-2; do
                    env XDG_RUNTIME_DIR="$sock_dir" WAYLAND_DISPLAY="$wd" \
                        wlr-randr --output "$out" --mode "$mode" >/dev/null 2>&1 || true
                done
            done
        done
    fi
    if [ -n "${DISPLAY:-}" ] || [ -S /tmp/.X11-unix/X0 ]; then
        for out in DSI-1 DSI-2; do
            DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-${REPO_OWNER_HOME}/.Xauthority}" \
                xrandr --output "$out" --mode "$mode" --primary >/dev/null 2>&1 || true
        done
    fi

    if [ "$applied" -eq 1 ]; then
        log_ok "Configured ${mode} via kanshi + dispsetup.sh (DSI-1/DSI-2 when present)"
    else
        log_warn "Could not persist ${mode} display config (continuing)"
    fi
}

install_gpio_fan() {
    # Kernel gpio-fan: on/off on the control wire when SoC hits the threshold.
    # Matches the official Pi case-fan wiring (GPIO 14). temp is millidegrees C.
    # Writes must not abort portal OTA under set -e (vfat remount-ro / full boot
    # partition) — same class of guard as Bluetooth panel edits (5fdb6d4).
    local fan_line="dtoverlay=gpio-fan,gpiopin=14,temp=60000"

    log_step "Case fan (gpio-fan overlay)"

    resolve_boot_paths
    if [ -z "$BOOT_CONFIG" ]; then
        log_warn "Could not find config.txt — skipped gpio-fan overlay"
        return 0
    fi
    if [ ! -w "$BOOT_CONFIG" ]; then
        log_warn "config.txt not writable ($BOOT_CONFIG) — skipped gpio-fan overlay"
        return 0
    fi

    if grep -qE '^\s*dtoverlay=gpio-fan' "$BOOT_CONFIG"; then
        if sed -i "s|^[[:space:]]*dtoverlay=gpio-fan.*|${fan_line}|" "$BOOT_CONFIG"; then
            log_ok "Updated gpio-fan overlay ($BOOT_CONFIG): GPIO 14 @ 60°C"
        else
            log_warn "Could not update gpio-fan overlay in $BOOT_CONFIG (continuing)"
        fi
    else
        if printf '\n# FlightScnr Pi — case fan (GPIO 14 @ 60°C)\n%s\n' "$fan_line" >> "$BOOT_CONFIG"; then
            log_ok "Installed gpio-fan overlay ($BOOT_CONFIG): GPIO 14 @ 60°C"
        else
            log_warn "Could not write gpio-fan overlay to $BOOT_CONFIG (continuing)"
        fi
    fi
}

install_boot_splash() {
    # Custom Plymouth splash + desktop wallpaper + hide firmware rainbow splash.
    local src="$APP_DIR/assets/boot/splash.png"
    local pix_dir="/usr/share/plymouth/themes/pix"
    local pix_splash="$pix_dir/splash.png"
    local wall_dir="/usr/share/rpd-wallpaper"
    local wall_splash="$wall_dir/flightscnr.png"
    local tmp_splash=""

    log_step "Boot splash & wallpaper (FlightScnr)"

    if [ ! -f "$src" ]; then
        log_warn "Missing $src — skipped boot splash / wallpaper install"
        return 0
    fi

    resolve_boot_paths

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
    # labwc's /usr/bin/pcmanfm-pi runs `pcmanfm --desktop` with no -p flag, so the
    # active profile is "default" (not LXDE-pi). Updating only LXDE-pi left
    # /etc/xdg/pcmanfm/default on sunrise.jpg and the desktop never changed.
    if [ -d "$wall_dir" ] || mkdir -p "$wall_dir" 2>/dev/null; then
        install -m 0644 "$tmp_splash" "$wall_splash"

        _set_pcmanfm_wallpaper_conf() {
            local conf="$1"
            mkdir -p "$(dirname "$conf")"
            if [ -f "$conf" ]; then
                if grep -qE '^\s*wallpaper=' "$conf"; then
                    sed -i "s|^[[:space:]]*wallpaper=.*|wallpaper=$wall_splash|" "$conf" || return 0
                else
                    printf 'wallpaper=%s\n' "$wall_splash" >> "$conf" || return 0
                fi
                if ! grep -qE '^\s*wallpaper_mode=' "$conf"; then
                    printf 'wallpaper_mode=crop\n' >> "$conf" || true
                fi
            else
                printf '[*]\nwallpaper_mode=crop\nwallpaper_common=1\nwallpaper=%s\n' \
                    "$wall_splash" > "$conf" || true
            fi
        }

        _refresh_pcmanfm_wallpaper() {
            local desk_user="$1"
            local desk_uid
            id "$desk_user" >/dev/null 2>&1 || return 0
            desk_uid="$(id -u "$desk_user")"
            sudo -u "$desk_user" env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$desk_uid}" \
                pcmanfm --set-wallpaper="$wall_splash" --wallpaper-mode=crop \
                >/dev/null 2>&1 || true
            sudo -u "$desk_user" env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$desk_uid}" \
                pcmanfm --reconfigure >/dev/null 2>&1 || true
        }

        local profile home_dir owner
        for profile in default LXDE-pi; do
            _set_pcmanfm_wallpaper_conf \
                "/etc/xdg/pcmanfm/${profile}/desktop-items-0.conf"
            if [ -f "/etc/xdg/pcmanfm/${profile}/desktop-items-1.conf" ]; then
                _set_pcmanfm_wallpaper_conf \
                    "/etc/xdg/pcmanfm/${profile}/desktop-items-1.conf"
            fi
            for owner in "$REPO_OWNER" pi root; do
                home_dir="$(getent passwd "$owner" | cut -d: -f6)"
                [ -n "$home_dir" ] || continue
                _set_pcmanfm_wallpaper_conf \
                    "${home_dir}/.config/pcmanfm/${profile}/desktop-items-0.conf"
                if [ -f "${home_dir}/.config/pcmanfm/${profile}/desktop-items-1.conf" ]; then
                    _set_pcmanfm_wallpaper_conf \
                        "${home_dir}/.config/pcmanfm/${profile}/desktop-items-1.conf"
                fi
                chown -R "$owner:$owner" "${home_dir}/.config/pcmanfm" 2>/dev/null || true
            done
        done

        _refresh_pcmanfm_wallpaper "${REPO_OWNER:-pi}"
        # Some images autologin the graphical session as root.
        if [ "$(id -u)" -eq 0 ]; then
            env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}" \
                pcmanfm --set-wallpaper="$wall_splash" --wallpaper-mode=crop \
                >/dev/null 2>&1 || true
            env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}" \
                pcmanfm --reconfigure >/dev/null 2>&1 || true
        fi

        unset -f _set_pcmanfm_wallpaper_conf _refresh_pcmanfm_wallpaper
        log_ok "Desktop wallpaper set to FlightScnr splash ($wall_splash)"
    else
        log_warn "Could not create $wall_dir — skipped wallpaper install"
    fi

    rm -f "$tmp_splash"

    if [ -n "$BOOT_CONFIG" ]; then
        if grep -qE '^\s*disable_splash=' "$BOOT_CONFIG"; then
            sed -i 's/^\s*disable_splash=.*/disable_splash=1/' "$BOOT_CONFIG"
        else
            printf '\n# FlightScnr Pi — hide firmware rainbow splash\ndisable_splash=1\n' >> "$BOOT_CONFIG"
        fi
        log_ok "Firmware splash disabled ($BOOT_CONFIG)"
    else
        log_warn "Could not find config.txt — firmware splash unchanged"
    fi

    if [ -n "$BOOT_CMDLINE" ] && [ -f "$BOOT_CMDLINE" ]; then
        # Keep quiet splash for Plymouth; add if missing.
        if ! grep -qw splash "$BOOT_CMDLINE"; then
            # cmdline is a single line
            sed -i 's/$/ splash/' "$BOOT_CMDLINE"
        fi
        if ! grep -qw quiet "$BOOT_CMDLINE"; then
            sed -i 's/$/ quiet/' "$BOOT_CMDLINE"
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
            if unzip -qo -j "$tmp/Inter.zip" \
                "extras/ttf/Inter-Regular.ttf" "extras/ttf/Inter-Bold.ttf" \
                -d "$inter_dir"
            then
                log_ok "Inter fonts ready"
            else
                log_warn "Could not extract Inter fonts — UI may fall back to DejaVu"
            fi
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
        # unzip exit 2 = zip format error; must not abort portal OTA (set -e).
        if unzip -qo "$logo_zip" -d "$REPO_ROOT"; then
            chmod -R a+r "$logo_dir"
            log_ok "Logos extracted to logo/"
        else
            log_warn "Could not extract logo.zip (continuing)"
        fi
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

    # pip uses exit 2 for UNKNOWN_ERROR — do not let a self-upgrade flake abort OTA.
    if ! "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null; then
        log_warn "pip self-upgrade failed (continuing with existing pip)"
    fi
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

lightdm_session_is_wayland() {
    # Prefer LightDM config over $XDG_SESSION_TYPE — installs over SSH are often
    # tty and would miss a labwc autologin session.
    local conf="${1:-/etc/lightdm/lightdm.conf}"
    [ -f "$conf" ] || return 1
    grep -qE '^[[:space:]]*(user-session|autologin-session)=(rpd-labwc|labwc|LXDE-pi-labwc|rpd-wayland)[[:space:]]*$' "$conf"
}

# True when the *live* desktop is still labwc/Xwayland (config may already say X11
# if the machine was never rebooted after prefer_x11_session).
wayland_desktop_still_running() {
    pgrep -x labwc >/dev/null 2>&1 || pgrep -x Xwayland >/dev/null 2>&1
}

prefer_x11_session() {
    # Bookworm defaults to labwc/Wayland. FlightScnr is an SDL X11 client on :0;
    # under Xwayland touch is pointer-emulated (MOUSE* only) so pinch cannot work
    # (issue #21). Mirror raspi-config "W1 X11" so fresh installs get real Xorg
    # multi-touch. Leaves SDL_VIDEODRIVER=x11 unchanged (env + unit already set it).
    local conf="/etc/lightdm/lightdm.conf"
    local xsession wsession xgsession
    local accounts=""

    log_step "Desktop session (X11 for multi-touch / pinch-zoom)"

    if [ ! -f "$conf" ]; then
        log_ok "No LightDM config — skipping session preference"
        return 0
    fi

    if [ -f /usr/share/xsessions/rpd-x.desktop ] \
        || [ -f /usr/share/wayland-sessions/rpd-labwc.desktop ]; then
        xsession=rpd-x
        wsession=rpd-labwc
        xgsession=pi-greeter-x
    elif [ -f /usr/share/xsessions/LXDE-pi-x.desktop ]; then
        xsession=LXDE-pi-x
        wsession=LXDE-pi-labwc
        xgsession=pi-greeter
    else
        log_warn "No Pi X11 session desktop file found — leave LightDM as-is"
        log_warn "Pinch-to-zoom needs real X11 (not Xwayland); see GitHub issue #21"
        return 0
    fi

    if [ ! -f "/usr/share/xsessions/${xsession}.desktop" ]; then
        log_warn "X11 session '${xsession}' missing — leave LightDM as-is"
        return 0
    fi

    if ! lightdm_session_is_wayland "$conf"; then
        if grep -qE "^[[:space:]]*user-session=${xsession}[[:space:]]*$" "$conf" \
            && grep -qE "^[[:space:]]*autologin-session=${xsession}[[:space:]]*$" "$conf"; then
            if wayland_desktop_still_running; then
                # Config was switched earlier but this boot is still labwc/Xwayland.
                NEED_REBOOT_FOR_X11=1
                log_warn "LightDM is set to X11 (${xsession}) but labwc/Xwayland is still running"
                log_warn "Reboot required before pinch-to-zoom will work (issue #21)"
                return 0
            fi
            log_ok "LightDM already on X11 (${xsession}) — pinch multi-touch path OK"
            return 0
        fi
        log_ok "LightDM not on labwc/Wayland — leaving session unchanged"
        return 0
    fi

    sed -i -e "s/^#\\?user-session.*/user-session=${xsession}/" "$conf"
    sed -i -e "s/^#\\?autologin-session.*/autologin-session=${xsession}/" "$conf"
    if [ -f "/usr/share/xgreeters/${xgsession}.desktop" ] \
        || [ -f "/usr/share/lightdm/greeters/${xgsession}.desktop" ]; then
        sed -i -e "s/^#\\?greeter-session.*/greeter-session=${xgsession}/" "$conf"
    fi
    sed -i -e "s/^fallback-test.*/#fallback-test=/" "$conf"
    sed -i -e "s/^fallback-session.*/#fallback-session=/" "$conf"
    sed -i -e "s/^fallback-greeter.*/#fallback-greeter=/" "$conf"

    accounts="/var/lib/AccountsService/users/${REPO_OWNER}"
    if [ -f "$accounts" ]; then
        sed -i -e "s/^XSession=.*/XSession=${xsession}/" "$accounts" || true
    fi

    NEED_REBOOT_FOR_X11=1
    log_ok "Switched LightDM to X11 (${xsession}; was ${wsession}) for pinch-to-zoom"
    log_warn "Reboot required before the X11 session (and pinch) take effect"
    return 0
}

setup_env_file() {
    if [ -f "$ENV_DEST" ]; then
        log_ok "$ENV_DEST already exists — keeping current configuration"
        # Bookworm labwc/Xwayland pointer-emulates touch (MOUSE* only). An old
        # TOUCH_USE_FINGER_EVENTS=True install silently drops every tap (#14).
        # Detect via LightDM config too — SSH installs often have no WAYLAND_*.
        if grep -qE '^[[:space:]]*TOUCH_USE_FINGER_EVENTS=(True|true|1|yes|on)[[:space:]]*$' "$ENV_DEST"; then
            if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] \
                || [ -n "${WAYLAND_DISPLAY:-}" ] \
                || lightdm_session_is_wayland \
                || [ "${NEED_REBOOT_FOR_X11:-0}" -eq 1 ]; then
                sed -i 's/^[[:space:]]*TOUCH_USE_FINGER_EVENTS=.*/TOUCH_USE_FINGER_EVENTS=False/' "$ENV_DEST"
                log_ok "Set TOUCH_USE_FINGER_EVENTS=False for safe taps (issue #14)"
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

suppress_desktop_bluetooth_popups() {
    # Raspberry Pi OS panel plugins (lxplug-bluetooth / wfplug-bluetooth, and
    # wfplug-volumepulse for BT audio) pop a "Connection successful" dialog or a
    # "<device> Connected N%" notification on every BlueZ connect/disconnect.
    # Those steal focus from fullscreen FlightScnr. Pairing is done via the web
    # portal, so drop the panel widget and the panel notification server —
    # BlueZ/PipeWire still work for ATC audio.
    log_step "Suppressing desktop Bluetooth pair/connect popups"

    local changed=0
    local panel
    local panels=(
        "${REPO_OWNER_HOME}/.config/lxpanel/LXDE-pi/panels/panel"
        "/etc/xdg/lxpanel/LXDE-pi/panels/panel"
        "/root/.config/lxpanel/LXDE-pi/panels/panel"
    )

    for panel in "${panels[@]}"; do
        if [ -f "$panel" ] && grep -q 'type=bluetooth' "$panel"; then
            # Drop the Plugin { type=bluetooth ... } block (and a following blank line).
            # A failure here must not abort the install (set -e), so keep it guarded.
            if python3 - "$panel" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
new, n = re.subn(
    r"(?ms)^Plugin \{\n(?:[^\n]*\n)*?[ \t]*type=bluetooth\n(?:[^\n]*\n)*?^\}\n?",
    "",
    text,
)
if not n:
    sys.exit(1)
path.write_text(new, encoding="utf-8")
PY
            then
                changed=1
                log_ok "Removed bluetooth plugin from $panel"
            else
                log_warn "Could not strip bluetooth plugin from $panel (continuing)"
            fi
        fi
    done

    # wf-panel-pi (Wayland). An absent widgets_right falls back to the compiled
    # default, which includes the bluetooth widget — so the key must be written
    # explicitly, not just filtered. notify_enable=false also stops volumepulse
    # from popping "Connected"/battery toasts over fullscreen FlightScnr.
    local wf_ini="${REPO_OWNER_HOME}/.config/wf-panel-pi.ini"
    mkdir -p "$(dirname "$wf_ini")"
    # Prefer stdout markers over sys.exit(2): that code became portal
    # "Update failed (exit 2)" whenever set -e / $? handling missed it (#77).
    local wf_rc=0
    local wf_out=""
    wf_out="$(python3 - "$wf_ini" 2>/dev/null <<'PY'
import pathlib, re, sys

FALLBACK_RIGHT = (
    "tray power ejecter updater spacing2 connect spacing2 bluetooth spacing2 "
    "netman spacing2 volumepulse spacing2 clock spacing2 batt spacing2 squeek"
)
METADATA = pathlib.Path("/usr/share/wf-panel-pi/metadata/panel-pi.xml")


def packaged_default():
    try:
        meta = METADATA.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_RIGHT
    m = re.search(
        r'<option name="widgets_right".*?<default>(.*?)</default>',
        meta,
        re.S,
    )
    return m.group(1).strip() if m else FALLBACK_RIGHT


def without_bluetooth(widgets):
    kept = []
    for token in widgets.split():
        if token == "bluetooth":
            continue
        # Dropping a widget can leave two spacers side by side.
        if re.fullmatch(r"spacing\d*", token) and kept and re.fullmatch(r"spacing\d*", kept[-1]):
            continue
        kept.append(token)
    return " ".join(kept)


def set_key(text, key, value):
    pattern = re.compile(rf"(?m)^[ \t]*{key}[ \t]*=.*$")
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return re.sub(r"(?m)^(\[panel\][ \t]*\n)", rf"\g<1>{key}={value}\n", text, count=1)


path = pathlib.Path(sys.argv[1])
original = path.read_text(encoding="utf-8") if path.exists() else "[panel]\n"
text = original
if not re.search(r"(?m)^\[panel\]", text):
    text = "[panel]\n" + text

current = re.search(r"(?m)^[ \t]*widgets_right[ \t]*=(.*)$", text)
text = set_key(text, "widgets_right", without_bluetooth(current.group(1) if current else packaged_default()))
text = set_key(text, "notify_enable", "false")

if text == original:
    print("UNCHANGED")
else:
    path.write_text(text, encoding="utf-8")
    print("UPDATED")
PY
)" || wf_rc=$?
    if [ "$wf_rc" -eq 0 ] && [ "$wf_out" = "UPDATED" ]; then
        if [ -n "${REPO_OWNER:-}" ]; then
            chown "$REPO_OWNER:$REPO_OWNER" "$wf_ini" 2>/dev/null || true
        fi
        changed=1
        log_ok "Disabled bluetooth widget + panel notifications in $wf_ini"
    elif [ "$wf_rc" -eq 0 ]; then
        log_ok "wf-panel-pi already has bluetooth widget + notifications disabled"
    else
        log_warn "Could not update $wf_ini (continuing)"
    fi

    if [ "$changed" -eq 1 ]; then
        # Reload panel if one is running (best-effort; ignore failures).
        if pgrep -x lxpanel >/dev/null 2>&1; then
            killall -q lxpanel 2>/dev/null || true
            log_ok "Restarted lxpanel (will respawn with desktop session)"
        fi
        if pgrep -x wf-panel-pi >/dev/null 2>&1; then
            # Nothing respawns it inside a running Wayland session; it comes back
            # at next login, reading the config written above.
            killall -q wf-panel-pi 2>/dev/null || true
            log_ok "Stopped wf-panel-pi (returns on next boot without the popups)"
        fi
    else
        log_ok "Desktop Bluetooth panel plugin already disabled (or not present)"
    fi
}

install_systemd_service() {
    local service_src="$SETUP_DIR/flightscnr.service"
    local xauthority="${REPO_OWNER_HOME}/.Xauthority"
    local runtime_dir="/run/user/${REPO_OWNER_UID}"
    local pulse_cookie="${REPO_OWNER_HOME}/.config/pulse/cookie"

    log_step "Installing systemd service (persists across reboot)"
    mkdir -p /etc/NetworkManager/dnsmasq-shared.d
    sed \
        -e "s|__REPO_DIR__|$REPO_ROOT|g" \
        -e "s|__XAUTHORITY__|$xauthority|g" \
        -e "s|__XDG_RUNTIME_DIR__|$runtime_dir|g" \
        -e "s|__PULSE_COOKIE__|$pulse_cookie|g" \
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
    # Preserve +x on release helpers — a blanket chmod 644 leaves git "dirty"
    # (mode 100755→100644) and blocks the next `git pull --ff-only` OTA.
    if [ -d "$REPO_ROOT/scripts" ]; then
        find "$REPO_ROOT/scripts" -type f \( -name '*.sh' -o -name '*.cmd' \) \
            -exec chmod 755 {} + 2>/dev/null || true
    fi
    chmod 755 "$VENV_DIR/bin/"* 2>/dev/null || true
    log_ok "Repo owned by $REPO_OWNER"
}

# Drop install-induced dirt that would abort `git pull --ff-only` (notably
# scripts/release.sh executable-bit flips from older fix_repo_permissions).
prepare_repo_for_pull() {
    local git_safe=("$@")
    local rel
    for rel in scripts/release.sh scripts/release.cmd scripts/dev-release.sh scripts/repair-ota.sh; do
        if "${git_safe[@]}" status --porcelain -- "$rel" 2>/dev/null | grep -q .; then
            log_step "Clearing local changes that block pull ($rel)"
            "${git_safe[@]}" restore --source=HEAD --staged --worktree -- "$rel" 2>/dev/null \
                || "${git_safe[@]}" checkout HEAD -- "$rel" 2>/dev/null \
                || true
        fi
    done
}

start_service() {
    if [ "${FLIGHTSCNR_SKIP_RESTART:-}" = "1" ]; then
        log_ok "Skipped service restart (FLIGHTSCNR_SKIP_RESTART=1)"
        return 0
    fi
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

write_install_stamp() {
    # Record which install-pi.sh body last completed successfully so the app
    # can auto-re-sync after OTAs that pulled a newer installer but ran the
    # old in-memory one (pre-re-exec update path).
    local stamp="$DATA_DIR/install-script.sha256"
    local script="$REPO_ROOT/install-pi.sh"
    mkdir -p "$DATA_DIR"
    if [ ! -f "$script" ]; then
        log_warn "install stamp skipped — missing $script"
        return 0
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$script" | awk '{print $1}' >"$stamp"
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$script" | awk '{print $1}' >"$stamp"
    else
        log_warn "install stamp skipped — no sha256sum/shasum"
        return 0
    fi
    chmod 644 "$stamp" 2>/dev/null || true
    log_ok "Wrote install stamp ($(tr -d '[:space:]' <"$stamp" | cut -c1-12)…)"
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
    # Before splash/UI assets so the panel is in native mode as early as possible.
    configure_display_720x720
    install_ui_fonts
    install_weather_icons
    install_aircraft_icons
    install_boot_splash
    install_gpio_fan
    extract_logos
    setup_venv
    verify_python_deps || true
    setup_data_dir
    prefer_x11_session
    setup_env_file
    suppress_desktop_bluetooth_popups
    install_systemd_service
    install_update_sudoers
    fix_repo_permissions
    write_install_stamp

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
    if [ "${NEED_REBOOT_FOR_X11:-0}" -eq 1 ]; then
        echo "  Reboot:    REQUIRED now — switched desktop to X11 for pinch-to-zoom"
        echo "             (sudo reboot). Afterward: pinch on radar should change range."
    else
        echo "  Reboot:    starts automatically (systemctl is-enabled flightscnr)"
    fi
    echo "  Fan:       gpio-fan on GPIO 14 @ 60°C (reboot once if this install just added it)"
    echo "  Update:    bash $REPO_ROOT/install-pi.sh update"
    echo ""
}

cmd_update() {
    local no_start=0
    for arg in "$@"; do
        case "$arg" in
            --no-start) no_start=1 ;;
            *) echo "Unknown option: $arg" >&2; exit 1 ;;
        esac
    done

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
    # Match utilities/updater.py: always pass safe.directory so root-run portal
    # updates can read a pi-owned checkout (Git 2.35+ dubious-ownership checks).
    local git_safe=(git -c "safe.directory=${REPO_ROOT}" -C "$REPO_ROOT")
    prepare_repo_for_pull "${git_safe[@]}"
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        sudo -u "$SUDO_USER" "${git_safe[@]}" pull --ff-only
    elif [ "$(id -u)" -eq 0 ]; then
        sudo -u "$REPO_OWNER" "${git_safe[@]}" pull --ff-only
    else
        "${git_safe[@]}" pull --ff-only
    fi
    log_ok "Git pull complete ($("${git_safe[@]}" log --oneline -1 2>/dev/null || true))"

    local install_args=(--skip-apt)
    if [ "$no_start" -eq 1 ]; then
        install_args+=(--no-start)
    fi

    # Always re-exec the post-pull install-pi.sh. Calling cmd_install in-process
    # would keep running the *pre-pull* script still loaded in memory — so OTA
    # from an old build would skip new steps (X11 session, 720x720, fan guards).
    echo ""
    echo "Re-syncing with updated installer..."
    if [ "$(id -u)" -ne 0 ]; then
        exec sudo bash "$REPO_ROOT/install-pi.sh" install "${install_args[@]}"
    else
        exec bash "$REPO_ROOT/install-pi.sh" install "${install_args[@]}"
    fi
}

usage() {
    cat <<EOF
Usage:
  sudo bash install-pi.sh [install] [--no-start] [--skip-apt]
      First install or full re-sync (includes apt packages)
  bash install-pi.sh update [--no-start]
      git pull + re-sync + restart (skips apt for speed)
      --no-start  skip service restart (portal update schedules it after status write)

If apt fails with MergeList / "no Package: header" (corrupt index cache):
  sudo rm -rf /var/lib/apt/lists/* && sudo apt-get clean && sudo apt-get update
  sudo bash install-pi.sh
  (install also auto-clears lists once and retries)

EOF
}

# --- main ---
# Default to install when argv is empty (README: `sudo bash install-pi.sh`).
# Do not bare-`shift` with zero args: under `set -euo pipefail` that exits 1
# before cmd_install runs (GitHub issues #8 / #9).
case "${1:-install}" in
    install)
        [ $# -gt 0 ] && shift
        cmd_install "$@"
        ;;
    update)
        shift
        cmd_update "$@"
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
