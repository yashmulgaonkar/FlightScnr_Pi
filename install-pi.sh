#!/bin/bash
# install-pi.sh — Install or update FlightScnr Pi on a Raspberry Pi.
#
# Requires: Raspberry Pi OS with desktop (X11 on :0), round touch LCD, network.
# Fresh installs force the X11 session (rpd-x) over labwc/Wayland so SDL gets
# real multi-touch (pinch-to-zoom), then auto-reboot when needed. Users should
# not need raspi-config. SDL_VIDEODRIVER=x11 stays as today.
#
# First install (after clone):
#   git clone https://github.com/yashmulgaonkar/FlightScnr_Pi.git ~/FlightScnr_Pi
#   cd ~/FlightScnr_Pi
#   sudo bash install-pi.sh
#
# Update (sync origin/main + re-sync, skips apt for speed):
#   bash ~/FlightScnr_Pi/install-pi.sh update
#
# After an OTA from builds that still ran install in-process (pre-re-exec),
# the app auto-re-syncs install-pi.sh once (stamp mismatch) so users do not
# need a second Update click. Auto-reboots if LightDM switched to rpd-x.
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

# Snapshot to /tmp before doing work. Bash keeps reading this file as it runs;
# if git/an editor rewrites it mid-install, the tail can hit a stray `;;` (exit 2).
# `install-pi.sh update` still re-execs the on-disk script after pull (new steps).
if [ -z "${FLIGHTSCNR_INSTALL_SNAPSHOT:-}" ] \
    && [ "${FLIGHTSCNR_NO_INSTALL_SNAPSHOT:-}" != "1" ]; then
    _install_src="$0"
    if [ "${_install_src#/}" = "$_install_src" ]; then
        _install_src="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    fi
    if [ ! -f "$_install_src" ]; then
        echo "install-pi.sh not found: $_install_src" >&2
        exit 1
    fi
    _install_snap="$(mktemp /tmp/flightscnr-install-pi.XXXXXX.sh)"
    cp "$_install_src" "$_install_snap"
    chmod 700 "$_install_snap"
    export FLIGHTSCNR_INSTALL_SNAPSHOT=1
    export FLIGHTSCNR_INSTALL_PI="$_install_src"
    export FLIGHTSCNR_INSTALL_SNAPSHOT_PATH="$_install_snap"
    exec bash "$_install_snap" "$@"
fi
if [ -n "${FLIGHTSCNR_INSTALL_SNAPSHOT_PATH:-}" ]; then
    trap 'rm -f "${FLIGHTSCNR_INSTALL_SNAPSHOT_PATH:-}"' EXIT
fi

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
    # Prefer the real checkout path when running from a /tmp snapshot.
    local src="${FLIGHTSCNR_INSTALL_PI:-$0}"
    if [ "${src#/}" = "$src" ]; then
        src="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")"
    fi
    REPO_ROOT="$(cd "$(dirname "$src")" && pwd)"
    SETUP_DIR="$REPO_ROOT/flightscnr/setup"
    APP_DIR="$REPO_ROOT/flightscnr"
    VENV_DIR="$REPO_ROOT/flightscnr-venv"

    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        REPO_OWNER="$SUDO_USER"
    else
        REPO_OWNER="$(stat -c '%U' "$REPO_ROOT")"
    fi

    if ! id -u "$REPO_OWNER" >/dev/null 2>&1; then
        echo "Could not resolve install owner '$REPO_OWNER' (SUDO_USER or repo ownership)." >&2
        exit 1
    fi
    # Fail clearly if the owner has no passwd home (do not abort with bare exit 2).
    REPO_OWNER_HOME="$(getent passwd "$REPO_OWNER" | cut -d: -f6 || true)"
    REPO_OWNER_UID="$(id -u "$REPO_OWNER")"
    if [ -z "$REPO_OWNER_HOME" ]; then
        echo "Could not resolve home directory for install owner '$REPO_OWNER'." >&2
        exit 1
    fi
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

# Bound a command so a hung child cannot wedge portal OTA. If GNU timeout is
# missing, run unwrapped — skipping the work (fetch/pip) would strand devices.
run_with_timeout() {
    local kill_after="$1"
    local wait_s="$2"
    shift 2
    if command -v timeout >/dev/null 2>&1; then
        timeout -k "$kill_after" "$wait_s" "$@"
    else
        "$@"
    fi
}

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
        pulseaudio-utils \
        rfkill \
        iw
    log_ok "System packages ready"
}

