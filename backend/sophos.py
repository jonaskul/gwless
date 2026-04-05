"""
Sophos XGS (SFOS 22) integration.

Two data sources:
  A) SSH -> invoke_shell navigation to advanced shell -> read /tmp/dhcpd.leases
  B) XML API -> DHCPServer configuration and static reservations
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

import requests
import urllib3
import xmltodict

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    logger.warning("paramiko not installed — SSH lease fetching disabled")


# ---------------------------------------------------------------------------
# SSH host key: Trust On First Use (TOFU) policy
# ---------------------------------------------------------------------------

if PARAMIKO_AVAILABLE:
    class _TOFUPolicy(paramiko.MissingHostKeyPolicy):
        """
        On first connect: accept the key and invoke save_callback(fingerprint).
        On subsequent connects: reject if fingerprint doesn't match saved value.
        """
        def __init__(self, saved_fingerprint: str, save_callback):
            self._saved = saved_fingerprint
            self._save_callback = save_callback

        def missing_host_key(self, client, hostname, key):
            fingerprint = key.get_fingerprint().hex()
            if not self._saved:
                logger.info("TOFU: trusting SSH host key for %s (%s)", hostname, fingerprint)
                self._save_callback(fingerprint)
            elif fingerprint != self._saved:
                raise ValueError(
                    f"SSH host key mismatch for {hostname}. "
                    f"Expected {self._saved}, got {fingerprint}. "
                    f"If the device changed, clear 'ssh_host_key' in config.yaml to re-trust."
                )


# ---------------------------------------------------------------------------
# SSH — dynamic leases
# ---------------------------------------------------------------------------

def _ssh_recv_until(channel, markers: list[str], timeout: float = 10.0) -> str:
    """Read from SSH channel until one of the marker strings appears."""
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if channel.recv_ready():
            chunk = channel.recv(65535).decode("utf-8", errors="replace")
            buf += chunk
            for marker in markers:
                if marker in buf:
                    return buf
        time.sleep(0.1)
    return buf


def fetch_dhcp_leases_ssh(config: dict) -> list[dict]:
    """
    Connect to Sophos via SSH, navigate menu to advanced shell,
    cat /tmp/dhcpd.leases and parse active leases.
    """
    if not PARAMIKO_AVAILABLE:
        raise RuntimeError("paramiko is not installed")

    host = config.get("host", "")
    if not host:
        raise ValueError("Sophos host is not configured. Open Settings to add credentials.")
    port = config.get("ssh_port", 22)
    username = config["username"]
    password = config["password"]

    logger.debug("SSH connecting to Sophos device")

    client = paramiko.SSHClient()
    saved_key = config.get("ssh_host_key", "")
    save_cb   = config.get("_save_host_key_cb")  # injected by main.py for TOFU
    if save_cb is not None:
        client.set_missing_host_key_policy(_TOFUPolicy(saved_key, save_cb))
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        channel = client.invoke_shell(width=220, height=50)
        time.sleep(0.5)

        # Drain initial banner
        _ssh_recv_until(channel, ["Select Menu Number:", "$", "#"], timeout=5)

        # Navigate: 5 = Device Management
        channel.send("5\n")
        time.sleep(0.5)
        out = _ssh_recv_until(channel, ["Select Menu Number:"], timeout=8)
        logger.debug("After sending '5': %s", out[-200:])

        # Navigate: 3 = Advanced Shell
        channel.send("3\n")
        time.sleep(0.5)
        out = _ssh_recv_until(channel, ["$", "#"], timeout=8)
        logger.debug("After sending '3': %s", out[-200:])

        # Read leases file
        channel.send("cat /tmp/dhcpd.leases\n")
        time.sleep(0.5)
        lease_output = _ssh_recv_until(channel, ["$", "#"], timeout=10)
        logger.debug("Lease output length: %d chars", len(lease_output))

        channel.close()
        return parse_isc_leases(lease_output)
    finally:
        client.close()


def parse_isc_leases(raw: str) -> list[dict]:
    """Parse ISC DHCP lease format, returning only active leases."""
    leases = []
    # Split on lease blocks
    blocks = re.findall(
        r"lease\s+([\d.]+)\s*\{([^}]+)\}",
        raw,
        re.DOTALL,
    )
    for ip, body in blocks:
        fields: dict[str, Any] = {"ip": ip}

        # binding state
        m = re.search(r"binding state\s+(\w+);", body)
        if not m or m.group(1) != "active":
            continue
        fields["binding_state"] = "active"

        # hardware ethernet
        m = re.search(r"hardware ethernet\s+([\da-fA-F:]+);", body)
        if m:
            fields["mac"] = m.group(1).lower()

        # client-hostname
        m = re.search(r'client-hostname\s+"([^"]+)";', body)
        if m:
            fields["hostname"] = m.group(1)

        # starts
        m = re.search(r"starts\s+\d+\s+([\d/]+ [\d:]+);", body)
        if m:
            fields["starts"] = m.group(1)

        # ends
        m = re.search(r"ends\s+\d+\s+([\d/]+ [\d:]+);", body)
        if m:
            fields["ends"] = m.group(1)

        leases.append(fields)

    logger.info("Parsed %d active leases from SSH", len(leases))
    return leases


# ---------------------------------------------------------------------------
# XML API — server config and static entries
# ---------------------------------------------------------------------------

def _api_url(config: dict) -> str:
    port = config.get("api_port", 4444)
    return f"https://{config['host']}:{port}/webconsole/APIController"


def _build_payload(config: dict, get_element: str) -> str:
    _ALLOWED_ELEMENTS = {"DHCPServer"}
    if get_element not in _ALLOWED_ELEMENTS:
        raise ValueError(f"Invalid element: {get_element!r}. Allowed: {_ALLOWED_ELEMENTS}")
    username = xml_escape(config.get("username", ""))
    password = xml_escape(config.get("api_password") or config.get("password", ""))
    xml = (
        f"<Request>"
        f"<Login><Username>{username}</Username><Password>{password}</Password></Login>"
        f"<Get><{get_element}></{get_element}></Get>"
        f"</Request>"
    )
    return xml


def fetch_dhcp_server_config(config: dict) -> dict:
    """
    Fetch DHCPServer configuration from Sophos XML API.
    Returns dict with 'servers' (list) and 'static_entries' (list).
    """
    host = config.get("host", "")
    if not host:
        raise ValueError("Sophos host is not configured. Open Settings to add credentials.")
    url = _api_url(config)
    payload = _build_payload(config, "DHCPServer")

    logger.debug("Fetching Sophos XML API: %s", url)
    resp = requests.post(
        url,
        data={"reqxml": payload},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=config.get("verify_ssl", False),
        timeout=15,
    )
    resp.raise_for_status()

    try:
        doc = xmltodict.parse(resp.text)
    except Exception as e:
        logger.error("Failed to parse Sophos XML response: %s", e)
        return {"servers": [], "static_entries": []}

    response = doc.get("Response", {})
    servers_raw = response.get("DHCPServer", {})

    servers = []
    static_entries = []

    # DHCPServer may be a dict (single) or list (multiple)
    server_list = servers_raw if isinstance(servers_raw, list) else [servers_raw]

    for srv in server_list:
        if not srv:
            continue

        server_info = {
            "name": srv.get("@name") or srv.get("Name", ""),
            "interface": srv.get("Interface", ""),
            "subnet": srv.get("Network", ""),
            "gateway": srv.get("GatewayIP", ""),
            "dns1": srv.get("DNS1", ""),
            "dns2": srv.get("DNS2", ""),
            "lease_time": srv.get("LeaseTime", ""),
            "range_start": None,
            "range_end": None,
        }

        # IP range
        ip_range = srv.get("IPRange") or srv.get("Range") or {}
        if isinstance(ip_range, dict):
            server_info["range_start"] = ip_range.get("StartIP") or ip_range.get("From")
            server_info["range_end"] = ip_range.get("EndIP") or ip_range.get("To")

        servers.append(server_info)

        # Static MAC reservations
        reservations = srv.get("Static") or []
        if isinstance(reservations, dict):
            reservations = [reservations]
        for res in reservations:
            if not res:
                continue
            static_entries.append({
                "mac": (res.get("MACAddress") or res.get("MAC", "")).lower(),
                "ip": res.get("IPAddress") or res.get("IP", ""),
                "hostname": res.get("Name") or res.get("Hostname", ""),
                "scope_name": server_info["name"],
                "sophos_type": "static",
            })

    logger.info(
        "Sophos XML API: %d DHCP servers, %d static entries",
        len(servers),
        len(static_entries),
    )
    return {"servers": servers, "static_entries": static_entries}


def diagnose_ssh(config: dict, log_fn=None) -> None:
    """
    Diagnostic version of fetch_dhcp_leases_ssh that reports progress via log_fn(msg, level).
    Used by the live-log streaming endpoint.
    """
    def log(msg, level="info", **kw):
        logger.info(msg)
        if log_fn:
            log_fn(msg, level, **kw)

    if not PARAMIKO_AVAILABLE:
        log("paramiko is not installed — cannot run SSH test", "err", final=True, ok=False)
        return

    host = config.get("host", "").strip()
    if not host:
        log("Sophos host is not configured. Open Settings to add credentials.", "err", final=True, ok=False)
        return

    port = config.get("ssh_port", 22)
    username = config.get("username", "")
    password = config.get("password", "")

    log(f"Connecting to {host}:{port}…")
    client = paramiko.SSHClient()
    saved_key = config.get("ssh_host_key", "")
    save_cb   = config.get("_save_host_key_cb")
    if save_cb is not None:
        client.set_missing_host_key_policy(_TOFUPolicy(saved_key, save_cb))
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        log("SSH handshake complete, checking host key…")
        log(f"Authenticated as {username!r}", "ok")

        channel = client.invoke_shell(width=220, height=50)
        time.sleep(0.5)
        _ssh_recv_until(channel, ["Select Menu Number:", "$", "#"], timeout=5)

        log("Navigating to advanced shell (Device Management → Advanced Shell)…")
        channel.send("5\n")
        time.sleep(0.5)
        _ssh_recv_until(channel, ["Select Menu Number:"], timeout=8)
        channel.send("3\n")
        time.sleep(0.5)
        _ssh_recv_until(channel, ["$", "#"], timeout=8)
        log("Shell prompt reached")

        log("Reading /tmp/dhcpd.leases…")
        channel.send("cat /tmp/dhcpd.leases\n")
        time.sleep(0.5)
        lease_output = _ssh_recv_until(channel, ["$", "#"], timeout=10)
        channel.close()

        leases = parse_isc_leases(lease_output)
        log(f"Found {len(leases)} active lease(s)", "ok", final=True, ok=True)
    except Exception as e:
        log(str(e), "err", final=True, ok=False)
    finally:
        client.close()


def diagnose_api(config: dict, log_fn=None) -> None:
    """
    Diagnostic version of fetch_dhcp_server_config that reports progress via log_fn(msg, level).
    Used by the live-log streaming endpoint.
    """
    def log(msg, level="info", **kw):
        logger.info(msg)
        if log_fn:
            log_fn(msg, level, **kw)

    host = config.get("host", "").strip()
    if not host:
        log("Sophos host is not configured. Open Settings to add credentials.", "err", final=True, ok=False)
        return

    url = _api_url(config)
    log(f"Connecting to {url}…")

    try:
        payload = _build_payload(config, "DHCPServer")
        log("Authenticating…")
        resp = requests.post(
            url,
            data={"reqxml": payload},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            verify=config.get("verify_ssl", False),
            timeout=15,
        )
        resp.raise_for_status()
        log("Querying DHCPServer configuration…")

        doc = xmltodict.parse(resp.text)
        response = doc.get("Response", {})
        servers_raw = response.get("DHCPServer", {})
        server_list = servers_raw if isinstance(servers_raw, list) else [servers_raw]
        servers = [s for s in server_list if s]

        static_count = sum(
            len(s.get("Static", []) if isinstance(s.get("Static"), list) else ([s.get("Static")] if s.get("Static") else []))
            for s in servers
        )
        log(f"Found {len(servers)} DHCP server(s), {static_count} static reservation(s)", "ok", final=True, ok=True)
    except Exception as e:
        log(str(e), "err", final=True, ok=False)


def get_scopes_summary(servers: list[dict], active_leases: list[dict]) -> list[dict]:
    """Build scope summary with used/total lease counts."""
    # Build quick lookup of how many leases per scope
    # We don't have a direct scope→lease mapping, so approximate by subnet
    scopes = []
    for srv in servers:
        subnet = srv.get("subnet", "")
        # Count leases in range (simple prefix match on first 3 octets)
        prefix = ".".join(subnet.split(".")[:3]) + "." if subnet else ""
        used = sum(1 for l in active_leases if l.get("ip", "").startswith(prefix)) if prefix else 0

        # Calculate total from range
        total = 0
        try:
            start = srv.get("range_start") or ""
            end = srv.get("range_end") or ""
            if start and end:
                s_parts = [int(x) for x in start.split(".")]
                e_parts = [int(x) for x in end.split(".")]
                total = (
                    (e_parts[3] - s_parts[3] + 1)
                    + (e_parts[2] - s_parts[2]) * 256
                )
        except Exception:
            pass

        scopes.append({
            "name": srv.get("name", ""),
            "interface": srv.get("interface", ""),
            "subnet": subnet,
            "range_start": srv.get("range_start"),
            "range_end": srv.get("range_end"),
            "gateway": srv.get("gateway"),
            "dns1": srv.get("dns1"),
            "dns2": srv.get("dns2"),
            "lease_time": srv.get("lease_time"),
            "leases_used": used,
            "leases_total": total,
        })
    return scopes
