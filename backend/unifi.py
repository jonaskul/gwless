"""
UniFi Network Application v10 REST client.
Uses the new Proxy API (not legacy port-8443).
Authentication via cookie-based session (TOKEN cookie).
"""
from __future__ import annotations

import logging
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class UniFiClient:
    def __init__(self, config: dict):
        host = config.get("host", "").rstrip("/")
        if not host:
            raise ValueError("UniFi host is not configured. Open Settings to add credentials.")
        self.host = host
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.site = config.get("site", "default")
        self.verify_ssl = config.get("verify_ssl", False)
        self._session = requests.Session()
        self._logged_in = False

    def _login(self) -> None:
        url = f"{self.host}/api/auth/login"
        payload = {"username": self.username, "password": self.password}
        logger.debug("UniFi login: %s", url)
        resp = self._session.post(
            url,
            json=payload,
            verify=self.verify_ssl,
            timeout=15,
        )
        resp.raise_for_status()
        self._logged_in = True
        logger.debug("UniFi login successful")

    def _get(self, path: str) -> Any:
        if not self._logged_in:
            self._login()
        url = f"{self.host}{path}"
        resp = self._session.get(url, verify=self.verify_ssl, timeout=15)
        if resp.status_code == 401:
            # Re-authenticate and retry once
            self._logged_in = False
            self._login()
            resp = self._session.get(url, verify=self.verify_ssl, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # UniFi API wraps results in {"data": [...]}
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def fetch_clients(self) -> list[dict]:
        """Fetch all active wireless/wired clients from UniFi."""
        path = f"/proxy/network/api/s/{self.site}/stat/sta"
        raw = self._get(path)
        if not isinstance(raw, list):
            logger.warning("UniFi clients response is not a list: %s", type(raw))
            return []
        logger.info("UniFi: fetched %d clients", len(raw))
        return [_normalize_client(c) for c in raw]

    def fetch_devices(self) -> list[dict]:
        """Fetch all UniFi devices (APs, switches, etc.)."""
        path = f"/proxy/network/api/s/{self.site}/stat/device"
        raw = self._get(path)
        if not isinstance(raw, list):
            logger.warning("UniFi devices response is not a list: %s", type(raw))
            return []
        logger.info("UniFi: fetched %d devices", len(raw))
        return raw

    def fetch_ap_map(self) -> dict[str, str]:
        """Return dict mapping AP MAC address -> AP name."""
        devices = self.fetch_devices()
        ap_map = {}
        for dev in devices:
            mac = dev.get("mac", "").lower()
            name = dev.get("name") or dev.get("hostname") or mac
            if mac:
                ap_map[mac] = name
        return ap_map


def _normalize_client(raw: dict) -> dict:
    """Normalize a raw UniFi client dict to consistent field names."""
    return {
        "mac": raw.get("mac", "").lower(),
        "ip": raw.get("ip", ""),
        "hostname": raw.get("hostname") or raw.get("name") or "",
        "ap_mac": raw.get("ap_mac", "").lower() if raw.get("ap_mac") else None,
        "essid": raw.get("essid"),
        "vlan": raw.get("vlan"),
        "rssi": raw.get("rssi"),
        "signal": raw.get("signal"),
        "uptime": raw.get("uptime"),
        "oui": raw.get("oui"),
        "is_wired": raw.get("is_wired", False),
        "sw_mac": raw.get("sw_mac", "").lower() if raw.get("sw_mac") else None,
        "sw_port": raw.get("sw_port"),
        "name": raw.get("name") or raw.get("hostname") or "",
        # Pass through raw fields for detail view
        "_raw": raw,
    }