ensure_bluetooth_ready() {
    # Fresh Bookworm images often soft-block Bluetooth (rfkill) and/or leave
    # bluetoothd inactive until the desktop tray toggles it. FlightScnr pairs
    # speakers via bluetoothctl with no tray — unblock + enable here.
    local conf="/etc/bluetooth/main.conf"

    log_step "Bluetooth (adapter for speaker pairing)"

    if command -v rfkill >/dev/null 2>&1; then
        rfkill unblock bluetooth >/dev/null 2>&1 || true
        log_ok "rfkill unblock bluetooth"
    else
        log_warn "rfkill not installed — skipping unblock"
    fi

    if systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
        systemctl enable bluetooth.service >/dev/null 2>&1 || true
        if systemctl start bluetooth.service >/dev/null 2>&1; then
            log_ok "bluetooth.service enabled and started"
        else
            log_warn "Could not start bluetooth.service (continuing)"
        fi
    else
        log_warn "bluetooth.service not found — is bluez installed?"
    fi

    if [ -f "$conf" ]; then
        if grep -qE '^[[:space:]]*AutoEnable=' "$conf"; then
            sed -i -e 's/^[[:space:]]*AutoEnable=.*/AutoEnable=true/' "$conf" || true
        elif grep -qE '^[[:space:]]*#[[:space:]]*AutoEnable=' "$conf"; then
            sed -i -e 's/^[[:space:]]*#[[:space:]]*AutoEnable=.*/AutoEnable=true/' "$conf" || true
        else
            printf '\n# FlightScnr Pi — power adapter on boot for speaker pairing\nAutoEnable=true\n' >> "$conf" || true
        fi
        log_ok "BlueZ AutoEnable=true ($conf)"
    fi

    if command -v bluetoothctl >/dev/null 2>&1; then
        run_with_timeout 5 15 bluetoothctl power on >/dev/null 2>&1 || true
        run_with_timeout 5 15 bluetoothctl pairable on >/dev/null 2>&1 || true
    fi
}

