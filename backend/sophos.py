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


def _vlan_from_name(name: str) -> "int | None":
    """Extract VLAN ID from interface name: 'VLAN91' → 91, 'Port5.91' → 91."""
    import re as _re
    m = _re.search(r'\.(\d+)$|[Vv][Ll][Aa][Nn](\d+)', name)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _fetch_vlan_map(config: dict) -> dict:
    """
    Fetch VLAN entity from Sophos XML API.
    Returns {interface_name: vlan_id (int)} — empty dict on any failure.
    """
    try:
        url = _api_url(config)
        payload = _build_payload(config, "VLAN")
        resp = requests.post(
            url,
            data={"reqxml": payload},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            verify=config.get("verify_ssl", False),
            timeout=10,
        )
        resp.raise_for_status()
        doc = xmltodict.parse(resp.text)
        raw = doc.get("Response", {}).get("VLAN", {})
        entries = raw if isinstance(raw, list) else ([raw] if raw else [])
        vlan_map = {}
        for entry in entries:
            if not entry:
                continue
            name = entry.get("Name") or entry.get("@name", "")
            vid_raw = entry.get("VLANID") or entry.get("VLANId") or entry.get("ID") or entry.get("Tag")
            try:
                vid = int(vid_raw) if vid_raw is not None else None
            except (ValueError, TypeError):
                vid = None
            if name and vid:
                vlan_map[name] = vid
        logger.debug("Sophos VLAN map: %s", vlan_map)
        return vlan_map
    except Exception as exc:
        logger.debug("Could not fetch Sophos VLAN map (non-fatal): %s", exc)
        return {}


def _build_payload(config: dict, get_element: str) -> str:
    _ALLOWED_ELEMENTS = {"DHCPServer", "VLAN"}
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


def _extract_reservations(srv: dict) -> list:
    """
    Extract static DHCP reservations from a server dict.
    Handles flat list, single dict, and nested structures like
    StaticLease = {"Host": [...]} or {"HostParams": [...]}.
    """
    raw = (
        srv.get("StaticLease")
        or srv.get("Static")
        or srv.get("Host")
        or srv.get("Reservation")
    )
    if not raw:
        return []
    # Nested container: {"Host": [...]} or {"HostParams": [...]} etc.
    if isinstance(raw, dict):
        for sub_key in ("Host", "HostParams", "Entry", "Lease", "Reservation"):
            if sub_key in raw:
                inner = raw[sub_key]
                if isinstance(inner, list):
                    return inner
                if isinstance(inner, dict):
                    return [inner]
        # No known sub-key — treat the dict itself as a single reservation
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


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
    vlan_map = _fetch_vlan_map(config)
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

        iface = srv.get("Interface", "")
        vlan_id = vlan_map.get(iface) or _vlan_from_name(iface)
        server_info = {
            "name": srv.get("Name") or srv.get("@name", ""),
            "interface": iface,
            "vlan": vlan_id,
            "subnet": srv.get("SubnetMask") or srv.get("Network", ""),
            "gateway": srv.get("Gateway") or srv.get("GatewayIP", ""),
            "dns1": srv.get("PrimaryDNSServer") or srv.get("DNS1", ""),
            "dns2": srv.get("SecondaryDNSServer") or srv.get("DNS2", ""),
            "lease_time": srv.get("DefaultLeaseTime") or srv.get("LeaseTime", ""),
            "range_start": None,
            "range_end": None,
        }

        # IP range
        ip_range = srv.get("IPLease") or srv.get("IPRange") or srv.get("Range") or {}
        if isinstance(ip_range, dict):
            server_info["range_start"] = ip_range.get("StartIP") or ip_range.get("From")
            server_info["range_end"] = ip_range.get("EndIP") or ip_range.get("To")

        servers.append(server_info)

        # Static MAC reservations
        for res in _extract_reservations(srv):
            if not res:
                continue
            static_entries.append({
                "mac": (res.get("MACAddress") or res.get("MAC", "")).lower(),
                "ip":  res.get("IPAddress") or res.get("IP", ""),
                "hostname": (
                    res.get("HostName")
                    or res.get("Name")
                    or res.get("Hostname", "")
                ),
                "scope_name": server_info["name"],
                "vlan": server_info.get("vlan"),
                "sophos_type": "static",
            })

    logger.info(
        "Sophos XML API: %d DHCP servers, %d static entries",
        len(servers),
        len(static_entries),
    )
    return {"servers": servers, "static_entries": static_entries}


