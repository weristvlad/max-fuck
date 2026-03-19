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
):
    """Save auth token to disk.

    Args:
        token: The current session token (from refresh or login).
        path: File path.
        login_token: The original long-lived LOGIN token (survives months).
        lifetime_ts: Session token expiry timestamp in ms.
        refresh_ts: Recommended refresh timestamp in ms.
    """
    # Preserve existing login_token if not provided
    existing_login = None
    if path.exists() and login_token is None:
        try:
            existing = json.loads(path.read_text())
            existing_login = existing.get("login_token")
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
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


def load_token(path: Path = DEFAULT_TOKEN_FILE) -> str | None:
    """Load the best available token.

    Tries session token first, falls back to long-lived login token.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())

        # Try session token if not expired
        token = data.get("token")
        lifetime_ts = data.get("lifetime_ts")
        if token and lifetime_ts:
            now_ms = int(time.time() * 1000)
            if now_ms < lifetime_ts:
                return token  # session token still valid

        # Fall back to long-lived login token (survives months)
        login_token = data.get("login_token")
        if login_token:
            return login_token

        # Last resort: try session token even if expired
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