install_wifi_powersave_off() {
    # Wall-powered kiosk: IEEE 802.11 power save stalls phone hotspots and
    # Bluetooth coexistence on brcmfmac (BCM43455). 2 = disable.
    # Does not bounce NetworkManager / wlan0 — live iw + profile update only.
    local nm_conf_src="$SETUP_DIR/wifi-powersave-off.conf"
    local nm_conf_dest="/etc/NetworkManager/conf.d/99-wifi-powersave-off.conf"
    local disp_src="$SETUP_DIR/99-wifi-powersave-off"
    local disp_dest="/etc/NetworkManager/dispatcher.d/99-wifi-powersave-off"
    local name type iface

    log_step "Wi-Fi power save off (kiosk)"

    mkdir -p /etc/NetworkManager/conf.d /etc/NetworkManager/dispatcher.d

    if [ -f "$nm_conf_src" ]; then
        cp "$nm_conf_src" "$nm_conf_dest"
        chmod 0644 "$nm_conf_dest"
        log_ok "Installed $nm_conf_dest"
    else
        printf '%s\n' \
            '[connection]' \
            'wifi.powersave=2' \
            > "$nm_conf_dest"
        chmod 0644 "$nm_conf_dest"
        log_ok "Wrote $nm_conf_dest (inline)"
    fi

    if [ -f "$disp_src" ]; then
        cp "$disp_src" "$disp_dest"
        chmod 0755 "$disp_dest"
        log_ok "Installed $disp_dest"
    else
        log_warn "dispatcher script missing — $disp_src"
    fi

    if command -v nmcli >/dev/null 2>&1; then
        nmcli general reload >/dev/null 2>&1 || true
        while IFS=: read -r name type; do
            [ "$type" = "802-11-wireless" ] || continue
            [ -n "$name" ] || continue
            nmcli connection modify "$name" wifi.powersave 2 >/dev/null 2>&1 || true
        done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null || true)
        log_ok "wifi.powersave=2 on saved Wi-Fi profiles"
    fi

    if command -v iw >/dev/null 2>&1; then
        shopt -s nullglob
        for iface in /sys/class/net/wl* /sys/class/net/wlan*; do
            [ -e "$iface" ] || continue
            iw dev "$(basename "$iface")" set power_save off >/dev/null 2>&1 || true
        done
        shopt -u nullglob
        log_ok "iw power_save off on live Wi-Fi interfaces"
    else
        log_warn "iw not installed — live power_save not changed this run"
    fi
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

    # Per-user kanshi for the install owner.
    if [ -n "${REPO_OWNER_HOME:-}" ]; then
        mkdir -p "${REPO_OWNER_HOME}/.config/kanshi"
        if printf '%s\n' "$kanshi_body" > "${REPO_OWNER_HOME}/.config/kanshi/config"; then
            chown -R "$REPO_OWNER:" "${REPO_OWNER_HOME}/.config/kanshi" 2>/dev/null || true
            applied=1
        else
            log_warn "Could not write ${REPO_OWNER_HOME}/.config/kanshi/config (continuing)"
        fi
    fi

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
                run_with_timeout 5 10 xrandr --output "$out" --mode "$mode" --primary \
                >/dev/null 2>&1 || true
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
    local splash_hash=""
    local splash_stamp="${DATA_DIR}/plymouth-initramfs.sha256"
    local plymouth_theme=""
    local need_initramfs=0

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
            plymouth_theme="$(plymouth-set-default-theme 2>/dev/null || true)"
            plymouth-set-default-theme pix >/dev/null 2>&1 || true
            if command -v sha256sum >/dev/null 2>&1; then
                splash_hash="$(sha256sum "$pix_splash" | awk '{print $1}')"
            fi
            # Skip initramfs unless the splash bytes changed or the theme was
            # not pix. Stamp only on success so a failed rebuild still retries
            # next OTA (do not skip forever after one full-/boot failure).
            if [ -z "$splash_hash" ] \
                || [ "$plymouth_theme" != "pix" ] \
                || [ "$(cat "$splash_stamp" 2>/dev/null || true)" != "$splash_hash" ]
            then
                need_initramfs=1
            fi
            if command -v update-initramfs >/dev/null 2>&1 && [ "$need_initramfs" -eq 1 ]; then
                if run_with_timeout 30 600 update-initramfs -u >/dev/null 2>&1; then
                    if [ -n "$splash_hash" ]; then
                        mkdir -p "$DATA_DIR"
                        printf '%s\n' "$splash_hash" >"$splash_stamp" || true
                        chmod 644 "$splash_stamp" 2>/dev/null || true
                    fi
                    log_ok "Plymouth theme set to pix (initramfs updated)"
                else
                    log_warn "update-initramfs failed or timed out (splash may need a reboot once)"
                fi
            else
                log_ok "Plymouth theme set to pix (initramfs unchanged)"
            fi
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
                timeout -k 5 10 pcmanfm --set-wallpaper="$wall_splash" --wallpaper-mode=crop \
                >/dev/null 2>&1 || true
            sudo -u "$desk_user" env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$desk_uid}" \
                timeout -k 5 10 pcmanfm --reconfigure >/dev/null 2>&1 || true
        }

        local profile
        for profile in default LXDE-pi; do
            _set_pcmanfm_wallpaper_conf \
                "/etc/xdg/pcmanfm/${profile}/desktop-items-0.conf"
            if [ -f "/etc/xdg/pcmanfm/${profile}/desktop-items-1.conf" ]; then
                _set_pcmanfm_wallpaper_conf \
                    "/etc/xdg/pcmanfm/${profile}/desktop-items-1.conf"
            fi
            # Per-user pcmanfm for the install owner.
            if [ -n "${REPO_OWNER_HOME:-}" ]; then
                _set_pcmanfm_wallpaper_conf \
                    "${REPO_OWNER_HOME}/.config/pcmanfm/${profile}/desktop-items-0.conf"
                if [ -f "${REPO_OWNER_HOME}/.config/pcmanfm/${profile}/desktop-items-1.conf" ]; then
                    _set_pcmanfm_wallpaper_conf \
                        "${REPO_OWNER_HOME}/.config/pcmanfm/${profile}/desktop-items-1.conf"
                fi
                chown -R "$REPO_OWNER:" \
                    "${REPO_OWNER_HOME}/.config/pcmanfm" 2>/dev/null || true
            fi
        done

        _refresh_pcmanfm_wallpaper "$REPO_OWNER"
        # Some images autologin the graphical session as root.
        if [ "$(id -u)" -eq 0 ]; then
            env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}" \
                timeout -k 5 10 pcmanfm --set-wallpaper="$wall_splash" --wallpaper-mode=crop \
                >/dev/null 2>&1 || true
            env DISPLAY="${DISPLAY:-:0}" \
                XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}" \
                timeout -k 5 10 pcmanfm --reconfigure >/dev/null 2>&1 || true
        fi

        unset -f _set_pcmanfm_wallpaper_conf _refresh_pcmanfm_wallpaper
        log_ok "Desktop wallpaper set to FlightScnr splash ($wall_splash)"
    else
        log_warn "Could not create $wall_dir — skipped wallpaper install"
    fi

    rm -f "$tmp_splash"

    # Same vfat remount-ro / full-partition guard as install_gpio_fan — these
    # writes must not abort portal OTA under set -e.
    if [ -z "$BOOT_CONFIG" ]; then
        log_warn "Could not find config.txt — firmware splash unchanged"
    elif [ ! -w "$BOOT_CONFIG" ]; then
        log_warn "config.txt not writable ($BOOT_CONFIG) — firmware splash unchanged"
    elif grep -qE '^\s*disable_splash=' "$BOOT_CONFIG"; then
        if sed -i 's/^\s*disable_splash=.*/disable_splash=1/' "$BOOT_CONFIG"; then
            log_ok "Firmware splash disabled ($BOOT_CONFIG)"
        else
            log_warn "Could not update disable_splash in $BOOT_CONFIG (continuing)"
        fi
    else
        if printf '\n# FlightScnr Pi — hide firmware rainbow splash\ndisable_splash=1\n' >> "$BOOT_CONFIG"; then
            log_ok "Firmware splash disabled ($BOOT_CONFIG)"
        else
            log_warn "Could not write disable_splash to $BOOT_CONFIG (continuing)"
        fi
    fi

    if [ -n "$BOOT_CMDLINE" ] && [ -f "$BOOT_CMDLINE" ]; then
        if [ ! -w "$BOOT_CMDLINE" ]; then
            log_warn "cmdline.txt not writable ($BOOT_CMDLINE) — splash/quiet unchanged"
        else
            # Keep quiet splash for Plymouth; add if missing. cmdline is one line.
            if ! grep -qw splash "$BOOT_CMDLINE"; then
                if ! sed -i 's/$/ splash/' "$BOOT_CMDLINE"; then
                    log_warn "Could not add splash to $BOOT_CMDLINE (continuing)"
                fi
            fi
            if ! grep -qw quiet "$BOOT_CMDLINE"; then
                if ! sed -i 's/$/ quiet/' "$BOOT_CMDLINE"; then
                    log_warn "Could not add quiet to $BOOT_CMDLINE (continuing)"
                fi
            fi
            if grep -qw splash "$BOOT_CMDLINE" && grep -qw quiet "$BOOT_CMDLINE"; then
                log_ok "Kernel cmdline keeps quiet splash"
            fi
        fi
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
    if ! run_with_timeout 15 180 "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null; then
        log_warn "pip self-upgrade failed (continuing with existing pip)"
    fi
    # Bound a hung index/wheel fetch. 20 minutes is enough for a Pi native
    # wheel; still fail-closed so a broken venv does not write the install
    # stamp (auto-resync can retry). Do not || true — that would skip retry.
    if ! run_with_timeout 30 1200 "$VENV_DIR/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt"; then
        echo "pip install -r requirements.txt failed or timed out" >&2
        return 1
    fi
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
    chown -R "$REPO_OWNER:" "$DATA_DIR"
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
    chown "$REPO_OWNER:" "$dest"
    chmod 0644 "$dest"
    log_ok "Created config.h from config.h.example"
}

