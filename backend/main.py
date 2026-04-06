"""
Gwless — FastAPI application entry point.
API routes MUST be defined before StaticFiles mount.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cache import TTLCache
from .history import init_db, record_seen, get_device, get_recent_events
from .merger import merge_clients, normalize_mac
from .oui import lookup as oui_lookup, ensure_oui_db
from .syslog_server import SyslogReceiver

logger = logging.getLogger(__name__)

_test_executor = ThreadPoolExecutor(max_workers=3)
_history_last_ts: float = 0.0

# ---------------------------------------------------------------------------
# Config path resolution
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    """Return the writable config.yaml path."""
    preferred = Path("/opt/gwless/config.yaml")
    if preferred.exists():
        return preferred
    local = Path("config.yaml")
    if local.exists():
        return local
    # Fallback: write next to the example
    example = Path("config.yaml.example")
    if example.exists():
        return Path("config.yaml")
    return preferred


def _load_config() -> dict:
    candidates = [
        Path("/opt/gwless/config.yaml"),
        Path("config.yaml"),
        Path("config.yaml.example"),
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            logger.info("Loaded config from %s", path)
            return cfg
    logger.warning("No config.yaml found — starting with empty config")
    return {}


def _save_config(cfg: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    path.chmod(0o600)
    logger.info("Config saved to %s", path)


# ---------------------------------------------------------------------------
# Runtime config — mutable, reloaded after saves
# ---------------------------------------------------------------------------

CONFIG: dict = _load_config()

# Defaults for missing keys
CONFIG.setdefault("sophos", {
    "host": "", "ssh_port": 22, "api_port": 4444,
    "username": "admin", "password": "", "api_password": "",
    "verify_ssl": False, "poll_interval_leases": 60, "poll_interval_config": 300,
    "ssh_host_key": "", "ssh_enabled": False,
})
CONFIG.setdefault("unifi", {
    "host": "", "port": 443, "username": "", "password": "",
    "site": "default", "verify_ssl": False, "poll_interval": 30,
})
CONFIG.setdefault("app", {"port": 8080, "log_level": "info", "oui_update_on_start": True, "secret": ""})
CONFIG.setdefault("syslog", {"enabled": False, "port": 514, "bind_host": "0.0.0.0"})

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_cache_leases    = TTLCache(ttl=CONFIG["sophos"].get("poll_interval_leases", 60))
_cache_sophos_cfg = TTLCache(ttl=CONFIG["sophos"].get("poll_interval_config", 300))
_cache_unifi     = TTLCache(ttl=CONFIG["unifi"].get("poll_interval", 30))

_sophos_error: Optional[str] = None
_unifi_error:  Optional[str] = None

_syslog_receiver: Optional[SyslogReceiver] = None


def _rebuild_caches() -> None:
    """Recreate caches with updated TTLs after config change."""
    global _cache_leases, _cache_sophos_cfg, _cache_unifi
    _cache_leases    = TTLCache(ttl=CONFIG["sophos"].get("poll_interval_leases", 60))
    _cache_sophos_cfg = TTLCache(ttl=CONFIG["sophos"].get("poll_interval_config", 300))
    _cache_unifi     = TTLCache(ttl=CONFIG["unifi"].get("poll_interval", 30))


def _start_syslog_if_enabled() -> None:
    """Start (or restart) the syslog receiver according to current CONFIG."""
    global _syslog_receiver
    if _syslog_receiver is not None:
        _syslog_receiver.stop()
        _syslog_receiver = None
    cfg = CONFIG.get("syslog", {})
    if cfg.get("enabled", False):
        _syslog_receiver = SyslogReceiver(
            bind_host=cfg.get("bind_host", "0.0.0.0"),
            port=int(cfg.get("port", 514)),
        )
        _syslog_receiver.start()


_start_syslog_if_enabled()


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _get_sophos_leases() -> list[dict]:
    global _sophos_error

    # Prefer syslog when receiver is active and has data
    if _syslog_receiver is not None and CONFIG.get("syslog", {}).get("enabled", False):
        leases = _syslog_receiver.get_leases()
        if leases:
            _cache_leases.set(leases)
            _sophos_error = None
            return leases
        # Receiver is enabled but no events received yet — fall through to SSH

    entry = _cache_leases.get()
    if entry and not entry.stale:
        return entry.data

    if not CONFIG["sophos"].get("ssh_enabled", False):
        # SSH disabled — return cached data if available, otherwise empty
        if entry:
            entry.stale = True
            return entry.data
        return []

    try:
        from .sophos import fetch_dhcp_leases_ssh

        def _save_host_key(fingerprint: str) -> None:
            CONFIG["sophos"]["ssh_host_key"] = fingerprint
            _save_config(CONFIG)
            logger.info("TOFU: SSH host key saved to config")

        sophos_cfg = {**CONFIG["sophos"], "_save_host_key_cb": _save_host_key}
        leases = fetch_dhcp_leases_ssh(sophos_cfg)
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
        host = unifi_cfg.get("host", "")
        if host and not host.startswith("http"):
            host = f"https://{host}:{unifi_cfg.get('port', 443)}"
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
        sophos_servers=sophos_cfg.get("servers", []),
    )

    for c in merged:
        mac = c.get("mac", "")
        if c.get("unifi") and c["unifi"].get("oui"):
            c["vendor"] = c["unifi"]["oui"]
        else:
            c["vendor"] = oui_lookup(mac)

    # Record to history DB only when cache was actually refreshed
    global _history_last_ts
    cache_ts = max(
        _cache_leases.last_updated or 0,
        _cache_unifi.last_updated or 0,
    )
    if cache_ts > _history_last_ts:
        try:
            record_seen(merged)
            _history_last_ts = cache_ts
        except Exception as e:
            logger.warning("History recording failed: %s", e)

    return merged


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def _read_version() -> str:
    try:
        return (Path(__file__).parent.parent / "VERSION").read_text().strip()
    except Exception:
        return "unknown"


app = FastAPI(title="Gwless", description="DHCP & Network Client Dashboard", version=_read_version())

init_db()

# No CORS middleware — frontend is served from the same origin (StaticFiles mount).
# The browser's default same-origin policy protects all API endpoints.

_VALID_SOURCE = {"sophos_only", "unifi_only", "both"}
_VALID_STATUS = {"online", "offline"}


def _require_secret(x_gwless_secret: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: enforce X-Gwless-Secret when app.secret is configured."""
    secret = CONFIG.get("app", {}).get("secret", "")
    if not secret:
        return  # no secret configured — open access
    if x_gwless_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Gwless-Secret header")


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
# Health + stats
# ---------------------------------------------------------------------------

