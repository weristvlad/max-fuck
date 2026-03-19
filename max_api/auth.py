"""Token persistence and auto-login for MAX messenger."""

import getpass
import json
import os
import time
from pathlib import Path

DEFAULT_TOKEN_FILE = Path.home() / ".max_token.json"


def save_token(token: str, path: Path = DEFAULT_TOKEN_FILE):
    """Save auth token to disk."""
    data = {
        "token": token,
        "saved_at": int(time.time()),
    }
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)  # only owner can read


def load_token(path: Path = DEFAULT_TOKEN_FILE) -> str | None:
    """Load saved token, or None if not found."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get("token")
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
