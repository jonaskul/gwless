"""OUI MAC address vendor lookup against a local JSON database."""
import csv
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

OUI_PATH = Path("/opt/gwless/oui.json")

# Primary: IEEE standards body — always available, no auth required
_IEEE_CSV_URL = "https://standards-oui.ieee.org/oui/oui.csv"
# Fallback: maclookup.app JSON
_MACLOOKUP_URL = "https://maclookup.app/downloads/json-database/mac-oui"

_oui_db: Optional[dict] = None
_oui_loaded_at: float = 0.0


def _load_db() -> dict:
    global _oui_db, _oui_loaded_at
    if _oui_db is not None:
        return _oui_db

    candidates = [OUI_PATH, Path("oui.json")]
    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    _oui_db = {
                        entry.get("macPrefix", "").upper()
                        .replace(":", "").replace("-", "").replace(".", ""): entry.get("vendorName", "Unknown")
                        for entry in raw
                        if entry.get("macPrefix")
                    }
                elif isinstance(raw, dict):
                    _oui_db = {
                        k.upper().replace(":", "").replace("-", "").replace(".", ""): v
                        for k, v in raw.items()
                    }
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


def _download_ieee_csv() -> dict:
    """Download OUI data from IEEE standards-oui.ieee.org (CSV format)."""
    logger.info("Downloading OUI database from IEEE: %s", _IEEE_CSV_URL)
    resp = requests.get(_IEEE_CSV_URL, timeout=60, verify=True,
                        headers={"User-Agent": "gwless/1.0"})
    resp.raise_for_status()
    # CSV columns: Registry,Assignment,Organization Name,Organization Address
    reader = csv.DictReader(io.StringIO(resp.text))
    db = {}
    for row in reader:
        prefix = row.get("Assignment", "").strip().upper()
        name = row.get("Organization Name", "").strip()
        if prefix and name:
            db[prefix] = name
    if not db:
        raise ValueError("IEEE CSV parsed to empty database")
    return db


def _download_maclookup_json() -> dict:
    """Download OUI data from maclookup.app (JSON format, fallback)."""
    logger.info("Downloading OUI database from maclookup.app: %s", _MACLOOKUP_URL)
    resp = requests.get(_MACLOOKUP_URL, timeout=60, verify=False,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; gwless/1.0)",
                                 "Accept": "application/json, */*"})
    resp.raise_for_status()
    data = resp.json()  # raises ValueError if not JSON
    if not data:
        raise ValueError("maclookup.app returned empty database")
    # Normalise list-of-dicts or plain dict
    if isinstance(data, list):
        db = {
            entry.get("macPrefix", "").upper()
            .replace(":", "").replace("-", "").replace(".", ""): entry.get("vendorName", "Unknown")
            for entry in data if entry.get("macPrefix")
        }
    else:
        db = {k.upper().replace(":", "").replace("-", "").replace(".", ""): v for k, v in data.items()}
    if not db:
        raise ValueError("maclookup.app JSON produced empty lookup table")
    return db


def download_oui_db() -> Tuple[bool, str]:
    """Download OUI database, trying IEEE CSV first then maclookup.app.

    Returns (success, message).
    """
    last_err = ""
    for name, fn in [("IEEE", _download_ieee_csv), ("maclookup.app", _download_maclookup_json)]:
        try:
            db = fn()
            os.makedirs(OUI_PATH.parent, exist_ok=True)
            with open(OUI_PATH, "w") as f:
                json.dump(db, f)
            logger.info("OUI database saved to %s (%d entries, source: %s)", OUI_PATH, len(db), name)
            global _oui_db
            _oui_db = None
            _load_db()
            return True, f"{len(db):,} entries (source: {name})"
        except Exception as e:
            last_err = f"{name}: {e}"
            logger.warning("OUI download failed from %s: %s", name, e)

    logger.error("All OUI sources failed. Last error: %s", last_err)
    return False, last_err


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
    normalized = mac.upper().replace(":", "").replace("-", "").replace(".", "")
    if len(normalized) < 6:
        return "Unknown"
    return db.get(normalized[:6], "Unknown")
