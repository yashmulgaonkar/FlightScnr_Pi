#!/usr/bin/env bash
# repair-ota.sh — Unblock OTA when local file-mode dirt blocks git pull.
#
# Background: older install-pi.sh ran `chmod 644` on the whole tree and only
# restored +x on install-pi.sh / portal-update.sh. That left scripts/release.sh
# "modified" in git (mode only). The next `git pull --ff-only` then aborts and
# the device stays on the old VERSION (e.g. 2026.8.5.5) even though the portal
# may look like the update "finished".
#
# It also reattaches detached HEAD (checkout of a tag/commit such as
# 2026.8.10.2 / 7381c3f). `git pull` has no branch in that state; this script
# fetches origin and runs `checkout -f -B main origin/main` so HEAD is on main
# before install-pi.sh update runs — even an old on-disk installer can proceed.
#
# It also repairs a corrupted git object database — the classic Pi power-loss
# signatures: "error: object file .git/objects/... is empty" followed by
# "fatal: unpack-objects failed", or "fatal: bad object refs/tags/..." +
# "did not send all necessary objects" (a ref left pointing at a lost object).
# Zero-byte objects and broken refs are removed and history is re-fetched;
# if that is not enough, .git is re-cloned from GitHub (local repo edits are
# discarded — they are unrecoverable anyway).
#
# It also clears a leftover update.lock / update-status.json "running" flag
# (issue #100) when no update worker is alive, so the kiosk overlay and
# portal Wipe/Update buttons unstick after a killed OTA.
#
# This script does NOT need a successful pull first — fetch it from GitHub:
#
#   curl -fsSL https://raw.githubusercontent.com/yashmulgaonkar/FlightScnr_Pi/main/scripts/repair-ota.sh | bash
#
# Options:
#   --hard   git fetch + checkout -f -B main origin/main (discards ALL local
#            repo edits), then install --skip-apt. Same git sync as the
#            default path; kept so older instructions still work.
#   --repo DIR   FlightScnr_Pi checkout path (default: auto-detect)
#
set -euo pipefail

GITHUB_URL="https://github.com/yashmulgaonkar/FlightScnr_Pi.git"

HARD=0
REPO_ROOT="${FLIGHTSCNR_REPO:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --hard) HARD=1 ;;
        --repo)
            shift
            REPO_ROOT="${1:-}"
            if [ -z "$REPO_ROOT" ]; then
                echo "Missing path for --repo" >&2
                exit 1
            fi
            ;;
        -h|--help)
            sed -n '2,36p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
    shift
done

# An explicitly requested path must exist — never silently auto-detect a
# different checkout than the one the user asked for.
if [ -n "$REPO_ROOT" ] && [ ! -f "$REPO_ROOT/install-pi.sh" ]; then
    echo "No FlightScnr_Pi checkout at requested path: $REPO_ROOT" >&2
    exit 1
fi