REBOOT_X11_FLAG="${DATA_DIR}/need-reboot-for-x11"

lightdm_session_is_wayland() {
    # Prefer LightDM config over $XDG_SESSION_TYPE — installs over SSH are often
    # tty and would miss a labwc autologin session.
    local conf="${1:-/etc/lightdm/lightdm.conf}"
    [ -f "$conf" ] || return 1
    grep -qE '^[[:space:]]*(user-session|autologin-session)=(rpd-labwc|labwc|LXDE-pi-labwc|LXDE-pi-wayland|rpd-wayland)[[:space:]]*$' "$conf"
}

# True when the *live* desktop is still labwc/Xwayland (config may already say X11
# if the machine was never rebooted after prefer_x11_session).
wayland_desktop_still_running() {
    pgrep -x labwc >/dev/null 2>&1 || pgrep -x Xwayland >/dev/null 2>&1
}

lightdm_on_x11_session() {
    local conf="$1"
    local xsession="$2"
    grep -qE "^[[:space:]]*user-session=${xsession}[[:space:]]*$" "$conf" \
        && grep -qE "^[[:space:]]*autologin-session=${xsession}[[:space:]]*$" "$conf"
}

# Ensure a LightDM [Seat:*] key exists (create or uncomment), then set its value.
# Mirrors raspi-config do_wayland W1; also handles images missing the key entirely.
_set_lightdm_seat_key() {
    local conf="$1"
    local key="$2"
    local value="$3"
    if grep -qE "^#?[[:space:]]*${key}=" "$conf"; then
        sed -i -e "s/^#\\?[[:space:]]*${key}.*/${key}=${value}/" "$conf"
        return 0
    fi
    if grep -qE '^\[Seat:\*\]' "$conf"; then
        sed -i "/^\[Seat:\*\]/a ${key}=${value}" "$conf"
    else
        printf '\n[Seat:*]\n%s=%s\n' "$key" "$value" >> "$conf"
    fi
}

_mark_reboot_for_x11() {
    NEED_REBOOT_FOR_X11=1
    mkdir -p "$DATA_DIR"
    printf 'x11\n' >"$REBOOT_X11_FLAG"
    chmod 644 "$REBOOT_X11_FLAG" 2>/dev/null || true
}

_clear_reboot_for_x11() {
    rm -f "$REBOOT_X11_FLAG"
}

prefer_x11_session() {
    # Bookworm/Trixie default to labwc/Wayland. FlightScnr is an SDL X11 client
    # on :0; under Xwayland touch is pointer-emulated (MOUSE* only) so pinch
    # cannot work (issue #21). Always force the Pi OS X11 session (same as
    # raspi-config "W1 X11") — do not leave LightDM alone just because the
    # current session name is unfamiliar. Leaves SDL_VIDEODRIVER=x11 unchanged.
    local conf="/etc/lightdm/lightdm.conf"
    local xsession wsession xgsession
    local accounts=""
    local switched=0

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

    if lightdm_on_x11_session "$conf" "$xsession"; then
        if wayland_desktop_still_running; then
            # Config was switched earlier but this boot is still labwc/Xwayland.
            _mark_reboot_for_x11
            log_warn "LightDM is set to X11 (${xsession}) but labwc/Xwayland is still running"
            log_warn "Will reboot automatically so pinch-to-zoom can take effect"
            return 0
        fi
        _clear_reboot_for_x11
        rm -f "${DATA_DIR}/reboot-in-progress"
        log_ok "LightDM already on X11 (${xsession}) — pinch multi-touch path OK"
        return 0
    fi

    # Prefer raspi-config when present (tracks OS session/greeter naming).
    if command -v raspi-config >/dev/null 2>&1; then
        if raspi-config nonint do_wayland W1 >/dev/null 2>&1; then
            switched=1
            log_ok "raspi-config nonint do_wayland W1 → X11 (${xsession})"
        else
            log_warn "raspi-config nonint do_wayland W1 failed — applying LightDM edits directly"
        fi
    fi

    # Always apply the same LightDM edits raspi-config uses, so we still win on
    # images where nonint is missing/broken or left greeter/AccountsService stale.
    _set_lightdm_seat_key "$conf" user-session "$xsession"
    _set_lightdm_seat_key "$conf" autologin-session "$xsession"
    if [ -f "/usr/share/xgreeters/${xgsession}.desktop" ] \
        || [ -f "/usr/share/lightdm/greeters/${xgsession}.desktop" ]; then
        _set_lightdm_seat_key "$conf" greeter-session "$xgsession"
    fi
    sed -i -e "s/^fallback-test.*/#fallback-test=/" "$conf"
    sed -i -e "s/^fallback-session.*/#fallback-session=/" "$conf"
    sed -i -e "s/^fallback-greeter.*/#fallback-greeter=/" "$conf"

    accounts="/var/lib/AccountsService/users/${REPO_OWNER}"
    if [ -f "$accounts" ]; then
        if grep -qE '^XSession=' "$accounts"; then
            sed -i -e "s/^XSession=.*/XSession=${xsession}/" "$accounts" || true
        else
            printf 'XSession=%s\n' "$xsession" >> "$accounts" || true
        fi
    fi

    if ! lightdm_on_x11_session "$conf" "$xsession"; then
        log_warn "Could not set LightDM to ${xsession} — pinch may stay unavailable"
        return 0
    fi

    _mark_reboot_for_x11
    if [ "$switched" -eq 1 ]; then
        log_ok "Confirmed LightDM on X11 (${xsession}) for pinch-to-zoom"
    else
        log_ok "Switched LightDM to X11 (${xsession}; was ${wsession}) for pinch-to-zoom"
    fi
    log_warn "Reboot will be scheduled so the X11 session (and pinch) take effect"
    return 0
}

