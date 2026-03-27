"""Token persistence and auto-login for MAX messenger."""

import getpass
import json
import os
import time
from pathlib import Path

DEFAULT_TOKEN_FILE = Path.home() / ".max_token.json"


def save_token(
    token: str,
    path: Path = DEFAULT_TOKEN_FILE,
    login_token: str | None = None,
    lifetime_ts: int | None = None,
    refresh_ts: int | None = None,
    device_id: str | None = None,
):
    """Save auth token to disk.

    Args:
        token: The current session token (from refresh or login).
        path: File path.
        login_token: The original long-lived LOGIN token (survives months).
        lifetime_ts: Session token expiry timestamp in ms.
        refresh_ts: Recommended refresh timestamp in ms.
        device_id: Device ID tied to this session.
    """
    # Preserve existing fields if not provided
    existing_login = None
    existing_device_id = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if login_token is None:
                existing_login = existing.get("login_token")
            if device_id is None:
                existing_device_id = existing.get("device_id")
        except (json.JSONDecodeError, KeyError):
            pass

    data = {
        "token": token,
        "login_token": login_token or existing_login or token,
        "saved_at": int(time.time()),
    }
    if lifetime_ts:
        data["lifetime_ts"] = lifetime_ts
    if refresh_ts:
        data["refresh_ts"] = refresh_ts
    did = device_id or existing_device_id
    if did:
        data["device_id"] = did
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


def load_token(path: Path = DEFAULT_TOKEN_FILE) -> tuple[str | None, str | None]:
    """Load the LOGIN token and saved device_id.

    Always returns the long-lived LOGIN token (An_...) for opcode 19.
    Session tokens ($...) from refresh are only valid within a single
    WS connection and cannot be used for re-login.

    Returns:
        (login_token, device_id) tuple. Either may be None.
    """
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
        device_id = data.get("device_id")

        # Always prefer the LOGIN token — it's the only one valid for opcode 19
        login_token = data.get("login_token")
        if login_token:
            return login_token, device_id

        # Fallback to token field (old format where both were the same)
        return data.get("token"), device_id
    except (json.JSONDecodeError, KeyError):
        return None, None


def clear_token(path: Path = DEFAULT_TOKEN_FILE):
    """Delete saved token."""
    if path.exists():
        path.unlink()


def print_qr_terminal(url: str):
    """Print QR code directly in the terminal."""
    try:
        import io
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        f = io.StringIO()
        qr.print_ascii(out=f, invert=True)
        print(f.getvalue())
    except ImportError:
        print(f"[QR] Install 'qrcode' for terminal QR: pip install qrcode")
        print(f"[QR] Open this link on your phone: {url}")
