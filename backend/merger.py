"""
Join logic for merging Sophos DHCP leases with UniFi client data.
Primary key: MAC address (normalized lowercase, colon-separated).
Fallback: IP address match when MAC is missing from one source.
"""
from __future__ import annotations

import re
from typing import Any, Optional


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to lowercase colon-separated format."""
    if not mac:
        return ""
    # Strip any separators and normalize
    clean = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(clean) != 12:
        return mac.lower()
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2)).lower()


def normalize_ip(ip: str) -> str:
    return ip.strip() if ip else ""


def _vlan_for_ip(ip: str, servers: list[dict]) -> "int | None":
    """Match an IP address to a DHCP server by subnet prefix and return its VLAN."""
    if not ip or not servers:
        return None
    prefix = ".".join(ip.split(".")[:3])
    for s in servers:
        for ref in (s.get("gateway", ""), s.get("subnet", ""),
                    s.get("range_start", "") or ""):
            if ref and ref.startswith(prefix + "."):
                return s.get("vlan")
    return None


def _scope_for_ip(ip: str, servers: list[dict]) -> "str | None":
    """Match an IP address to a DHCP server by subnet prefix and return its name."""
    if not ip or not servers:
        return None
    prefix = ".".join(ip.split(".")[:3])
    for s in servers:
        for ref in (s.get("gateway", ""), s.get("subnet", ""),
                    s.get("range_start", "") or ""):
            if ref and ref.startswith(prefix + "."):
                return s.get("name") or None
    return None


def _range_for_ip(ip: str, servers: list[dict]) -> "tuple[str, str]":
    """Match an IP address to a DHCP server and return its (range_start, range_end)."""
    if not ip or not servers:
        return ("", "")
    prefix = ".".join(ip.split(".")[:3])
    for s in servers:
        for ref in (s.get("gateway", ""), s.get("subnet", ""),
                    s.get("range_start", "") or ""):
            if ref and ref.startswith(prefix + "."):
                return (s.get("range_start") or "", s.get("range_end") or "")
    return ("", "")


def merge_clients(
    sophos_leases: list[dict],
    sophos_static: list[dict],
    unifi_clients: list[dict],
    unifi_aps: dict[str, str],
    sophos_servers: "list[dict] | None" = None,
) -> list[dict]:
    """
    Merge Sophos and UniFi data into unified client records.

    Args:
        sophos_leases: Active dynamic leases from Sophos SSH.
        sophos_static: Static DHCP reservations from Sophos XML API.
        unifi_clients: Active clients from UniFi stat/sta endpoint.
        unifi_aps: Dict mapping AP MAC -> AP name.
        sophos_servers: DHCP server configs with VLAN info for IP-based VLAN lookup.

    Returns:
        List of merged client dicts.
    """
    servers = sophos_servers or []

    # Index Sophos data by MAC (primary) and IP (fallback)
    sophos_by_mac: dict[str, dict] = {}
    sophos_by_ip: dict[str, dict] = {}

    for lease in sophos_leases:
        mac = normalize_mac(lease.get("mac", ""))
        ip = normalize_ip(lease.get("ip", ""))
        # Attach VLAN, scope_name and range from server config if not already set
        vlan = lease.get("vlan") or _vlan_for_ip(ip, servers)
        scope_name = lease.get("scope_name") or _scope_for_ip(ip, servers)
        range_start, range_end = _range_for_ip(ip, servers)
        record = {**lease, "mac": mac, "ip": ip, "sophos_type": "dynamic", "vlan": vlan,
                  "scope_name": scope_name, "range_start": range_start, "range_end": range_end}
        if mac:
            sophos_by_mac[mac] = record
        if ip:
            sophos_by_ip[ip] = record

    # Merge static entries — static reservations take priority over dynamic leases
    # (a device can appear in both lists if it has a reservation and is currently active)
    for static in sophos_static:
        mac = normalize_mac(static.get("mac", ""))
        ip = normalize_ip(static.get("ip", ""))
        record = {**static, "mac": mac, "ip": ip, "sophos_type": "static"}
        if mac:
            sophos_by_mac[mac] = record
        if ip:
            sophos_by_ip[ip] = record

    # Index UniFi data by MAC (primary) and IP (fallback)
    unifi_by_mac: dict[str, dict] = {}
    unifi_by_ip: dict[str, dict] = {}

    for client in unifi_clients:
        mac = normalize_mac(client.get("mac", ""))
        ip = normalize_ip(client.get("ip", ""))
        # Resolve AP name
        ap_mac = normalize_mac(client.get("ap_mac", ""))
        ap_name = unifi_aps.get(ap_mac, ap_mac) if ap_mac else None
        record = {**client, "mac": mac, "ip": ip, "ap_name": ap_name}
        if mac:
            unifi_by_mac[mac] = record
        if ip:
            unifi_by_ip[ip] = record

    merged: dict[str, dict] = {}

    # Process all Sophos records
    for mac, sophos in sophos_by_mac.items():
        unifi = unifi_by_mac.get(mac)
        if unifi is None:
            ip = sophos.get("ip", "")
            unifi = unifi_by_ip.get(ip)

        record = _build_record(mac, sophos, unifi)
        merged[mac] = record

    # Process UniFi-only records (not yet merged)
    for mac, unifi in unifi_by_mac.items():
        if mac in merged:
            continue
        sophos = sophos_by_ip.get(normalize_ip(unifi.get("ip", "")))
        record = _build_record(mac, sophos, unifi)
        merged[mac] = record

    return list(merged.values())


def _build_record(
    mac: str,
    sophos: Optional[dict],
    unifi: Optional[dict],
) -> dict:
    """Build a unified client record from Sophos and/or UniFi data."""
    if sophos and unifi:
        source = "both"
    elif sophos:
        source = "sophos_only"
    else:
        source = "unifi_only"

    # Determine hostname: prefer sophos client-hostname, fallback to UniFi
    hostname = (
        (sophos or {}).get("hostname")
        or (unifi or {}).get("hostname")
        or (unifi or {}).get("name")
        or ""
    )

    ip = (sophos or {}).get("ip") or (unifi or {}).get("ip") or ""

    vlan = None
    if unifi:
        vlan = unifi.get("vlan")
    if not vlan and sophos:
        vlan = sophos.get("vlan")

    is_wired = (unifi or {}).get("is_wired", None)
    online = unifi is not None  # Present in UniFi = currently online

    record: dict[str, Any] = {
        "mac": mac,
        "ip": ip,
        "hostname": hostname,
        "source": source,
        "online": online,
        "vlan": vlan,
        "is_wired": is_wired,
        # Sophos fields
        "sophos": {
            "ip": (sophos or {}).get("ip"),
            "mac": (sophos or {}).get("mac"),
            "hostname": (sophos or {}).get("hostname"),
            "lease_start": (sophos or {}).get("starts"),
            "lease_end": (sophos or {}).get("ends"),
            "lease_type": (sophos or {}).get("sophos_type"),
            "scope": (sophos or {}).get("scope"),
            "scope_name": (sophos or {}).get("scope_name"),
            "range_start": (sophos or {}).get("range_start"),
            "range_end": (sophos or {}).get("range_end"),
        } if sophos else None,
        # UniFi fields
        "unifi": {
            "mac": (unifi or {}).get("mac"),
            "ip": (unifi or {}).get("ip"),
            "hostname": (unifi or {}).get("hostname") or (unifi or {}).get("name"),
            "ap_mac": (unifi or {}).get("ap_mac"),
            "ap_name": (unifi or {}).get("ap_name"),
            "essid": (unifi or {}).get("essid"),
            "rssi": (unifi or {}).get("rssi"),
            "signal": (unifi or {}).get("signal"),
            "vlan": (unifi or {}).get("vlan"),
            "uptime": (unifi or {}).get("uptime"),
            "is_wired": (unifi or {}).get("is_wired"),
            "sw_mac": (unifi or {}).get("sw_mac"),
            "sw_port": (unifi or {}).get("sw_port"),
            "oui": (unifi or {}).get("oui"),
        } if unifi else None,
    }

    return record
