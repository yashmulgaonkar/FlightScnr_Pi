"""GitHub release/commit check and portal-triggered updates."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("flightscnr.updater")

GITHUB_REPO = os.environ.get("FLIGHTSCNR_GITHUB_REPO", "yashmulgaonkar/FlightScnr_Pi")
GITHUB_BRANCH = os.environ.get("FLIGHTSCNR_GITHUB_BRANCH", "main")
GITHUB_API = "https://api.github.com"
DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
STATUS_PATH = os.path.join(DATA_DIR, "update-status.json")
LOCK_PATH = os.path.join(DATA_DIR, "update.lock")
UPDATE_LOG_PATH = os.path.join(DATA_DIR, "update.log")
GITHUB_TIMEOUT_S = 12


def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def update_script_path() -> str:
    return os.path.join(repo_root(), "flightscnr", "setup", "portal-update.sh")


def _run_git(args: list[str]) -> str | None:
    root = repo_root()
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git %s failed: %s", " ".join(args), exc)
        return None


def local_version_info() -> dict:
    from version import APP_VERSION, read_version

    root = repo_root()
    commit = _run_git(["rev-parse", "HEAD"]) or ""
    short = _run_git(["rev-parse", "--short", "HEAD"]) or (commit[:7] if commit else "")
    describe = _run_git(["describe", "--tags", "--always", "--dirty"]) or short
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    release = read_version() or APP_VERSION
    return {
        "release": release,
        "commit": commit,
        "commit_short": short,
        "describe": describe,
        "branch": branch,
        "repo_root": root,
        "is_git_repo": bool(commit),
    }


def _github_get(path: str) -> dict | None:
    url = f"{GITHUB_API}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FlightScnr-Pi-Updater",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(url, headers=headers, timeout=GITHUB_TIMEOUT_S)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("GitHub API request failed (%s): %s", path, exc)
        return None


def _remote_commit_via_git() -> dict:
    ref = f"refs/heads/{GITHUB_BRANCH}"
    output = _run_git(["ls-remote", "origin", ref])
    if not output:
        output = _run_git(["ls-remote", "origin", "HEAD"])
    if not output:
        return {}
    commit = output.split()[0].strip()
    if not commit:
        return {}
    return {
        "commit": commit,
        "commit_short": commit[:7],
        "branch": GITHUB_BRANCH,
        "source": "git",
    }


def remote_version_info() -> dict:
    owner, _, name = GITHUB_REPO.partition("/")
    release = _github_get(f"/repos/{owner}/{name}/releases/latest")
    branch_commit = _github_get(f"/repos/{owner}/{name}/commits/{GITHUB_BRANCH}")

    remote_commit = ""
    remote_short = ""
    published_at = ""
    source = ""
    if branch_commit:
        remote_commit = str(branch_commit.get("sha") or "")
        remote_short = remote_commit[:7]
        commit_meta = branch_commit.get("commit") or {}
        published_at = str(commit_meta.get("committer", {}).get("date") or "")
        source = "github_api"

    if not remote_commit:
        git_remote = _remote_commit_via_git()
        if git_remote:
            remote_commit = git_remote.get("commit", "")
            remote_short = git_remote.get("commit_short", "")
            source = git_remote.get("source", "git")

    release_tag = ""
    release_name = ""
    release_published = ""
    if release:
        release_tag = str(release.get("tag_name") or "")
        release_name = str(release.get("name") or release_tag)
        release_published = str(release.get("published_at") or "")

    return {
        "commit": remote_commit,
        "commit_short": remote_short,
        "branch": GITHUB_BRANCH,
        "release_tag": release_tag,
        "release_name": release_name,
        "release_published": release_published,
        "commit_date": published_at,
        "repo": GITHUB_REPO,
        "source": source,
    }


def _read_status() -> dict:
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_status(state: str, message: str = "", **extra) -> dict:
    payload = {
        "state": state,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, STATUS_PATH)
    except OSError as exc:
        logger.warning("Could not write update status: %s", exc)
    return payload


def update_running() -> bool:
    if os.path.isfile(LOCK_PATH):
        try:
            with open(LOCK_PATH, encoding="utf-8") as fh:
                pid = int((fh.read() or "").strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid > 0:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                pass
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
    status = _read_status()
    return status.get("state") == "running"


def check_for_update() -> dict:
    from version import compare_versions, normalize_version

    local = local_version_info()
    remote = remote_version_info()
    status = _read_status()

    local_release = normalize_version(local.get("release") or "")
    remote_release = normalize_version(remote.get("release_tag") or "")

    update_available = False
    if local_release and remote_release:
        # Release tags are authoritative — matching versions are up to date.
        update_available = compare_versions(local_release, remote_release) < 0
    elif local.get("commit") and remote.get("commit"):
        update_available = local["commit"] != remote["commit"]

    message = "Up to date."
    if not local.get("is_git_repo"):
        message = "This install is not a git checkout — use install-pi.sh manually."
    elif not remote.get("commit") and not remote_release:
        message = "Could not reach GitHub to check for updates."
    elif update_available:
        if remote_release and local_release:
            message = f"Update available: {local_release} → {remote_release}"
        else:
            message = "A newer version is available."

    running = update_running()
    if running:
        message = "Update in progress…"

    return {
        "ok": True,
        "update_available": update_available and not running,
        "update_running": running,
        "message": message,
        "local": local,
        "remote": remote,
        "status": status,
    }


def mark_update_running() -> None:
    _write_status("running", "Update started.")


def mark_update_finished(success: bool, message: str) -> None:
    _write_status("success" if success else "failed", message)


def start_update() -> dict:
    if update_running():
        return {"ok": False, "message": "An update is already running."}

    local = local_version_info()
    if not local.get("is_git_repo"):
        return {"ok": False, "message": "This install is not a git repository."}

    script = update_script_path()
    if not os.path.isfile(script):
        return {"ok": False, "message": f"Update script not found: {script}"}

    os.makedirs(DATA_DIR, exist_ok=True)
    mark_update_running()

    log_fh = open(UPDATE_LOG_PATH, "a", encoding="utf-8")
    log_fh.write(f"\n--- update started {datetime.now(timezone.utc).isoformat()} ---\n")
    log_fh.flush()

    if os.geteuid() == 0:
        cmd = ["/bin/bash", script]
    else:
        cmd = ["sudo", "-n", "/bin/bash", script]

    try:
        subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log_fh.close()
        mark_update_finished(False, f"Could not start update: {exc}")
        return {"ok": False, "message": f"Could not start update: {exc}"}

    return {
        "ok": True,
        "message": "Update started. The display will restart shortly.",
    }


def update_status() -> dict:
    status = _read_status()
    running = update_running()
    tail = ""
    try:
        if os.path.isfile(UPDATE_LOG_PATH):
            with open(UPDATE_LOG_PATH, encoding="utf-8", errors="replace") as fh:
                tail = "".join(fh.readlines()[-40:])
    except OSError:
        pass
    return {
        "ok": True,
        "update_running": running,
        "status": status,
        "log_tail": tail,
    }