schedule_reboot_for_x11() {
    # Pinch needs a real Xorg session; config changes only apply after reboot.
    # Auto-reboot so fresh installs do not require raspi-config or a manual reboot.
    local delay_s="${FLIGHTSCNR_X11_REBOOT_DELAY_S:-8}"
    local unit="flightscnr-x11-reboot-$$"
    local progress="${DATA_DIR}/reboot-in-progress"

    if [ "${NEED_REBOOT_FOR_X11:-0}" -ne 1 ] && [ ! -f "$REBOOT_X11_FLAG" ]; then
        return 0
    fi
    if [ "${FLIGHTSCNR_NO_AUTO_REBOOT:-}" = "1" ]; then
        log_warn "X11 reboot needed but FLIGHTSCNR_NO_AUTO_REBOOT=1 — run: sudo reboot"
        return 0
    fi

    # On-screen modal in the display app while we wait for the reboot.
    mkdir -p "$DATA_DIR"
    printf 'x11\n' >"$progress"
    chmod 644 "$progress" 2>/dev/null || true

    log_step "Scheduling reboot for X11 / pinch-to-zoom (${delay_s}s)"
    if command -v systemd-run >/dev/null 2>&1; then
        if systemd-run \
            --quiet \
            --collect \
            --unit="$unit" \
            --on-active="${delay_s}s" \
            /bin/systemctl reboot
        then
            log_ok "Reboot scheduled (${unit}) — pinch works after X11 comes up"
            return 0
        fi
        log_warn "systemd-run reboot schedule failed — falling back to background sleep"
    fi
    nohup bash -c "sleep ${delay_s}; systemctl reboot" >/dev/null 2>&1 </dev/null &
    log_ok "Reboot scheduled (sleep fallback, pid $!) — pinch works after X11 comes up"
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
        # If dump1090 was never configured in env, keep the explicit off default
        # (avoids connection-refused spam when no local receiver is installed).
        if ! grep -qE '^[[:space:]]*DUMP1090_ENABLED=' "$ENV_DEST"; then
            printf '\n# Local ADS-B receiver (off until enabled in the portal)\nDUMP1090_ENABLED=False\n' >> "$ENV_DEST"
            log_ok "Set DUMP1090_ENABLED=False (no local receiver by default)"
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
        if ! grep -qE '^[[:space:]]*DUMP1090_ENABLED=' "$ENV_DEST"; then
            printf '\nDUMP1090_ENABLED=False\n' >> "$ENV_DEST"
        fi
    fi

    setup_config_h
}

