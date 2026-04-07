"""OUI MAC address vendor lookup against a local JSON database."""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

OUI_PATH = Path("/opt/gwless/oui.json")
OUI_DOWNLOAD_URL = "https://maclookup.app/downloads/json-database/mac-oui"

_oui_db: Optional[dict] = None
_oui_loaded_at: float = 0.0


def _load_db() -> dict:
    global _oui_db, _oui_loaded_at
    if _oui_db is not None:
        return _oui_db

    # Try local path first, then fallback to cwd
    candidates = [OUI_PATH, Path("oui.json")]
    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    raw = json.load(f)
                # Support both list-of-dicts and dict formats
                if isinstance(raw, list):
                    _oui_db = {
                        entry.get("macPrefix", "").upper().replace(":", "").replace("-", "").replace(".", ""): entry.get("vendorName", "Unknown")
                        for entry in raw
                        if entry.get("macPrefix")
                    }
                elif isinstance(raw, dict):
                    _oui_db = {k.upper().replace(":", "").replace("-", "").replace(".", ""): v for k, v in raw.items()}
                else:
                    _oui_db = {}
                _oui_loaded_at = time.time()
                logger.info("OUI database loaded: %d entries from %s", len(_oui_db), path)
                return _oui_db
            except Exception as e:
                logger.warning("Failed to load OUI database from %s: %s", path, e)

    logger.warning("OUI database not found — vendor lookups will return 'Unknown'")
    _oui_db = {}
    return _oui_db


def download_oui_db() -> bool:
    """Download OUI database from maclookup.app. Returns True on success."""
    try:
        logger.info("Downloading OUI database from %s", OUI_DOWNLOAD_URL)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; gwless/1.0)",
            "Accept": "application/json, */*",
        }
        resp = requests.get(OUI_DOWNLOAD_URL, timeout=60, verify=False, headers=headers)
        resp.raise_for_status()
        # Validate JSON before writing to disk
        try:
            data = resp.json()
        except Exception:
            raise ValueError(
                f"Response is not valid JSON (content-type: {resp.headers.get('content-type', '?')}, "
                f"first 200 chars: {resp.text[:200]!r})"
            )
        if not data:
            raise ValueError("Downloaded OUI database is empty")
        os.makedirs(OUI_PATH.parent, exist_ok=True)
        with open(OUI_PATH, "wb") as f:
            f.write(resp.content)
        logger.info("OUI database saved to %s (%d bytes)", OUI_PATH, len(resp.content))
        # Reload
        global _oui_db
        _oui_db = None
        _load_db()
        return True
    except Exception as e:
        logger.error("Failed to download OUI database: %s", e)
        return False


def ensure_oui_db() -> None:
    """Download OUI database if it doesn't exist yet."""
    if not OUI_PATH.exists() and not Path("oui.json").exists():
        download_oui_db()
    else:
        _load_db()


def lookup(mac: str) -> str:
    """Return vendor name for a MAC address, or 'Unknown'."""
    db = _load_db()
    if not db:
        return "Unknown"
    # Normalize: remove separators, uppercase, take first 6 chars (OUI prefix)
    normalized = mac.upper().replace(":", "").replace("-", "").replace(".", "")
    if len(normalized) < 6:
        return "Unknown"
    prefix = normalized[:6]
    return db.get(prefix, "Unknown")
