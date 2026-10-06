"""Tests for get_scopes_summary in backend/sophos.py."""
from backend.sophos import get_scopes_summary

# Sophos reports `subnet` as the netmask, not the network address.
WIFI = {"name": "WIFI", "gateway": "10.2.91.1", "subnet": "255.255.255.0",
        "range_start": "10.2.91.101", "range_end": "10.2.91.200"}


def _scope(servers, leases):
    return {s["name"]: s for s in get_scopes_summary(servers, leases)}


def test_counts_leases_inside_the_scope_network():
    """Regression: the netmask was used as the prefix, so this was always 0."""
    leases = [{"ip": "10.2.91.150"}, {"ip": "10.2.91.151"}, {"ip": "10.2.10.5"}]
    assert _scope([WIFI], leases)["WIFI"]["leases_used"] == 2


def test_total_is_the_size_of_the_dynamic_pool():
    assert _scope([WIFI], [])["WIFI"]["leases_total"] == 100


def test_pool_spanning_octets():
    srv = {**WIFI, "subnet": "255.255.254.0",
           "range_start": "10.2.90.200", "range_end": "10.2.91.10"}
    assert _scope([srv], [])["WIFI"]["leases_total"] == 67


def test_respects_a_non_24_netmask():
    srv = {**WIFI, "subnet": "255.255.255.128"}  # 10.2.91.0/25
    leases = [{"ip": "10.2.91.100"}, {"ip": "10.2.91.150"}]
    assert _scope([srv], leases)["WIFI"]["leases_used"] == 1


def test_falls_back_to_range_start_without_gateway():
    srv = {k: v for k, v in WIFI.items() if k != "gateway"}
    assert _scope([srv], [{"ip": "10.2.91.150"}])["WIFI"]["leases_used"] == 1


def test_falls_back_to_slash_24_without_a_usable_mask():
    srv = {**WIFI, "subnet": ""}
    assert _scope([srv], [{"ip": "10.2.91.150"}])["WIFI"]["leases_used"] == 1


def test_garbage_does_not_raise():
    srv = {"name": "X", "gateway": "nope", "subnet": "junk",
           "range_start": "a", "range_end": "b"}
    out = _scope([srv], [{"ip": "not-an-ip"}, {}])["X"]
    assert (out["leases_used"], out["leases_total"]) == (0, 0)