resolve_repo() {
    local d candidate
    if [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/install-pi.sh" ]; then
        (cd "$REPO_ROOT" && pwd)
        return 0
    fi
    for d in \
        "${HOME:+$HOME/FlightScnr_Pi}" \
        "$(pwd)"
    do
        [ -n "$d" ] || continue
        if [ -f "$d/install-pi.sh" ] && [ -d "$d/.git" ]; then
            (cd "$d" && pwd)
            return 0
        fi
    done
    # Last resort: shallow search under /home (any Imager username).
    while IFS= read -r candidate; do
        d="$(dirname "$candidate")"
        if [ -d "$d/.git" ]; then
            (cd "$d" && pwd)
            return 0
        fi
    done < <(find /home -maxdepth 3 -type f -name install-pi.sh 2>/dev/null | head -n 5)
    return 1
}

REPO_ROOT="$(resolve_repo)" || {
    echo "Could not find FlightScnr_Pi (install-pi.sh + .git)." >&2
    echo "Re-run with: bash repair-ota.sh --repo /path/to/FlightScnr_Pi" >&2
    exit 1
}

echo "Repo: $REPO_ROOT"
cd "$REPO_ROOT"

OWNER="$(stat -c '%U' "$REPO_ROOT" 2>/dev/null || true)"
if [ -z "$OWNER" ] || ! id -u "$OWNER" >/dev/null 2>&1; then
    echo "Could not resolve repo owner for $REPO_ROOT." >&2
    exit 1
fi
git_safe=(git -c "safe.directory=${REPO_ROOT}" -C "$REPO_ROOT")

run_as_owner() {
    if [ "$(id -u)" -eq 0 ] && [ "$OWNER" != "root" ]; then
        sudo -u "$OWNER" "$@"
    else
        "$@"
    fi
}

run_git() {
    run_as_owner "${git_safe[@]}" "$@"
}

remote_url() {
    run_git config --get remote.origin.url 2>/dev/null || echo "$GITHUB_URL"
}

# Drop a dead update.lock and rewrite leftover state=running so the display
# overlay / portal stop claiming an update is in progress (issue #100).
# Leaves a live worker alone.
clear_stale_update_banner() {
    local data_dir="${FLIGHTSCNR_DATA_DIR:-/var/lib/flightscnr}"
    local lock="$data_dir/update.lock"
    local status="$data_dir/update-status.json"
    local pid=""

    if [ -f "$lock" ]; then
        pid="$(tr -d '[:space:]' <"$lock" 2>/dev/null || true)"
        if printf '%s' "$pid" | grep -Eq '^[1-9][0-9]*$' \
            && kill -0 "$pid" 2>/dev/null; then
            echo "Update worker still running (pid $pid) — leaving status alone"
            return 0
        fi
        rm -f "$lock"
        echo "Removed stale update.lock"
    fi

    [ -f "$status" ] || return 0
    python3 - "$status" <<'PY' || true
import json, sys
from datetime import datetime, timezone

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
if not isinstance(data, dict) or data.get("state") != "running":
    raise SystemExit(0)
payload = {
    "state": "failed",
    "message": "Update interrupted (no live worker). Safe to retry.",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
print("Cleared stale update-status.json (was running)")
PY
}

# True if the repo has the power-loss signature: zero-byte object files, or
# refs whose target object is gone (seen as "fatal: bad object refs/tags/X" +
# "did not send all necessary objects" when the empty files were already
# deleted by hand but a ref pointed at one of them).
repo_corrupt() {
    if find "$REPO_ROOT/.git/objects" -type f -empty 2>/dev/null | grep -q .; then
        return 0
    fi
    # Loose ref files corrupted at the file level (truncated/garbage) are
    # silently *skipped* by for-each-ref, so the loop below never sees them —
    # check the files directly. A valid loose ref holds a hex object id or a
    # "ref: " symref line.
    local reffile
    while IFS= read -r reffile; do
        grep -qE '^([0-9a-f]{40}|ref: )' "$reffile" 2>/dev/null || return 0
    done < <(find "$REPO_ROOT/.git/refs" -type f 2>/dev/null)
    local ref obj
    while read -r ref obj; do
        [ -n "$obj" ] || continue
        run_git cat-file -e "$obj" 2>/dev/null || return 0
    done < <(run_git for-each-ref --format='%(refname) %(objectname)' 2>/dev/null)
    return 1
}

# Last resort: replace .git with a fresh clone, keeping the worktree path.
# Staged on the same filesystem as the repo (NOT /tmp, which is tmpfs/RAM on
# Bookworm and too small for the ~100MB history on low-memory Pis), so the
# final swap is an atomic rename.
reclone_git_dir() {
    local url tmp bak
    url="$(remote_url)"
    echo "Re-cloning git history from $url…"
    tmp="$(run_as_owner mktemp -d "$REPO_ROOT/.repair-clone.XXXXXX")"
    if ! run_as_owner git clone --no-checkout "$url" "$tmp/clone"; then
        rm -rf "$tmp"
        echo "Re-clone failed (network down?). Cannot repair automatically." >&2
        echo "Manual fix: back up any local edits, then delete and re-clone:" >&2
        echo "  git clone $GITHUB_URL ~/FlightScnr_Pi && sudo bash ~/FlightScnr_Pi/install-pi.sh" >&2
        return 1
    fi
    bak="$REPO_ROOT/.git.corrupt.$$"
    mv "$REPO_ROOT/.git" "$bak"
    mv "$tmp/clone/.git" "$REPO_ROOT/.git"
    rm -rf "$tmp" "$bak"
}

# Remove zero-byte loose objects and broken refs, re-fetch history, and point
# main at origin/main. Discards local repo edits (they reference lost objects
# anyway).
repair_object_db() {
    echo "Git object database looks corrupted (e.g. zero-byte files in .git/objects)."
    echo "This usually follows a power loss / SD-card write interruption."
    echo "Repairing — local repo edits (if any) will be discarded…"

    find "$REPO_ROOT/.git/objects" -type f -empty -delete 2>/dev/null || true
    rm -f "$REPO_ROOT/.git/objects/pack/tmp_pack_"* 2>/dev/null || true
    # Drop .idx files whose .pack was truncated away by the sweep above.
    local idx
    for idx in "$REPO_ROOT/.git/objects/pack/"*.idx; do
        [ -e "$idx" ] || continue
        [ -f "${idx%.idx}.pack" ] || rm -f "$idx"
    done

    # Loose ref files broken at the file level cannot be deleted with
    # update-ref ("cannot lock ref …: reference broken") and are invisible to
    # for-each-ref — remove the files directly. Anything legitimate is
    # recreated by the fetch below.
    local reffile
    while IFS= read -r reffile; do
        if ! grep -qE '^([0-9a-f]{40}|ref: )' "$reffile" 2>/dev/null; then
            echo "Removing broken ref file: ${reffile#"$REPO_ROOT/"}"
            rm -f "$reffile"
        fi
    done < <(find "$REPO_ROOT/.git/refs" -type f 2>/dev/null)

    # Delete refs whose target object was lost — they make every fetch fail
    # its connectivity check ("did not send all necessary objects"). main,
    # origin/* and tags are recreated below.
    local ref obj
    while read -r ref obj; do
        [ -n "$ref" ] || continue
        if ! run_git cat-file -e "$obj" 2>/dev/null; then
            run_git update-ref -d "$ref" 2>/dev/null || true
        fi
    done < <(run_git for-each-ref --format='%(refname) %(objectname)' 2>/dev/null || true)

    # Reflogs still reference the lost objects; drop them so fsck/gc stay clean.
    run_git reflog expire --expire=now --all 2>/dev/null || true
    rm -rf "$REPO_ROOT/.git/logs" 2>/dev/null || true

    # Point main back at origin/main. -B recreates the branch with tracking
    # (status showed "[gone]"), -f overwrites the index/worktree left over
    # from the corrupt state. The full fsck catches objects that are still
    # missing but not needed by the checkout itself (e.g. a blob whose
    # worktree copy happens to be current) — without it a later gc/checkout
    # would hit the hole.
    restore_main() {
        run_git rev-parse --verify --quiet 'origin/main^{commit}' >/dev/null \
            && run_git checkout -f -B main origin/main \
            && ! repo_corrupt \
            && run_git fsck --no-progress >/dev/null 2>&1
    }

    # Cheapest first: a plain incremental fetch is enough when none of the
    # lost objects are needed by main or as delta bases (the common case).
    # --tags restores release tags whose broken refs were pruned above.
    # --refetch (git >= 2.36) re-downloads full history as a self-contained
    # pack; a fresh clone is the last resort.
    run_git fetch --tags origin || true
    if ! restore_main; then
        echo "Incremental fetch was not enough — re-fetching full history…"
        run_git fetch --refetch origin 2>/dev/null || true
        if ! restore_main; then
            reclone_git_dir || exit 1
            if ! restore_main; then
                echo "Repository still unhealthy after repair." >&2
                echo "Manual fix: back up any local edits, then delete and re-clone:" >&2
                echo "  git clone $GITHUB_URL ~/FlightScnr_Pi && sudo bash ~/FlightScnr_Pi/install-pi.sh" >&2
                exit 1
            fi
        fi
    fi
    echo "Git repository repaired."
}

clear_stale_update_banner

if repo_corrupt; then
    repair_object_db
fi

echo "Before: $(run_git status -sb | tr '\n' ' ')"
echo "VERSION=$(tr -d '[:space:]' <VERSION 2>/dev/null || echo '?')"

# Same sync as install-pi.sh update: fetch + checkout -B leaves HEAD on main.
# reset --hard while detached would move the commit and stay detached, so the
# following `git pull` still fails. -f discards local checkout edits (kiosk).
echo "Syncing to origin/main…"
run_git fetch --tags origin || repair_object_db
if ! run_git show-ref --verify --quiet refs/remotes/origin/main; then
    echo "origin/main not found after fetch" >&2
    exit 1
fi
run_git checkout -f -B main origin/main

echo "After sync: $(run_git status -sb | tr '\n' ' ')"

echo "Running install-pi.sh update…"
if [ "$(id -u)" -eq 0 ]; then
    bash "$REPO_ROOT/install-pi.sh" update
else
    sudo bash "$REPO_ROOT/install-pi.sh" update
fi

clear_stale_update_banner

echo ""
echo "Done. VERSION=$(tr -d '[:space:]' <VERSION 2>/dev/null || echo '?')"
echo "If the portal still looks old, refresh the page or: sudo systemctl restart flightscnr"
echo "If LightDM switched to X11 for pinch-zoom, the Pi reboots on its own."