@app.get("/api/version")
async def get_version():
    return {"version": _read_version()}


@app.get("/health")
async def health():
    sophos_status = "error" if _sophos_error else ("stale" if _cache_leases.is_stale else "ok")
    unifi_status  = "error" if _unifi_error  else ("stale" if _cache_unifi.is_stale  else "ok")
    return {
        "status": "ok",
        "sophos": sophos_status,
        "unifi":  unifi_status,
        "sophos_error": _sophos_error,
        "unifi_error":  _unifi_error,
    }


@app.get("/api/stats")
async def get_stats():
    clients    = _get_merged_clients()
    sophos_age = _cache_leases.age_seconds
    unifi_age  = _cache_unifi.age_seconds
    return {
        "total":       len(clients),
        "online":      sum(1 for c in clients if c.get("online")),
        "sophos_only": sum(1 for c in clients if c.get("source") == "sophos_only"),
        "unifi_only":  sum(1 for c in clients if c.get("source") == "unifi_only"),
        "both":        sum(1 for c in clients if c.get("source") == "both"),
        "sophos_stale": _cache_leases.is_stale,
        "unifi_stale":  _cache_unifi.is_stale,
        "sophos_age_seconds": round(sophos_age, 1) if sophos_age is not None else None,
        "unifi_age_seconds":  round(unifi_age,  1) if unifi_age  is not None else None,
        "sophos_last_updated": _cache_leases.last_updated,
        "unifi_last_updated":  _cache_unifi.last_updated,
        "sophos_error": _sophos_error,
        "unifi_error":  _unifi_error,
    }


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.get("/api/clients")
async def get_clients(
    q:      Optional[str] = Query(None),
    vlan:   Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    if source and source not in _VALID_SOURCE:
        raise HTTPException(status_code=400, detail=f"Invalid source. Must be one of: {', '.join(sorted(_VALID_SOURCE))}")
    if status and status not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(sorted(_VALID_STATUS))}")

    clients = _get_merged_clients()

    if q:
        q_lower = q.lower()
        clients = [
            c for c in clients
            if q_lower in (c.get("hostname") or "").lower()
            or q_lower in (c.get("ip")       or "").lower()
            or q_lower in (c.get("mac")      or "").lower()
            or q_lower in (c.get("vendor")   or "").lower()
        ]
    if vlan   is not None: clients = [c for c in clients if c.get("vlan") == vlan]
    if source:             clients = [c for c in clients if c.get("source") == source]
    if status == "online":  clients = [c for c in clients if c.get("online")]
    elif status == "offline": clients = [c for c in clients if not c.get("online")]

    return {
        "clients": clients,
        "total": len(clients),
        "sophos_stale": _cache_leases.is_stale,
        "unifi_stale":  _cache_unifi.is_stale,
        "sophos_age_seconds": round(_cache_leases.age_seconds, 1) if _cache_leases.age_seconds is not None else None,
        "unifi_age_seconds":  round(_cache_unifi.age_seconds,  1) if _cache_unifi.age_seconds  is not None else None,
    }