def _parse_sophos_status(doc: dict) -> dict:
    """Extract ok/message from a Sophos Set response dict."""
    status = doc.get("Response", {}).get("DHCPServer", {})
    if isinstance(status, dict):
        code = status.get("Status", {})
        if isinstance(code, dict):
            code_val = str(code.get("@code", ""))
            msg = code.get("#text", "")
            if code_val == "200":
                return {"ok": True}
            return {"ok": False, "message": msg or f"Sophos returned code {code_val}"}
    return {"ok": True}


def create_static_reservation(config: dict, server_name: str, mac: str, ip: str, hostname: str) -> dict:
    """
    Add a static DHCP reservation to the named DHCP server via Sophos XML API.

    Sophos <Set> requires the complete DHCPServer object (not a partial update),
    so we first <Get> the current config, append the new static lease, then
    send the full object back with <Set>.

    Returns {"ok": True} or {"ok": False, "message": "..."}.
    """
    host = config.get("host", "")
    if not host:
        raise ValueError("Sophos host is not configured.")
    url = _api_url(config)
    verify = config.get("verify_ssl", False)
    post_kwargs = dict(
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=verify,
        timeout=15,
    )
    username = xml_escape(config.get("username", ""))
    password = xml_escape(config.get("api_password") or config.get("password", ""))

    # ── Step 1: fetch current DHCPServer config ──────────────────────────────
    get_payload = _build_payload(config, "DHCPServer")
    resp = requests.post(url, data={"reqxml": get_payload}, **post_kwargs)
    resp.raise_for_status()
    get_doc = xmltodict.parse(resp.text)
    servers_raw = get_doc.get("Response", {}).get("DHCPServer", {})
    server_list = servers_raw if isinstance(servers_raw, list) else [servers_raw]

    target = None
    for srv in server_list:
        if srv and srv.get("Name") == server_name:
            target = srv
            break
    if target is None:
        return {"ok": False, "message": f"DHCP server '{server_name}' not found on Sophos"}

    # ── Step 2: extract required fields ──────────────────────────────────────
    def _x(val: Any) -> str:
        return xml_escape(str(val)) if val else ""

    iface        = _x(target.get("Interface"))
    subnet       = _x(target.get("SubnetMask") or target.get("Network"))
    gateway      = _x(target.get("Gateway") or target.get("GatewayIP"))
    dns1         = _x(target.get("PrimaryDNSServer") or target.get("DNS1"))
    dns2         = _x(target.get("SecondaryDNSServer") or target.get("DNS2"))
    default_lt   = _x(target.get("DefaultLeaseTime") or "86400")
    max_lt       = _x(target.get("MaxLeaseTime") or target.get("DefaultLeaseTime") or "86400")
    sname        = _x(server_name)

    ip_range = target.get("IPLease") or target.get("IPRange") or target.get("Range") or {}
    range_start = _x(ip_range.get("StartIP") or ip_range.get("From")) if isinstance(ip_range, dict) else ""
    range_end   = _x(ip_range.get("EndIP")   or ip_range.get("To"))   if isinstance(ip_range, dict) else ""

    # ── Step 3: collect existing static leases (preserve them) ───────────────
    existing_macs: set[str] = set()
    hosts_xml = ""
    for res in _extract_reservations(target):
        if not res:
            continue
        h_mac  = _x(res.get("MACAddress") or res.get("MAC"))
        h_ip   = _x(res.get("IPAddress")  or res.get("IP"))
        h_name = _x(res.get("HostName")   or res.get("Name") or res.get("Hostname"))
        if h_mac:
            existing_macs.add(h_mac.lower())
            hosts_xml += (
                f"<Host>"
                f"<MACAddress>{h_mac}</MACAddress>"
                f"<IPAddress>{h_ip}</IPAddress>"
                f"<HostName>{h_name}</HostName>"
                f"</Host>"
            )

    norm_mac = mac.lower()
    if norm_mac in existing_macs:
        return {"ok": False, "message": f"A static reservation for {mac} already exists on this server"}

    # Append the new reservation
    hosts_xml += (
        f"<Host>"
        f"<MACAddress>{_x(mac)}</MACAddress>"
        f"<IPAddress>{_x(ip)}</IPAddress>"
        f"<HostName>{_x(hostname)}</HostName>"
        f"</Host>"
    )

    # ── Step 4: build optional XML fragments ─────────────────────────────────
    range_xml = (
        f"<IPLease><StartIP>{range_start}</StartIP><EndIP>{range_end}</EndIP></IPLease>"
        if range_start and range_end else ""
    )
    dns_xml = ""
    if dns1:
        dns_xml += f"<PrimaryDNSServer>{dns1}</PrimaryDNSServer>"
    if dns2:
        dns_xml += f"<SecondaryDNSServer>{dns2}</SecondaryDNSServer>"

    # ── Step 5: send complete Set payload ─────────────────────────────────────
    set_payload = (
        f"<Request>"
        f"<Login><Username>{username}</Username><Password>{password}</Password></Login>"
        f"<Set>"
        f"<DHCPServer>"
        f"<Name>{sname}</Name>"
        f"<Interface>{iface}</Interface>"
        f"<Gateway>{gateway}</Gateway>"
        f"<SubnetMask>{subnet}</SubnetMask>"
        f"{range_xml}"
        f"{dns_xml}"
        f"<DefaultLeaseTime>{default_lt}</DefaultLeaseTime>"
        f"<MaxLeaseTime>{max_lt}</MaxLeaseTime>"
        f"<StaticLease>{hosts_xml}</StaticLease>"
        f"</DHCPServer>"
        f"</Set>"
        f"</Request>"
    )

    resp = requests.post(url, data={"reqxml": set_payload}, **post_kwargs)
    resp.raise_for_status()
    doc = xmltodict.parse(resp.text)
    logger.debug("Sophos Set/DHCPServer response: %s", resp.text)
    return _parse_sophos_status(doc)


