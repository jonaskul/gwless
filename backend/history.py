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


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """Create tables and prune old events. Safe to call on every startup."""
    with _lock, _conn() as db:
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
        cutoff = int(time.time()) - _PRUNE_DAYS * 86400
        db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    logger.info("History DB initialised at %s", DB_PATH)


# ---------------------------------------------------------------------------
# DHCP lease persistence (for syslog receiver)
# ---------------------------------------------------------------------------

def upsert_lease(mac: str, ip: str, hostname: str, starts: str, ends: str,
                 seen_at: float, lease_time: int) -> None:
    """Insert or update a DHCP lease."""
    with _lock, _conn() as db:
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


def delete_lease(mac: str) -> None:
    """Remove a DHCP lease (on release/expire)."""
    with _lock, _conn() as db:
        db.execute("DELETE FROM dhcp_leases WHERE mac = ?", (mac,))


def load_leases() -> list[dict]:
    """Load all non-expired leases from DB. Called at startup."""
    cutoff = int(time.time() * 1.1)  # small tolerance
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM dhcp_leases WHERE seen_at + lease_time * 1.1 > ?",
            (int(time.time()),),
        ).fetchall()
    return [dict(r) for r in rows]


def record_seen(clients: list[dict]) -> None:
    """
    Upsert last_seen for each client; generate events on first appearance
    or when IP/hostname changes. Called after each fresh data merge.
    """
    now = int(time.time())
    with _lock, _conn() as db:
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


def get_device(mac: str) -> dict:
    """Return device record + recent events for a MAC address."""
    with _conn() as db:
        row = db.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        events = db.execute(
            "SELECT * FROM events WHERE mac = ? ORDER BY ts DESC LIMIT 50", (mac,)
        ).fetchall()
    return {
        "device": dict(row) if row else None,
        "events": [dict(e) for e in events],
    }


def get_recent_events(limit: int = 100) -> list[dict]:
    """Return the most recent events across all devices."""
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
