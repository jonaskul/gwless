"""
SQLite-backed device history.
Records first/last seen per MAC and generates events for notable changes.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "history.db"
_lock = threading.Lock()
_PRUNE_DAYS = 90
_PRUNE_INTERVAL = 3600  # re-prune at most once per hour during runtime
_last_prune: float = 0.0

# ---------------------------------------------------------------------------
# Single persistent connection — avoids per-call open/close overhead.
# Protected by _lock; check_same_thread=False is safe because we never use
# the connection outside the lock.
# ---------------------------------------------------------------------------
_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        # WAL mode: writers don't block readers; better concurrent performance.
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA synchronous=NORMAL")
        _db.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
    return _db


def init_db() -> None:
    """Create tables, enable WAL, prune old events. Safe to call on every startup."""
    with _lock:
        db = _conn()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                mac           TEXT PRIMARY KEY,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                last_ip       TEXT,
                last_hostname TEXT,
                vendor        TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     INTEGER NOT NULL,
                mac    TEXT NOT NULL,
                event  TEXT NOT NULL,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS events_mac ON events(mac);
            CREATE INDEX IF NOT EXISTS events_ts  ON events(ts);
            CREATE TABLE IF NOT EXISTS dhcp_leases (
                mac           TEXT PRIMARY KEY,
                ip            TEXT NOT NULL,
                hostname      TEXT,
                starts        TEXT,
                ends          TEXT,
                binding_state TEXT DEFAULT 'active',
                seen_at       INTEGER NOT NULL,
                lease_time    INTEGER NOT NULL DEFAULT 86400
            );
        """)
        # Migration: add custom_name column if not present (SQLite has no IF NOT EXISTS for columns)
        try:
            db.execute("ALTER TABLE devices ADD COLUMN custom_name TEXT")
            db.commit()
        except Exception:
            pass  # column already exists
        _prune(db)
    logger.info("History DB initialised at %s", DB_PATH)


def _prune(db: sqlite3.Connection) -> None:
    """Delete events older than _PRUNE_DAYS. Caller must hold _lock."""
    global _last_prune
    cutoff = int(time.time()) - _PRUNE_DAYS * 86400
    result = db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    db.commit()
    if result.rowcount:
        logger.info("Pruned %d old events from history DB", result.rowcount)
    _last_prune = time.time()


def _maybe_prune(db: sqlite3.Connection) -> None:
    """Prune once per _PRUNE_INTERVAL during normal operation."""
    if time.time() - _last_prune > _PRUNE_INTERVAL:
        _prune(db)


# ---------------------------------------------------------------------------
# DHCP lease persistence (for syslog receiver)
# ---------------------------------------------------------------------------

def upsert_lease(mac: str, ip: str, hostname: str, starts: str, ends: str,
                 seen_at: float, lease_time: int) -> None:
    """Insert or update a DHCP lease."""
    with _lock:
        db = _conn()
        db.execute(
            """INSERT INTO dhcp_leases
               (mac, ip, hostname, starts, ends, binding_state, seen_at, lease_time)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
               ON CONFLICT(mac) DO UPDATE SET
                 ip=excluded.ip, hostname=excluded.hostname,
                 starts=excluded.starts, ends=excluded.ends,
                 seen_at=excluded.seen_at, lease_time=excluded.lease_time,
                 binding_state='active'""",
            (mac, ip, hostname, starts, ends, int(seen_at), lease_time),
        )
        db.commit()


def delete_lease(mac: str) -> None:
    """Remove a DHCP lease (on release/expire)."""
    with _lock:
        db = _conn()
        db.execute("DELETE FROM dhcp_leases WHERE mac = ?", (mac,))
        db.commit()


