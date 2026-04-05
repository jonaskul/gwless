"""
Gwless — FastAPI application entry point.
API routes MUST be defined before StaticFiles mount.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .cache import TTLCache
from .merger import merge_clients, normalize_mac
from .oui import lookup as oui_lookup, ensure_oui_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    candidates = [
        Path("/opt/gwless/config.yaml"),
        Path("config.yaml"),
        Path("config.yaml.example"),
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                cfg = yaml.safe_load(f)
            logger.info("Loaded config from %s", path)
            return cfg
    raise RuntimeError("No config.yaml found. Copy config.yaml.example and fill in your credentials.")


CONFIG: dict = {}
try:
    CONFIG = _load_config()
except Exception as e:
    logger.warning("Config not loaded: %s", e)
    CONFIG = {
        "sophos": {"host": "192.168.1.1", "username": "admin", "password": ""},
        "unifi": {"host": "https://192.168.1.2", "username": "admin", "password": "", "site": "default"},
        "app": {"port": 8080, "log_level": "info"},
    }

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_cache_leases = TTLCache(ttl=CONFIG.get("sophos", {}).get("poll_interval_leases", 60))
_cache_sophos_cfg = TTLCache(ttl=CONFIG.get("sophos", {}).get("poll_interval_config", 300))
_cache_unifi = TTLCache(ttl=CONFIG.get("unifi", {}).get("poll_interval", 30))

_sophos_error: Optional[str] = None
_unifi_error: Optional[str] = None

# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _get_sophos_leases() -> list[dict]:
    global _sophos_error
    entry = _cache_leases.get()
    if entry and not entry.stale:
        return entry.data

    try:
        from .sophos import fetch_dhcp_leases_ssh
        leases = fetch_dhcp_leases_ssh(CONFIG["sophos"])
        _cache_leases.set(leases)
        _sophos_error = None
        return leases
    except Exception as e:
        _sophos_error = str(e)
        logger.error("Sophos SSH error: %s", e)
        if entry:
            entry.stale = True
            return entry.data
        return []


def _get_sophos_config() -> dict:
    global _sophos_error
    entry = _cache_sophos_cfg.get()
    if entry and not entry.stale:
        return entry.data

    try:
        from .sophos import fetch_dhcp_server_config
        cfg_data = fetch_dhcp_server_config(CONFIG["sophos"])
        _cache_sophos_cfg.set(cfg_data)
        _sophos_error = None
        return cfg_data
    except Exception as e:
        _sophos_error = str(e)
        logger.error("Sophos XML API error: %s", e)
        if entry:
            entry.stale = True
            return entry.data
        return {"servers": [], "static_entries": []}


def _get_unifi_data() -> dict:
    global _unifi_error
    entry = _cache_unifi.get()
    if entry and not entry.stale:
        return entry.data

    try:
        from .unifi import UniFiClient
        unifi_cfg = CONFIG.get("unifi", {})
        # Ensure host has scheme
        host = unifi_cfg.get("host", "")
        if not host.startswith("http"):
            host = "https://" + host
        unifi_cfg = {**unifi_cfg, "host": host}
        client = UniFiClient(unifi_cfg)
        clients = client.fetch_clients()
        ap_map = client.fetch_ap_map()
        data = {"clients": clients, "ap_map": ap_map}
        _cache_unifi.set(data)
        _unifi_error = None
        return data
    except Exception as e:
        _unifi_error = str(e)
        logger.error("UniFi error: %s", e)
        if entry:
            entry.stale = True
            return entry.data
        return {"clients": [], "ap_map": {}}


def _get_merged_clients() -> list[dict]:
    leases = _get_sophos_leases()
    sophos_cfg = _get_sophos_config()
    unifi_data = _get_unifi_data()

    merged = merge_clients(
        sophos_leases=leases,
        sophos_static=sophos_cfg.get("static_entries", []),
        unifi_clients=unifi_data.get("clients", []),
        unifi_aps=unifi_data.get("ap_map", {}),
    )

    # Enrich with OUI vendor lookup
    for c in merged:
        mac = c.get("mac", "")
        # Prefer UniFi OUI field, fallback to local lookup
        if c.get("unifi") and c["unifi"].get("oui"):
            c["vendor"] = c["unifi"]["oui"]
        else:
            c["vendor"] = oui_lookup(mac)

    return merged


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Gwless", description="DHCP & Network Client Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    log_level = CONFIG.get("app", {}).get("log_level", "info").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    if CONFIG.get("app", {}).get("oui_update_on_start", True):
        ensure_oui_db()


# ---------------------------------------------------------------------------
# API routes  (must be BEFORE StaticFiles mount)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    sophos_status = "error" if _sophos_error else ("stale" if _cache_leases.is_stale else "ok")
    unifi_status = "error" if _unifi_error else ("stale" if _cache_unifi.is_stale else "ok")
    return {
        "status": "ok",
        "sophos": sophos_status,
        "unifi": unifi_status,
        "sophos_error": _sophos_error,
        "unifi_error": _unifi_error,
    }


@app.get("/api/stats")
async def get_stats():
    clients = _get_merged_clients()
    online = [c for c in clients if c.get("online")]
    sophos_only = [c for c in clients if c.get("source") == "sophos_only"]
    unifi_only = [c for c in clients if c.get("source") == "unifi_only"]
    both = [c for c in clients if c.get("source") == "both"]

    sophos_age = _cache_leases.age_seconds
    unifi_age = _cache_unifi.age_seconds

    return {
        "total": len(clients),
        "online": len(online),
        "sophos_only": len(sophos_only),
        "unifi_only": len(unifi_only),
        "both": len(both),
        "sophos_stale": _cache_leases.is_stale,
        "unifi_stale": _cache_unifi.is_stale,
        "sophos_age_seconds": round(sophos_age, 1) if sophos_age is not None else None,
        "unifi_age_seconds": round(unifi_age, 1) if unifi_age is not None else None,
        "sophos_last_updated": _cache_leases.last_updated,
        "unifi_last_updated": _cache_unifi.last_updated,
        "sophos_error": _sophos_error,
        "unifi_error": _unifi_error,
    }


@app.get("/api/clients")
async def get_clients(
    q: Optional[str] = Query(None, description="Search: hostname, IP, MAC, vendor"),
    vlan: Optional[int] = Query(None, description="Filter by VLAN ID"),
    source: Optional[str] = Query(None, description="Filter by source: sophos_only|unifi_only|both"),
    status: Optional[str] = Query(None, description="Filter by status: online|offline"),
):
    clients = _get_merged_clients()

    # Apply filters
    if q:
        q_lower = q.lower()
        clients = [
            c for c in clients
            if q_lower in (c.get("hostname") or "").lower()
            or q_lower in (c.get("ip") or "").lower()
            or q_lower in (c.get("mac") or "").lower()
            or q_lower in (c.get("vendor") or "").lower()
        ]

    if vlan is not None:
        clients = [c for c in clients if c.get("vlan") == vlan]

    if source:
        clients = [c for c in clients if c.get("source") == source]

    if status == "online":
        clients = [c for c in clients if c.get("online")]
    elif status == "offline":
        clients = [c for c in clients if not c.get("online")]

    return {
        "clients": clients,
        "total": len(clients),
        "sophos_stale": _cache_leases.is_stale,
        "unifi_stale": _cache_unifi.is_stale,
        "sophos_age_seconds": round(_cache_leases.age_seconds, 1) if _cache_leases.age_seconds is not None else None,
        "unifi_age_seconds": round(_cache_unifi.age_seconds, 1) if _cache_unifi.age_seconds is not None else None,
    }


@app.get("/api/clients/{mac}")
async def get_client(mac: str):
    normalized = normalize_mac(mac)
    clients = _get_merged_clients()
    for c in clients:
        if c.get("mac") == normalized:
            return c
    raise HTTPException(status_code=404, detail=f"Client {mac} not found")


@app.get("/api/scopes")
async def get_scopes():
    sophos_cfg = _get_sophos_config()
    leases = _get_sophos_leases()
    from .sophos import get_scopes_summary
    scopes = get_scopes_summary(sophos_cfg.get("servers", []), leases)
    return {
        "scopes": scopes,
        "stale": _cache_sophos_cfg.is_stale,
        "last_updated": _cache_sophos_cfg.last_updated,
    }


@app.get("/api/refresh")
async def force_refresh():
    """Invalidate all caches to force fresh data on next request."""
    _cache_leases.invalidate()
    _cache_sophos_cfg.invalidate()
    _cache_unifi.invalidate()
    return {"status": "caches_invalidated"}


# ---------------------------------------------------------------------------
# Static frontend — mount LAST so /api/* routes are not shadowed
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
else:
    logger.warning("Frontend directory not found at %s", _frontend_dir)