suppress_desktop_bluetooth_popups() {
    # Raspberry Pi OS panel plugins (lxplug-bluetooth / wfplug-bluetooth, and
    # volumepulse / volumealsa for BT audio) pop a "Connection successful"
    # dialog or a "<device> Connected N%" toast on every BlueZ connect.
    # Those steal focus from fullscreen FlightScnr. Pairing is done via the
    # web portal, so drop the panel widgets and mute panel notifications —
    # BlueZ/PipeWire still work for ATC audio.
    #
    # After prefer_x11_session, the live stack is Openbox + lxpanel (not
    # labwc/wf-panel-pi). The earlier Wayland-only widgets_right fix is not
    # enough; lxpanel must lose its bluetooth plugin (and we still harden
    # wf-panel-pi for devices that remain on labwc).
    log_step "Suppressing desktop Bluetooth pair/connect popups"

    local changed=0
    local panel
    local panels=()
    local p
    local autostart

    # Collect every lxpanel panel config we can find (system + user profiles).
    while IFS= read -r p; do
        [ -n "$p" ] && panels+=("$p")
    done < <(
        find \
            "${REPO_OWNER_HOME}/.config/lxpanel" \
            /etc/xdg/lxpanel \
            /root/.config/lxpanel \
            -type f -path '*/panels/*' 2>/dev/null | sort -u
    )

    # Fresh X11 logins may have no user panel yet and still load bluetooth from
    # the packaged default — seed a user copy so our strip sticks.
    if [ -n "${REPO_OWNER_HOME:-}" ] \
        && [ ! -f "${REPO_OWNER_HOME}/.config/lxpanel/LXDE-pi/panels/panel" ] \
        && [ -f /etc/xdg/lxpanel/LXDE-pi/panels/panel ]
    then
        mkdir -p "${REPO_OWNER_HOME}/.config/lxpanel/LXDE-pi/panels"
        if cp -a /etc/xdg/lxpanel/LXDE-pi/panels/panel \
            "${REPO_OWNER_HOME}/.config/lxpanel/LXDE-pi/panels/panel"
        then
            chown -R "$REPO_OWNER:" "${REPO_OWNER_HOME}/.config/lxpanel" 2>/dev/null || true
            panels+=("${REPO_OWNER_HOME}/.config/lxpanel/LXDE-pi/panels/panel")
            log_ok "Seeded user lxpanel config from system default"
        fi
    fi

    for panel in "${panels[@]}"; do
        [ -f "$panel" ] || continue
        if ! grep -qiE '^[[:space:]]*type[[:space:]]*=[[:space:]]*bluetooth[[:space:]]*$' "$panel"; then
            continue
        fi
        # Drop Plugin { … type=bluetooth … } blocks. Allow "type = bluetooth".
        if python3 - "$panel" <<'PY'
import pathlib, re, sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
new, n = re.subn(
    r"(?ims)^Plugin\s*\{(?:(?!^Plugin\s*\{).)*?"
    r"^[ \t]*type[ \t]*=[ \t]*bluetooth[ \t]*\n"
    r"(?:(?!^Plugin\s*\{).)*?^\}\s*\n?",
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
    done

    # Disable desktop autostart helpers that show their own BT dialogs/toasts.
    for autostart in \
        /etc/xdg/autostart/blueman.desktop \
        /etc/xdg/autostart/blueman-applet.desktop \
        /etc/xdg/autostart/blueberry-tray.desktop \
        "${REPO_OWNER_HOME}/.config/autostart/blueman.desktop" \
        "${REPO_OWNER_HOME}/.config/autostart/blueman-applet.desktop"
    do
        if [ -f "$autostart" ] && ! grep -qE '^[[:space:]]*Hidden[[:space:]]*=[[:space:]]*true' "$autostart"; then
            if grep -qE '^[[:space:]]*Hidden[[:space:]]*=' "$autostart"; then
                sed -i -E 's/^[[:space:]]*Hidden[[:space:]]*=.*/Hidden=true/' "$autostart" || true
            else
                printf '\nHidden=true\n' >> "$autostart" || true
            fi
            changed=1
            log_ok "Hidden autostart $(basename "$autostart")"
        fi
    done

    # wf-panel-pi (Wayland / labwc). An absent widgets_right falls back to the
    # packaged default (includes bluetooth) — write the key explicitly.
    # notify_enable=false also stops volumepulse "Connected"/battery toasts.
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
            chown "$REPO_OWNER:" "$wf_ini" 2>/dev/null || true
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
            log_ok "Stopped lxpanel so bluetooth plugin reload cannot show dialogs"
        fi
        if pgrep -x wf-panel-pi >/dev/null 2>&1; then
            # Nothing respawns it inside a running Wayland session; it comes back
            # at next login, reading the config written above.
            killall -q wf-panel-pi 2>/dev/null || true
            log_ok "Stopped wf-panel-pi (returns on next boot without the popups)"
        fi
        # Blueman / blueberry trays if somehow still running.
        killall -q blueman-applet blueman-tray blueberry-tray 2>/dev/null || true
    else
        log_ok "Desktop Bluetooth panel plugin already disabled (or not present)"
    fi
}

suppress_openbox_decorations_for_kiosk() {
    # Fresh rpd-x (Openbox + PiXtrix) still draws a title bar on the SDL window.
    # On a 90°-rotated round panel that bar appears on the left with vertical
    # "FlightScnr Pi" text. Copy the system rc into the user config (Openbox
    # documents that path) and add undecorate/fullscreen rules.
    local dest_dir="${REPO_OWNER_HOME}/.config/openbox"
    local dest="${dest_dir}/rpd-rc.xml"
    local src=""
    local f
    local marker="flightscnr-kiosk-v2"

    log_step "Openbox decorations (hide title bar on round panel)"

    for f in /etc/xdg/openbox/rpd-rc.xml /etc/X11/openbox/rpd-rc.xml \
             /etc/xdg/openbox/lxde-pi-rc.xml /etc/xdg/openbox/rc.xml; do
        if [ -f "$f" ]; then
            src="$f"
            break
        fi
    done
    if [ -z "$src" ] || [ -z "${REPO_OWNER_HOME:-}" ]; then
        log_ok "No Openbox rc to patch (or no desktop user home)"
        return 0
    fi

    mkdir -p "$dest_dir"
    if [ ! -f "$dest" ]; then
        cp -a "$src" "$dest" || {
            log_warn "Could not copy $src → $dest"
            return 0
        }
        log_ok "Copied $src → $dest"
    fi

    if grep -q "$marker" "$dest" 2>/dev/null; then
        log_ok "Openbox kiosk rules already present ($dest)"
    elif grep -q '</applications>' "$dest"; then
        local snippet
        snippet="$(cat <<'XML'
    <!-- flightscnr-kiosk-v2: wildcard match — no hostname/user/path hardcoding -->
    <application name="*flightscnr*">
      <decor>no</decor>
      <fullscreen>yes</fullscreen>
      <maximized>yes</maximized>
    </application>
    <application class="*flightscnr*">
      <decor>no</decor>
      <fullscreen>yes</fullscreen>
      <maximized>yes</maximized>
    </application>
    <application class="SDL_App">
      <decor>no</decor>
      <fullscreen>yes</fullscreen>
      <maximized>yes</maximized>
    </application>
    <application class="pygame">
      <decor>no</decor>
      <fullscreen>yes</fullscreen>
      <maximized>yes</maximized>
    </application>
    <!-- /flightscnr-kiosk-v2 -->
XML
)"
        python3 - "$dest" "$snippet" <<'PY' || true
import sys
path, snippet = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
needle = "</applications>"
if needle not in text:
    raise SystemExit(1)
text = text.replace(needle, snippet.rstrip() + "\n  " + needle, 1)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
        if grep -q "$marker" "$dest"; then
            log_ok "Added Openbox undecorate rules ($dest)"
        else
            log_warn "Could not insert Openbox kiosk rules into $dest"
        fi
    else
        log_warn "Openbox rc has no </applications> section — skip ($dest)"
    fi

    chown -R "$REPO_OWNER:" "$dest_dir" 2>/dev/null || true

    if command -v openbox >/dev/null 2>&1 && id -u "$REPO_OWNER" >/dev/null 2>&1; then
        run_with_timeout 5 10 sudo -u "$REPO_OWNER" env \
            DISPLAY="${DISPLAY:-:0}" \
            XAUTHORITY="${REPO_OWNER_HOME}/.Xauthority" \
            openbox --reconfigure >/dev/null 2>&1 || true
    fi
}