def load_leases() -> list[dict]:
    """Load all non-expired leases from DB. Called at startup."""
    with _lock:
        db = _conn()
        rows = db.execute(
            "SELECT * FROM dhcp_leases WHERE seen_at + lease_time * 1.1 > ?",
            (int(time.time()),),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_lease_by_ip(ip: str) -> None:
    """Remove a DHCP lease by IP address (for Expire events that lack a MAC)."""
    with _lock:
        db = _conn()
        db.execute("DELETE FROM dhcp_leases WHERE ip = ?", (ip,))
        db.commit()


def record_seen(clients: list[dict]) -> None:
    """
    Upsert last_seen for each client; generate events on first appearance
    or when IP/hostname changes. Called after each fresh data merge.
    """
    now = int(time.time())
    with _lock:
        db = _conn()
        _maybe_prune(db)
        for c in clients:
            mac = c.get("mac", "").strip()
            if not mac:
                continue
            ip       = c.get("ip", "") or ""
            hostname = c.get("hostname", "") or ""
            vendor   = c.get("vendor", "") or ""

            row = db.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()

            if row is None:
                db.execute(
                    "INSERT INTO devices (mac, first_seen, last_seen, last_ip, last_hostname, vendor)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (mac, now, now, ip, hostname, vendor),
                )
                db.execute(
                    "INSERT INTO events (ts, mac, event, detail) VALUES (?, ?, 'new_device', ?)",
                    (now, mac, f"First seen — IP {ip}, hostname {hostname!r}"),
                )
            else:
                changes: dict = {"last_seen": now}
                if vendor:
                    changes["vendor"] = vendor
                if ip and ip != row["last_ip"]:
                    db.execute(
                        "INSERT INTO events (ts, mac, event, detail) VALUES (?, ?, 'ip_change', ?)",
                        (now, mac, f"{row['last_ip'] or '?'} → {ip}"),
                    )
                    changes["last_ip"] = ip
                if hostname and hostname != row["last_hostname"]:
                    old_hn = row["last_hostname"] or "?"
                    db.execute(
                        "INSERT INTO events (ts, mac, event, detail) VALUES (?, ?, 'hostname_change', ?)",
                        (now, mac, f"{old_hn!r} → {hostname!r}"),
                    )
                    changes["last_hostname"] = hostname
                set_sql = ", ".join(f"{k} = ?" for k in changes)
                db.execute(
                    f"UPDATE devices SET {set_sql} WHERE mac = ?",
                    (*changes.values(), mac),
                )
        db.commit()


def get_device(mac: str) -> dict:
    """Return device record + recent events for a MAC address."""
    with _lock:
        db = _conn()
        row = db.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        events = db.execute(
            "SELECT * FROM events WHERE mac = ? ORDER BY ts DESC LIMIT 50", (mac,)
        ).fetchall()
    return {
        "device": dict(row) if row else None,
        "events": [dict(e) for e in events],
    }


def set_custom_name(mac: str, name: str) -> None:
    """Set (or clear) a custom display name for a device MAC."""
    with _lock:
        db = _conn()
        db.execute(
            "INSERT INTO devices (mac, first_seen, last_seen, custom_name) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(mac) DO UPDATE SET custom_name=excluded.custom_name",
            (mac, int(time.time()), int(time.time()), name or None),
        )
        db.commit()


def get_all_custom_names() -> dict:
    """Return {mac: custom_name} for all devices that have a custom name set."""
    with _lock:
        db = _conn()
        rows = db.execute(
            "SELECT mac, custom_name FROM devices WHERE custom_name IS NOT NULL AND custom_name != ''"
        ).fetchall()
    return {r["mac"]: r["custom_name"] for r in rows}


def get_recent_events(limit: int = 100) -> list[dict]:
    """Return the most recent events across all devices."""
    with _lock:
        db = _conn()
        rows = db.execute(
            """SELECT e.*, COALESCE(d.custom_name, d.last_hostname) AS hostname
               FROM events e
               LEFT JOIN devices d ON e.mac = d.mac
               ORDER BY e.ts DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