def remove_static_reservation(config: dict, server_name: str, mac: str) -> dict:
    """
    Remove a static DHCP reservation from the named DHCP server via Sophos XML API.

    Fetches the current config, filters out the matching MAC, then sends
    the complete server object back via <Set>.

    Returns {"ok": True} or {"ok": False, "message": "..."}.
    """
    host = config.get("host", "")
    if not host:
        raise ValueError("Sophos host is not configured.")
    url = _api_url(config)
    verify = config.get("verify_ssl", False)
    post_kwargs = dict(
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        verify=verify,
        timeout=15,
    )
    username = xml_escape(config.get("username", ""))
    password = xml_escape(config.get("api_password") or config.get("password", ""))

    # ── Step 1: fetch current DHCPServer config ──────────────────────────────
    get_payload = _build_payload(config, "DHCPServer")
    resp = requests.post(url, data={"reqxml": get_payload}, **post_kwargs)
    resp.raise_for_status()
    get_doc = xmltodict.parse(resp.text)
    servers_raw = get_doc.get("Response", {}).get("DHCPServer", {})
    server_list = servers_raw if isinstance(servers_raw, list) else [servers_raw]

    target = None
    for srv in server_list:
        if srv and srv.get("Name") == server_name:
            target = srv
            break
    if target is None:
        return {"ok": False, "message": f"DHCP server '{server_name}' not found on Sophos"}

    # ── Step 2: extract required fields ──────────────────────────────────────
    def _x(val: Any) -> str:
        return xml_escape(str(val)) if val else ""

    iface      = _x(target.get("Interface"))
    subnet     = _x(target.get("SubnetMask") or target.get("Network"))
    gateway    = _x(target.get("Gateway") or target.get("GatewayIP"))
    dns1       = _x(target.get("PrimaryDNSServer") or target.get("DNS1"))
    dns2       = _x(target.get("SecondaryDNSServer") or target.get("DNS2"))
    default_lt = _x(target.get("DefaultLeaseTime") or "86400")
    max_lt     = _x(target.get("MaxLeaseTime") or target.get("DefaultLeaseTime") or "86400")
    sname      = _x(server_name)

    ip_range    = target.get("IPLease") or target.get("IPRange") or target.get("Range") or {}
    range_start = _x(ip_range.get("StartIP") or ip_range.get("From")) if isinstance(ip_range, dict) else ""
    range_end   = _x(ip_range.get("EndIP")   or ip_range.get("To"))   if isinstance(ip_range, dict) else ""

    # ── Step 3: rebuild static leases, skipping the target MAC ───────────────
    norm_mac = mac.lower()
    hosts_xml = ""
    found = False
    for res in _extract_reservations(target):
        if not res:
            continue
        h_mac = (res.get("MACAddress") or res.get("MAC", "")).lower()
        if h_mac == norm_mac:
            found = True
            continue  # skip — this is the one to remove
        h_ip   = _x(res.get("IPAddress") or res.get("IP"))
        h_name = _x(res.get("HostName")  or res.get("Name") or res.get("Hostname"))
        hosts_xml += (
            f"<Host>"
            f"<MACAddress>{_x(h_mac)}</MACAddress>"
            f"<IPAddress>{h_ip}</IPAddress>"
            f"<HostName>{h_name}</HostName>"
            f"</Host>"
        )

    if not found:
        return {"ok": False, "message": f"No static reservation for {mac} found on '{server_name}'"}

    # ── Step 4: build optional XML fragments ─────────────────────────────────
    range_xml = (
        f"<IPLease><StartIP>{range_start}</StartIP><EndIP>{range_end}</EndIP></IPLease>"
        if range_start and range_end else ""
    )
    dns_xml = ""
    if dns1:
        dns_xml += f"<PrimaryDNSServer>{dns1}</PrimaryDNSServer>"
    if dns2:
        dns_xml += f"<SecondaryDNSServer>{dns2}</SecondaryDNSServer>"

    static_xml = f"<StaticLease>{hosts_xml}</StaticLease>" if hosts_xml else ""

    # ── Step 5: send complete Set payload ────────────────────────────────────
    set_payload = (
        f"<Request>"
        f"<Login><Username>{username}</Username><Password>{password}</Password></Login>"
        f"<Set>"
        f"<DHCPServer>"
        f"<Name>{sname}</Name>"
        f"<Interface>{iface}</Interface>"
        f"<Gateway>{gateway}</Gateway>"
        f"<SubnetMask>{subnet}</SubnetMask>"
        f"{range_xml}"
        f"{dns_xml}"
        f"<DefaultLeaseTime>{default_lt}</DefaultLeaseTime>"
        f"<MaxLeaseTime>{max_lt}</MaxLeaseTime>"
        f"{static_xml}"
        f"</DHCPServer>"
        f"</Set>"
        f"</Request>"
    )

    resp = requests.post(url, data={"reqxml": set_payload}, **post_kwargs)
    resp.raise_for_status()
    doc = xmltodict.parse(resp.text)
    logger.debug("Sophos Remove/DHCPServer response: %s", resp.text)
    return _parse_sophos_status(doc)


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
        vlan_map = _fetch_vlan_map(config)

        for srv in servers:
            keys = [k for k in srv.keys() if not k.startswith("@")]
            iface = srv.get("Interface", "?")
            vlan_id = vlan_map.get(iface) or _vlan_from_name(iface)
            log(f"  Server '{srv.get('Name') or srv.get('@name', '?')}': interface={iface!r}, vlan={vlan_id}, fields={keys}")
            sl = srv.get("StaticLease")
            if sl is not None:
                log(f"    StaticLease type={type(sl).__name__}, value={repr(sl)[:300]}")

        def _count_reservations(s: dict) -> int:
            r = _extract_reservations(s)
            return len(r)

        static_count = sum(_count_reservations(s) for s in servers)
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
