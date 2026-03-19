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
    lifetime_ts: int | None = None,
    refresh_ts: int | None = None,
):
    """Save auth token to disk.

    Args:
        token: The auth token string.
        path: File path.
        lifetime_ts: Token expiry timestamp in ms (from server).
        refresh_ts: Recommended refresh timestamp in ms (from server).
    """
    data = {
        "token": token,
        "saved_at": int(time.time()),
    }
    if lifetime_ts:
        data["lifetime_ts"] = lifetime_ts
    if refresh_ts:
        data["refresh_ts"] = refresh_ts
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)  # only owner can read


def load_token(path: Path = DEFAULT_TOKEN_FILE) -> str | None:
    """Load saved token if it hasn't expired, or None."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        token = data.get("token")
        if not token:
            return None
        # Check if token has expired
        lifetime_ts = data.get("lifetime_ts")
        if lifetime_ts:
            now_ms = int(time.time() * 1000)
            if now_ms >= lifetime_ts:
                return None  # expired
        return token
    except (json.JSONDecodeError, KeyError):
        return None


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