@app.get("/api/clients/{mac}")
async def get_client(mac: str):
    normalized = normalize_mac(mac)
    for c in _get_merged_clients():
        if c.get("mac") == normalized:
            return c
    raise HTTPException(status_code=404, detail=f"Client {mac} not found")


@app.get("/api/scopes")
async def get_scopes():
    sophos_cfg = _get_sophos_config()
    leases = _get_sophos_leases()
    from .sophos import get_scopes_summary
    scopes = get_scopes_summary(sophos_cfg.get("servers", []), leases)
    return {"scopes": scopes, "stale": _cache_sophos_cfg.is_stale, "last_updated": _cache_sophos_cfg.last_updated}


@app.get("/api/refresh", dependencies=[Depends(_require_secret)])
async def force_refresh():
    _cache_leases.invalidate()
    _cache_sophos_cfg.invalidate()
    _cache_unifi.invalidate()
    return {"status": "caches_invalidated"}


@app.get("/api/syslog/status")
async def syslog_status():
    """Return syslog receiver status and live lease count."""
    enabled = CONFIG.get("syslog", {}).get("enabled", False)
    if _syslog_receiver is None:
        return {"enabled": enabled, "running": False, "lease_count": 0,
                "messages_received": 0, "last_event_ts": None}
    return {
        "enabled": enabled,
        "running": _syslog_receiver.running,
        "port": _syslog_receiver.port,
        "bind_host": _syslog_receiver.bind_host,
        "lease_count": len(_syslog_receiver.get_leases()),
        "messages_received": _syslog_receiver.messages_received,
        "last_event_ts": _syslog_receiver.last_event_ts,
        "recent_raw": _syslog_receiver.get_recent_raw(),
        "recent_dhcp": _syslog_receiver.get_recent_dhcp(),
    }


# ---------------------------------------------------------------------------
# Config API
# ---------------------------------------------------------------------------

