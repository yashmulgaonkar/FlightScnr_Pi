import os

import requests

SERVER_URL = os.environ.get("MAP_UPLOAD_URL", "").rstrip("/")


def get_upload_token() -> str:
    """Request a new upload token from the map upload server."""
    if not SERVER_URL:
        return ""
    try:
        resp = requests.get(f"{SERVER_URL}/get-token", timeout=5)
        resp.raise_for_status()
        token_line = resp.text.strip()
        return token_line.split(":")[-1].strip()
    except Exception as e:
        print(f"⚠️ Failed to get upload token: {e}")
        return ""


def upload_map_to_server(local_path: str) -> str:
    """
    Upload a map file to MAP_UPLOAD_URL using a dynamically obtained token.
    Returns the public URL (or empty string when upload is disabled or fails).
    """
    if not SERVER_URL:
        return ""
    if not os.path.isfile(local_path):
        print(f"⚠️ File not found: {local_path}")
        return ""

    token = get_upload_token()
    if not token:
        return ""

    upload_url = f"{SERVER_URL}/upload/{token}"
    try:
        with open(local_path, "rb") as f:
            files = {"file": f}
            resp = requests.post(upload_url, files=files, timeout=10)
            resp.raise_for_status()
            uploaded_name = resp.text.strip().split("Uploaded as")[-1].strip()
            return f"{SERVER_URL}/maps/{uploaded_name}"
    except Exception as e:
        print(f"⚠️ Failed to upload map: {e}")
        return ""
