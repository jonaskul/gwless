"""
Embedded UDP syslog receiver for Gwless.

Listens for syslog datagrams from Sophos SFOS and extracts DHCP events
(Acknowledge, Release) to maintain a live lease table — no SSH required.

Sophos SFOS syslog DHCP message format (key=value pairs):
  date=2026-04-05 time=14:30:00 timezone="UTC" device_name="XG-1"
  log_component="DHCP" log_subtype="DHCP Acknowledge"
  src_mac=AA:BB:CC:DD:EE:FF src_ip=192.168.1.100
  hostname="mydevice" lease_time=86400
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Matches key=value and key="value" pairs in Sophos syslog messages
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([\S]*))')


def _parse_kv(msg: str) -> dict:
    """Extract all key=value pairs from a syslog message line."""
    result: dict[str, str] = {}
    for m in _KV_RE.finditer(msg):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        result[key] = val
    return result


def _normalize_mac(mac: str) -> str:
    """Convert any MAC format to lowercase colon-separated (aa:bb:cc:dd:ee:ff)."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2)).lower()


def _fmt_ts(ts: float) -> str:
    """Format a Unix timestamp as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class SyslogReceiver:
    """
    Background UDP syslog receiver that builds a live DHCP lease table.

    Leases are persisted to SQLite so they survive restarts.

    Call start() after construction. Call stop() on shutdown.
    Use get_leases() to retrieve the current active leases as a list of dicts
    compatible with parse_isc_leases() output (for the merger).
    """

    def __init__(self, bind_host: str = "0.0.0.0", port: int = 514) -> None:
        self.bind_host = bind_host
        self.port = port

        self.running: bool = False
        self.last_event_ts: Optional[float] = None
        self.messages_received: int = 0

        self._leases: dict[str, dict] = {}   # mac → lease dict (with internal _keys)
        self._recent_raw: deque[str] = deque(maxlen=20)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._load_persisted_leases()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_persisted_leases(self) -> None:
        """Load non-expired leases from SQLite into memory on startup."""
        try:
            from .history import load_leases
            rows = load_leases()
            now = time.time()
            loaded = 0
            for row in rows:
                mac = row["mac"]
                seen_at = float(row["seen_at"])
                lease_time = int(row["lease_time"])
                if now < seen_at + max(lease_time, 3600) * 1.1:
                    self._leases[mac] = {
                        "mac": mac,
                        "ip": row["ip"],
                        "hostname": row["hostname"] or "",
                        "starts": row["starts"] or "",
                        "ends": row["ends"] or "",
                        "binding_state": "active",
                        "_seen_at": seen_at,
                        "_lease_time": lease_time,
                    }
                    loaded += 1
            if loaded:
                logger.info("Syslog: loaded %d persisted lease(s) from DB", loaded)
        except Exception as exc:
            logger.warning("Syslog: could not load persisted leases: %s", exc)

    def _persist_lease(self, mac: str, lease: dict) -> None:
        try:
            from .history import upsert_lease
            upsert_lease(
                mac=mac,
                ip=lease["ip"],
                hostname=lease["hostname"],
                starts=lease["starts"],
                ends=lease["ends"],
                seen_at=lease["_seen_at"],
                lease_time=lease["_lease_time"],
            )
        except Exception as exc:
            logger.warning("Syslog: failed to persist lease %s: %s", mac, exc)

    def _remove_persisted_lease(self, mac: str) -> None:
        try:
            from .history import delete_lease
            delete_lease(mac)
        except Exception as exc:
            logger.warning("Syslog: failed to remove persisted lease %s: %s", mac, exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background receiver thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="syslog-receiver"
        )
        self._thread.start()
        self.running = True
        logger.info("Syslog receiver starting on %s:%d", self.bind_host, self.port)

    def stop(self) -> None:
        """Signal the receiver thread to stop."""
        self._stop_event.set()
        self.running = False
        logger.info("Syslog receiver stopping")

    # ------------------------------------------------------------------
    # Network loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_host, self.port))
            sock.settimeout(1.0)
            logger.info(
                "Syslog receiver listening on udp://%s:%d", self.bind_host, self.port
            )
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(8192)
                    self._handle_message(
                        data.decode("utf-8", errors="replace"), addr
                    )
                except socket.timeout:
                    pass
                except Exception as exc:
                    logger.warning("Syslog recv error: %s", exc)
        except OSError as exc:
            logger.error(
                "Syslog receiver failed to bind to %s:%d — %s",
                self.bind_host,
                self.port,
                exc,
            )
            self.running = False
        finally:
            if sock:
                sock.close()

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _handle_message(self, msg: str, addr: tuple) -> None:
        self.messages_received += 1
        with self._lock:
            self._recent_raw.append(msg[:300])

        fields = _parse_kv(msg)

        # SFOS uses log_component="DHCP Server"
        component = fields.get("log_component", "").lower()
        if "dhcp" not in component:
            return

        # SFOS uses status="Renew"|"Release"|"Expire" (not log_subtype)
        status = fields.get("status", "").lower()

        # IP: SFOS 18+ uses leased_ip; older versions used src_ip / ipaddress
        src_ip = (
            fields.get("leased_ip")
            or fields.get("src_ip")
            or fields.get("ipaddress", "")
        )
        src_mac = fields.get("src_mac", "")

        if not src_mac or not src_ip or src_mac == "-" or src_ip == "-":
            return

        mac = _normalize_mac(src_mac)
        # SFOS uses client_host_name; fallback to hostname
        hostname = fields.get("client_host_name") or fields.get("hostname", "")
        now = time.time()
        self.last_event_ts = now

        if status == "renew":
            try:
                lease_time = int(fields.get("lease_time", 86400))
            except ValueError:
                lease_time = 86400

            lease = {
                "mac": mac,
                "ip": src_ip,
                "hostname": hostname,
                "starts": _fmt_ts(now),
                "ends": _fmt_ts(now + lease_time),
                "binding_state": "active",
                "_seen_at": now,
                "_lease_time": lease_time,
            }
            with self._lock:
                self._leases[mac] = lease
            self._persist_lease(mac, lease)
            logger.debug(
                "Syslog DHCP renew: %s → %s (%s)", mac, src_ip, hostname or "—"
            )

        elif status in ("release", "expire"):
            with self._lock:
                removed = self._leases.pop(mac, None)
            if removed:
                self._remove_persisted_lease(mac)
                logger.debug("Syslog DHCP %s: %s (%s)", status, mac, src_ip)

    # ------------------------------------------------------------------
    # Lease access
    # ------------------------------------------------------------------

    def get_leases(self) -> list[dict]:
        """
        Return current active leases as a list of dicts.

        Expired leases (not renewed within lease_time * 1.1) are automatically
        pruned. Internal ``_`` keys are stripped from returned dicts.
        """
        now = time.time()
        with self._lock:
            active = {
                mac: lease
                for mac, lease in self._leases.items()
                if now < lease["_seen_at"] + max(lease["_lease_time"], 3600) * 1.1
            }
            if len(active) != len(self._leases):
                expired_macs = set(self._leases) - set(active)
                logger.debug("Syslog: pruned %d expired lease(s)", len(expired_macs))
                self._leases = active
                for mac in expired_macs:
                    self._remove_persisted_lease(mac)

            return [
                {k: v for k, v in lease.items() if not k.startswith("_")}
                for lease in active.values()
            ]

    def get_recent_raw(self) -> list[str]:
        """Return the last up to 20 raw syslog messages received (any type)."""
        with self._lock:
            return list(self._recent_raw)