def _masked_config() -> dict:
    """Return config with passwords/secret replaced by a sentinel for the UI."""
    cfg = copy.deepcopy(CONFIG)
    MASKED = "••••••••"
    if cfg.get("sophos", {}).get("password"):
        cfg["sophos"]["password"] = MASKED
    if cfg.get("sophos", {}).get("api_password"):
        cfg["sophos"]["api_password"] = MASKED
    if cfg.get("unifi", {}).get("password"):
        cfg["unifi"]["password"] = MASKED
    if cfg.get("app", {}).get("secret"):
        cfg["app"]["secret"] = MASKED
    # Never expose internal callback
    cfg.get("sophos", {}).pop("_save_host_key_cb", None)
    return cfg


@app.get("/api/config")
async def get_config():
    """Return current config with passwords masked."""
    return _masked_config()


class SophosConfig(BaseModel):
    host:               str  = ""
    ssh_port:           int  = 22
    api_port:           int  = 4444
    username:           str  = "admin"
    password:           str  = ""
    api_password:       str  = ""   # kept for backwards compat; falls back to password
    verify_ssl:         bool = False
    ssh_enabled:        bool = False
    poll_interval_leases: int = 60
    poll_interval_config: int = 300
    ssh_host_key:       str  = ""


class UniFiConfig(BaseModel):
    host:         str  = ""
    port:         int  = 443
    username:     str  = ""
    password:     str  = ""
    site:         str  = "default"
    verify_ssl:   bool = False
    poll_interval: int = 30


class AppConfig(BaseModel):
    port:               int  = 8080
    log_level:          str  = "info"
    oui_update_on_start: bool = True
    secret:             str  = ""


class SyslogConfig(BaseModel):
    enabled:   bool = False
    port:      int  = 514
    bind_host: str  = "0.0.0.0"


class ConfigPayload(BaseModel):
    sophos: SophosConfig = SophosConfig()
    unifi:  UniFiConfig  = UniFiConfig()
    app:    AppConfig    = AppConfig()
    syslog: SyslogConfig = SyslogConfig()


MASKED_SENTINEL = "••••••••"


@app.post("/api/config", dependencies=[Depends(_require_secret)])
async def save_config(payload: ConfigPayload):
    """
    Save configuration. Password fields containing the masked sentinel are
    kept from the existing config (not overwritten).
    """
    global CONFIG, _sophos_error, _unifi_error

    new_cfg = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()

    # Preserve existing passwords if the UI sent back the masked sentinel
    def _keep_if_masked(new_val: str, section: str, key: str) -> str:
        if new_val == MASKED_SENTINEL:
            return CONFIG.get(section, {}).get(key, "")
        return new_val

    new_cfg["sophos"]["password"]     = _keep_if_masked(new_cfg["sophos"]["password"],     "sophos", "password")
    new_cfg["sophos"]["api_password"] = _keep_if_masked(new_cfg["sophos"]["api_password"], "sophos", "api_password")
    new_cfg["unifi"]["password"]      = _keep_if_masked(new_cfg["unifi"]["password"],      "unifi",  "password")
    new_cfg["app"]["secret"]          = _keep_if_masked(new_cfg["app"]["secret"],          "app",    "secret")
    # Preserve TOFU key — UI sends it back as-is (read-only field)
    new_cfg["sophos"]["ssh_host_key"] = new_cfg["sophos"].get("ssh_host_key") or CONFIG.get("sophos", {}).get("ssh_host_key", "")

    _save_config(new_cfg)
    CONFIG = new_cfg
    _sophos_error = None
    _unifi_error  = None
    _rebuild_caches()
    _start_syslog_if_enabled()

    return {"status": "saved"}


# ---------------------------------------------------------------------------
# Connection test endpoints
# Each accepts an optional inline config body so the UI can test before saving.
# Password fields containing the masked sentinel fall back to the saved config.
# ---------------------------------------------------------------------------