suppress_desktop_panel_for_kiosk() {
    # Under labwc, the panel often yields to fullscreen SDL. On X11 (rpd-x /
    # Openbox) lxpanel stays on the "above" layer, so the menu bar remains
    # visible over FlightScnr. Disable lxpanel autostart for the kiosk and
    # stop any running panel now — flightscnr.service also kills it on start.
    local changed=0
    local f
    local sys_files=(
        /etc/xdg/lxsession/LXDE-pi/autostart
        /etc/xdg/lxsession/LXDE/autostart
        /etc/xdg/lxsession/rpd-x/autostart
    )
    local user_dir="${REPO_OWNER_HOME}/.config/lxsession/LXDE-pi"
    local user_file="${user_dir}/autostart"

    log_step "Desktop panel (hide menu bar for fullscreen kiosk)"

    _comment_lxpanel_autostart() {
        local path="$1"
        [ -f "$path" ] || return 1
        if grep -qE '^[[:space:]]*@lxpanel\b' "$path"; then
            sed -i -E 's/^[[:space:]]*@lxpanel\b/#@lxpanel/' "$path" || return 1
            return 0
        fi
        return 1
    }

    for f in "${sys_files[@]}"; do
        if _comment_lxpanel_autostart "$f"; then
            changed=1
            log_ok "Disabled lxpanel in $f"
        fi
    done

    # Per-user autostart overrides the system file entirely when present.
    if [ -f "$user_file" ]; then
        if _comment_lxpanel_autostart "$user_file"; then
            changed=1
            log_ok "Disabled lxpanel in $user_file"
        fi
        chown "$REPO_OWNER:" "$user_file" 2>/dev/null || true
    elif [ -f /etc/xdg/lxsession/LXDE-pi/autostart ] && [ -n "${REPO_OWNER_HOME:-}" ]; then
        mkdir -p "$user_dir"
        if cp -a /etc/xdg/lxsession/LXDE-pi/autostart "$user_file"; then
            _comment_lxpanel_autostart "$user_file" || true
            chown -R "$REPO_OWNER:" "${REPO_OWNER_HOME}/.config/lxsession" 2>/dev/null || true
            changed=1
            log_ok "Installed user autostart without lxpanel ($user_file)"
        fi
    fi

    # Stop panels immediately so this install does not need another reboot.
    if pgrep -x lxpanel >/dev/null 2>&1; then
        killall -q lxpanel 2>/dev/null || true
        changed=1
        log_ok "Stopped lxpanel"
    fi
    if pgrep -x wf-panel-pi >/dev/null 2>&1; then
        killall -q wf-panel-pi 2>/dev/null || true
        changed=1
        log_ok "Stopped wf-panel-pi"
    fi

    if [ "$changed" -eq 0 ]; then
        log_ok "Desktop panel already hidden (or not present)"
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
    chown -R "$REPO_OWNER:" "$REPO_ROOT"
    find "$REPO_ROOT" -type d -exec chmod 755 {} +
    find "$REPO_ROOT" -type f -exec chmod 644 {} +
    chmod 755 "$REPO_ROOT/install-pi.sh"
    chmod 755 "$SETUP_DIR/portal-update.sh" 2>/dev/null || true
    chmod 755 "$SETUP_DIR/portal-factory-reset.sh" 2>/dev/null || true
    # Preserve +x on release helpers — a blanket chmod 644 leaves git "dirty"
    # (mode 100755→100644) and blocks the next `git pull --ff-only` OTA.
    if [ -d "$REPO_ROOT/scripts" ]; then
        find "$REPO_ROOT/scripts" -type f \( -name '*.sh' -o -name '*.cmd' \) \
            -exec chmod 755 {} + 2>/dev/null || true
    fi
    chmod 755 "$VENV_DIR/bin/"* 2>/dev/null || true
    log_ok "Repo owned by $REPO_OWNER"
}