def _resolve_sophos_cfg(body: Optional[SophosConfig]) -> dict:
    if body is None:
        return CONFIG["sophos"]
    d = body.dict()
    d["password"]     = d["password"]     if d["password"]     != MASKED_SENTINEL else CONFIG.get("sophos", {}).get("password", "")
    d["api_password"] = d["api_password"] if d["api_password"] != MASKED_SENTINEL else CONFIG.get("sophos", {}).get("api_password", "")
    return d


def _resolve_unifi_cfg(body: Optional[UniFiConfig]) -> dict:
    if body is None:
        return CONFIG["unifi"]
    d = body.dict()
    d["password"] = d["password"] if d["password"] != MASKED_SENTINEL else CONFIG.get("unifi", {}).get("password", "")
    return d


@app.post("/api/test/sophos-ssh", dependencies=[Depends(_require_secret)])
async def test_sophos_ssh(body: Optional[SophosConfig] = None):
    """Test Sophos SSH connectivity. Accepts inline config or uses saved config."""
    try:
        from .sophos import fetch_dhcp_leases_ssh
        leases = fetch_dhcp_leases_ssh(_resolve_sophos_cfg(body))
        return {"ok": True, "message": f"Connected — {len(leases)} active lease(s) found"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/test/sophos-api", dependencies=[Depends(_require_secret)])
async def test_sophos_api(body: Optional[SophosConfig] = None):
    """Test Sophos XML API connectivity. Accepts inline config or uses saved config."""
    try:
        from .sophos import fetch_dhcp_server_config
        result = fetch_dhcp_server_config(_resolve_sophos_cfg(body))
        server_count = len(result.get("servers", []))
        static_count = len(result.get("static_entries", []))
        return {"ok": True, "message": f"Connected — {server_count} DHCP server(s), {static_count} static reservation(s)"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/test/unifi", dependencies=[Depends(_require_secret)])
async def test_unifi(body: Optional[UniFiConfig] = None):
    """Test UniFi API connectivity. Accepts inline config or uses saved config."""
    try:
        from .unifi import UniFiClient
        cfg = _resolve_unifi_cfg(body)
        host = cfg.get("host", "")
        if host and not host.startswith("http"):
            host = f"https://{host}:{cfg.get('port', 443)}"
        client = UniFiClient({**cfg, "host": host})
        clients = client.fetch_clients()
        return {"ok": True, "message": f"Connected — {len(clients)} active client(s)"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Version + Changelog
# ---------------------------------------------------------------------------

def _read_version() -> str:
    for candidate in [
        Path(__file__).parent.parent / "VERSION",
        Path("/opt/gwless/VERSION"),
    ]:
        if candidate.exists():
            return candidate.read_text().strip()
    return "unknown"


def _read_changelog(max_entries: int = 3) -> str:
    """Return the first *max_entries* changelog sections as plain text."""
    for candidate in [
        Path(__file__).parent.parent / "CHANGELOG.md",
        Path("/opt/gwless/CHANGELOG.md"),
    ]:
        if candidate.exists():
            text = candidate.read_text()
            # Split on section headers (## v...) and return first max_entries
            import re
            sections = re.split(r'(?=^## v)', text, flags=re.MULTILINE)
            return "\n".join(s.strip() for s in sections[:max_entries] if s.strip())
    return ""


@app.get("/api/version")
async def get_version():
    return {"version": _read_version()}


@app.get("/api/update/info")
async def update_info():
    """Return current version and recent changelog entries."""
    return {
        "version": _read_version(),
        "changelog": _read_changelog(max_entries=3),
    }


@app.get("/api/update/check")
async def update_check():
    """Compare installed version with latest on GitHub; fetch remote changelog."""
    import urllib.request
    import re as _re
    current = _read_version()
    try:
        base = "https://raw.githubusercontent.com/jonaskul/gwless/main"
        with urllib.request.urlopen(f"{base}/VERSION", timeout=5) as resp:
            latest = resp.read().decode().strip()
        try:
            with urllib.request.urlopen(f"{base}/CHANGELOG.md", timeout=5) as resp:
                cl_text = resp.read().decode()
            sections = _re.split(r'(?=^## v)', cl_text, flags=_re.MULTILINE)
            remote_changelog = "\n".join(s.strip() for s in sections[:5] if s.strip())
        except Exception:
            remote_changelog = ""
    except Exception as exc:
        return {"current": current, "latest": None, "up_to_date": None, "error": str(exc)}
    return {
        "current": current,
        "latest": latest,
        "up_to_date": current == latest,
        "remote_changelog": remote_changelog,
    }


# ---------------------------------------------------------------------------
# Streaming (SSE) test endpoints — live log output
# ---------------------------------------------------------------------------

def _make_sse_response(sync_fn):
    """
    Wraps a sync diagnostic function (signature: fn(log_fn)) in an SSE StreamingResponse.
    log_fn(msg, level, **kw) is injected and bridges sync→async via asyncio.Queue.
    """
    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def log_fn(msg, level="info", **kw):
            loop.call_soon_threadsafe(queue.put_nowait, {"msg": msg, "level": level, **kw})

        async def run():
            try:
                await loop.run_in_executor(_test_executor, lambda: sync_fn(log_fn))
            except Exception as e:
                log_fn(str(e), "err", final=True, ok=False)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.create_task(run())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/test/sophos-ssh/stream", dependencies=[Depends(_require_secret)])
async def test_sophos_ssh_stream(body: Optional[SophosConfig] = None):
    from .sophos import diagnose_ssh
    cfg = _resolve_sophos_cfg(body)
    return _make_sse_response(lambda log_fn: diagnose_ssh(cfg, log_fn))


@app.post("/api/test/sophos-api/stream", dependencies=[Depends(_require_secret)])
async def test_sophos_api_stream(body: Optional[SophosConfig] = None):
    from .sophos import diagnose_api
    cfg = _resolve_sophos_cfg(body)
    return _make_sse_response(lambda log_fn: diagnose_api(cfg, log_fn))


@app.post("/api/test/unifi/stream", dependencies=[Depends(_require_secret)])
async def test_unifi_stream(body: Optional[UniFiConfig] = None):
    from .unifi import UniFiClient
    cfg = _resolve_unifi_cfg(body)
    host = cfg.get("host", "")
    if host and not host.startswith("http"):
        host = f"https://{host}:{cfg.get('port', 443)}"
    merged = {**cfg, "host": host}
    return _make_sse_response(lambda log_fn: UniFiClient(merged).diagnose(log_fn))


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------

@app.get("/api/history/device/{mac}")
async def history_device(mac: str):
    """Return first/last seen and event log for a specific MAC address."""
    return get_device(normalize_mac(mac))


@app.get("/api/history/events")
async def history_events(limit: int = Query(100, le=500)):
    """Return the most recent events across all devices."""
    return {"events": get_recent_events(limit)}


# ---------------------------------------------------------------------------
# Update endpoint — downloads latest code from GitHub, restarts service
# ---------------------------------------------------------------------------

@app.post("/api/update/apply", dependencies=[Depends(_require_secret)])
async def update_apply():
    """Run update.sh inside the container and stream output as SSE."""
    script = Path(__file__).parent.parent / "update.sh"

    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def run():
            try:
                proc = subprocess.Popen(
                    ["bash", str(script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, {"msg": line, "level": "info"}
                        )
                proc.wait()
                if proc.returncode != 0:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"msg": f"Update failed (exit code {proc.returncode})", "level": "err", "final": True, "ok": False},
                    )
                else:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"msg": "Service is restarting…", "level": "ok", "final": True, "ok": True, "restarting": True},
                    )
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"msg": str(e), "level": "err", "final": True, "ok": False}
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async def _run():
            await loop.run_in_executor(_test_executor, run)

        asyncio.create_task(_run())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Static frontend — mount LAST so /api/* routes are not shadowed
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
else:
    logger.warning("Frontend directory not found at %s", _frontend_dir)