# Run git as the checkout owner so root-run portal updates do not leave
# root-owned index/refs. setup_paths must have set REPO_ROOT / REPO_OWNER.
# Optional: run_repo_git --timeout KILL WAIT git-args...
# Timeout wraps the git binary inside sudo -u so SIGKILL reaches fetch.
run_repo_git() {
    local timed=()
    if [ "${1:-}" = "--timeout" ]; then
        shift
        local kill_after="$1"
        local wait_s="$2"
        shift 2
        if command -v timeout >/dev/null 2>&1; then
            timed=(timeout -k "$kill_after" "$wait_s")
        fi
    fi
    local git_safe=(git -c "safe.directory=${REPO_ROOT}" -C "$REPO_ROOT")
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        sudo -u "$SUDO_USER" "${timed[@]}" "${git_safe[@]}" "$@"
    elif [ "$(id -u)" -eq 0 ] && [ -n "${REPO_OWNER:-}" ] && [ "$REPO_OWNER" != "root" ]; then
        sudo -u "$REPO_OWNER" "${timed[@]}" "${git_safe[@]}" "$@"
    else
        "${timed[@]}" "${git_safe[@]}" "$@"
    fi
}

# Drop install-induced dirt that would abort a checkout (notably
# scripts/release.sh executable-bit flips from older fix_repo_permissions).
# Always git as REPO_OWNER (not root) so restore cannot leave root-owned
# files that checkout -f as the owner then cannot overwrite.
prepare_repo_for_pull() {
    local rel
    for rel in scripts/release.sh scripts/release.cmd scripts/dev-release.sh scripts/repair-ota.sh; do
        if [ "$(id -u)" -eq 0 ] && [ -n "${REPO_OWNER:-}" ] && [ "$REPO_OWNER" != "root" ] \
            && [ -e "$REPO_ROOT/$rel" ]
        then
            chown "$REPO_OWNER:" "$REPO_ROOT/$rel" 2>/dev/null || true
        fi
        if run_repo_git status --porcelain -- "$rel" 2>/dev/null | grep -q .; then
            log_step "Clearing local changes that block pull ($rel)"
            run_repo_git restore --source=HEAD --staged --worktree -- "$rel" 2>/dev/null \
                || run_repo_git checkout HEAD -- "$rel" 2>/dev/null \
                || true
        fi
    done
}

# Fleet OTA: fetch GitHub main and check it out. Works from a branch, a missing
# upstream, or detached HEAD (tag/commit checkout, e.g. 2026.8.10.2 / 7381c3f).
# `git pull --ff-only` cannot do this — with no branch it errors, and
# `reset --hard origin/main` while detached stays detached.
# -B recreates local main at origin/main and leaves HEAD on the branch.
# -f overwrites the worktree so leftover mode dirt cannot block the switch.
sync_to_origin_main() {
    log_step "Syncing to origin/main"
    # 10 minutes is enough for --tags on slow Wi-Fi; a hang is worse than retry.
    run_repo_git --timeout 30 600 fetch --tags origin
    if ! run_repo_git show-ref --verify --quiet refs/remotes/origin/main; then
        echo "origin/main not found after fetch" >&2
        return 1
    fi
    run_repo_git checkout -f -B main origin/main
    log_ok "Synced to origin/main ($(run_repo_git log --oneline -1 2>/dev/null || true))"
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
    local factory_reset_script="$SETUP_DIR/portal-factory-reset.sh"

    if [ ! -f "$src" ]; then
        log_warn "sudoers template missing — portal updates may require manual sudo"
        return 0
    fi

    log_step "Portal update permissions"
    chmod 0755 "$update_script"
    chmod 0755 "$factory_reset_script" 2>/dev/null || true
    # Owner is quoted in the template so hyphenated Imager usernames parse.
    sed \
        -e "s|__REPO_OWNER__|$REPO_OWNER|g" \
        -e "s|__UPDATE_SCRIPT__|$update_script|g" \
        -e "s|__FACTORY_RESET_SCRIPT__|$factory_reset_script|g" \
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
    ensure_bluetooth_ready
    install_wifi_powersave_off
    suppress_desktop_bluetooth_popups
    suppress_desktop_panel_for_kiosk
    suppress_openbox_decorations_for_kiosk
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
    _portal_host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
    _portal_host="${_portal_host%%.*}"
    if [ -n "$_portal_host" ]; then
        echo "             OR web portal → API Keys (http://${_portal_host}.local)"
    else
        echo "             OR web portal → API Keys (http://<hostname>.local)"
    fi
    echo "             (advanced: sudo nano $ENV_DEST)"
    if [ "${NEED_REBOOT_FOR_X11:-0}" -eq 1 ] || [ -f "$REBOOT_X11_FLAG" ]; then
        echo "  Reboot:    AUTO — desktop switched to X11 for pinch-to-zoom"
        echo "             (pinch on radar works after reboot completes)"
        # Portal (--no-start) schedules reboot after status/lock are cleared.
        if [ "$no_start" -eq 0 ]; then
            schedule_reboot_for_x11
        else
            log_ok "X11 reboot flagged for portal/updater to schedule after status write"
        fi
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

    prepare_repo_for_pull
    sync_to_origin_main

    local install_args=(--skip-apt)
    if [ "$no_start" -eq 1 ]; then
        install_args+=(--no-start)
    fi

    # Always re-exec the post-pull install-pi.sh. Calling cmd_install in-process
    # would keep running the *pre-pull* script still loaded in memory — so OTA
    # from an old build would skip new steps (X11 session, 720x720, fan guards).
    echo ""
    echo "Re-syncing with updated installer..."
    # Drop snapshot env so the post-pull file copies itself to /tmp again.
    unset FLIGHTSCNR_INSTALL_SNAPSHOT FLIGHTSCNR_INSTALL_SNAPSHOT_PATH
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
      fetch origin/main, check it out (reattaches detached HEAD), re-sync, restart
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
