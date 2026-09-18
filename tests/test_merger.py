"""
Tests for backend/merger.py — the Sophos/UniFi join logic.

merger.py is pure (no I/O, no network, no DB), so every behaviour here is
exercised directly with plain dicts.
"""
import pytest

from backend.merger import (
    merge_clients,
    normalize_ip,
    normalize_mac,
    _range_for_ip,
    _scope_for_ip,
    _vlan_for_ip,
)

# A representative DHCP server config as returned by the Sophos XML API.
SERVERS = [
    {
        "name": "WIFI",
        "vlan": 91,
        "gateway": "10.2.91.1",
        "subnet": "255.255.255.0",
        "range_start": "10.2.91.101",
        "range_end": "10.2.91.200",
    },
    {
        "name": "LAN",
        "vlan": 10,
        "gateway": "10.2.10.1",
        "subnet": "255.255.255.0",
        "range_start": "10.2.10.50",
        "range_end": "10.2.10.250",
    },
]


class TestNormalizeMac:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),   # already canonical
            ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),   # uppercase
            ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),   # hyphen separated
            ("aabb.ccdd.eeff",    "aa:bb:cc:dd:ee:ff"),   # Cisco dotted
            ("AABBCCDDEEFF",      "aa:bb:cc:dd:ee:ff"),   # no separators
            ("  AA:BB:CC:DD:EE:FF  ", "aa:bb:cc:dd:ee:ff"),  # surrounding space
        ],
    )
    def test_normalizes_to_lowercase_colon_form(self, raw, expected):
        assert normalize_mac(raw) == expected

    def test_empty_returns_empty(self):
        assert normalize_mac("") == ""

    @pytest.mark.parametrize("raw", ["not-a-mac", "aa:bb:cc", "xyz"])
    def test_unparseable_falls_back_to_lowercase_input(self, raw):
        """Anything that isn't 12 hex digits is passed through lowercased."""
        assert normalize_mac(raw) == raw.lower()

    def test_case_insensitive_join_across_sources(self):
        """A device is one device whether Sophos or UniFi shouts its MAC."""
        assert normalize_mac("AA:BB:CC:DD:EE:FF") == normalize_mac("aa-bb-cc-dd-ee-ff")


class TestNormalizeIp:
    def test_strips_surrounding_whitespace(self):
        assert normalize_ip("  10.0.0.1  ") == "10.0.0.1"

    @pytest.mark.parametrize("falsy", ["", None])
    def test_falsy_returns_empty_string(self, falsy):
        assert normalize_ip(falsy) == ""


class TestIpToServerLookups:
    """_vlan_for_ip / _scope_for_ip / _range_for_ip match on the /24 prefix."""

    def test_matches_via_gateway(self):
        assert _vlan_for_ip("10.2.91.55", SERVERS) == 91
        assert _scope_for_ip("10.2.91.55", SERVERS) == "WIFI"

    def test_picks_the_right_server_among_several(self):
        assert _scope_for_ip("10.2.10.77", SERVERS) == "LAN"
        assert _vlan_for_ip("10.2.10.77", SERVERS) == 10

    def test_matches_via_range_start_when_gateway_absent(self):
        """Regression: scope lookup must work off range_start alone."""
        servers = [{"name": "GUEST", "vlan": 5, "range_start": "192.168.5.100",
                    "range_end": "192.168.5.200"}]
        assert _scope_for_ip("192.168.5.42", servers) == "GUEST"
        assert _vlan_for_ip("192.168.5.42", servers) == 5

    def test_range_lookup_returns_start_and_end(self):
        assert _range_for_ip("10.2.91.55", SERVERS) == ("10.2.91.101", "10.2.91.200")

    @pytest.mark.parametrize("ip,servers", [("", SERVERS), ("10.2.91.5", []), ("", [])])
    def test_missing_input_yields_no_match(self, ip, servers):
        assert _vlan_for_ip(ip, servers) is None
        assert _scope_for_ip(ip, servers) is None
        assert _range_for_ip(ip, servers) == ("", "")

    def test_unknown_subnet_yields_no_match(self):
        assert _scope_for_ip("172.16.0.9", SERVERS) is None
        assert _range_for_ip("172.16.0.9", SERVERS) == ("", "")


class TestMergeSources:
    """source / online flags for each combination of inputs."""

    def test_empty_inputs_produce_no_records(self):
        assert merge_clients([], [], [], {}) == []

    def test_sophos_only_is_offline(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert rec["source"] == "sophos_only"
        assert rec["online"] is False
        assert rec["unifi"] is None
        assert rec["sophos"] is not None

    def test_unifi_only_is_online(self):
        [rec] = merge_clients(
            sophos_leases=[], sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            unifi_aps={},
        )
        assert rec["source"] == "unifi_only"
        assert rec["online"] is True
        assert rec["sophos"] is None

    def test_matched_on_mac_is_both(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "AA:BB:CC:DD:EE:FF", "ip": "10.2.91.150"}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa-bb-cc-dd-ee-ff", "ip": "10.2.91.150"}],
            unifi_aps={},
        )
        assert rec["source"] == "both"
        assert rec["online"] is True
        assert rec["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_records_are_keyed_by_normalized_mac(self):
        """Differently-formatted MACs for one device must not split into two rows."""
        out = merge_clients(
            sophos_leases=[{"mac": "AABBCCDDEEFF", "ip": "10.2.91.150"}],
            sophos_static=[],
            unifi_clients=[{"mac": "aabb.ccdd.eeff", "ip": "10.2.91.150"}],
            unifi_aps={},
        )
        assert len(out) == 1


class TestStaticReservations:
    def test_static_takes_priority_over_dynamic_lease(self):
        """A device with both a reservation and a live lease reports as static."""
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            sophos_static=[{"mac": "AA:BB:CC:DD:EE:FF", "ip": "10.2.91.23",
                            "hostname": "PRINTER"}],
            unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["sophos"]["lease_type"] == "static"
        assert rec["ip"] == "10.2.91.23"
        assert rec["hostname"] == "PRINTER"

    def test_dynamic_lease_is_tagged_dynamic(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert rec["sophos"]["lease_type"] == "dynamic"

    def test_static_gets_scope_and_range_from_server_config(self):
        """Regression: the DHCP modal needs scope_name + range on static entries."""
        [rec] = merge_clients(
            sophos_leases=[], sophos_static=[{"mac": "aa:bb:cc:dd:ee:ff",
                                              "ip": "10.2.91.23"}],
            unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["sophos"]["scope_name"] == "WIFI"
        assert rec["sophos"]["range_start"] == "10.2.91.101"
        assert rec["sophos"]["range_end"] == "10.2.91.200"

    def test_explicit_scope_name_is_not_overwritten(self):
        [rec] = merge_clients(
            sophos_leases=[], sophos_static=[{"mac": "aa:bb:cc:dd:ee:ff",
                                              "ip": "10.2.91.23",
                                              "scope_name": "FROM-API"}],
            unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["sophos"]["scope_name"] == "FROM-API"


class TestEnrichmentFromServerConfig:
    def test_dynamic_lease_gets_vlan_from_server_config(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            sophos_static=[], unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["vlan"] == 91

    def test_explicit_vlan_on_lease_wins_over_lookup(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150",
                            "vlan": 999}],
            sophos_static=[], unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["vlan"] == 999

    def test_static_reservation_gets_vlan_from_server_config(self):
        """A reservation with no UniFi match still reports the scope's VLAN."""
        [rec] = merge_clients(
            sophos_leases=[], sophos_static=[{"mac": "aa:bb:cc:dd:ee:ff",
                                              "ip": "10.2.91.23"}],
            unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["vlan"] == 91

    def test_explicit_vlan_on_static_wins_over_lookup(self):
        [rec] = merge_clients(
            sophos_leases=[], sophos_static=[{"mac": "aa:bb:cc:dd:ee:ff",
                                              "ip": "10.2.91.23", "vlan": 999}],
            unifi_clients=[], unifi_aps={}, sophos_servers=SERVERS,
        )
        assert rec["vlan"] == 999


class TestFieldPreference:
    def test_sophos_hostname_wins_over_unifi(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                            "hostname": "from-sophos"}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                            "hostname": "from-unifi"}],
            unifi_aps={},
        )
        assert rec["hostname"] == "from-sophos"

    def test_falls_back_to_unifi_name_when_no_hostname_anywhere(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                            "name": "unifi-alias"}],
            unifi_aps={},
        )
        assert rec["hostname"] == "unifi-alias"

    def test_hostname_defaults_to_empty_string(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert rec["hostname"] == ""

    def test_unifi_vlan_wins_over_sophos_vlan(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "vlan": 91}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "vlan": 42}],
            unifi_aps={},
        )
        assert rec["vlan"] == 42

    def test_sophos_vlan_used_when_unifi_vlan_absent(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1", "vlan": 91}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"}],
            unifi_aps={},
        )
        assert rec["vlan"] == 91


class TestAccessPointNames:
    def test_known_ap_mac_resolves_to_name(self):
        [rec] = merge_clients(
            [], [], [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                      "ap_mac": "CC:CC:CC:CC:CC:CC"}],
            {"cc:cc:cc:cc:cc:cc": "AP-Loft"},
        )
        assert rec["unifi"]["ap_name"] == "AP-Loft"

    def test_unknown_ap_mac_falls_back_to_the_mac(self):
        [rec] = merge_clients(
            [], [], [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                      "ap_mac": "dd:dd:dd:dd:dd:dd"}], {},
        )
        assert rec["unifi"]["ap_name"] == "dd:dd:dd:dd:dd:dd"

    def test_wired_client_has_no_ap_name(self):
        [rec] = merge_clients(
            [], [], [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                      "is_wired": True}], {},
        )
        assert rec["unifi"]["ap_name"] is None


class TestIpFallbackMatching:
    def test_unifi_client_without_mac_match_joins_sophos_by_ip(self):
        """UniFi rows are enriched from a Sophos lease sharing their IP."""
        out = merge_clients(
            sophos_leases=[{"mac": "", "ip": "10.2.91.150", "hostname": "leased"}],
            sophos_static=[],
            unifi_clients=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.2.91.150"}],
            unifi_aps={},
        )
        [rec] = out
        assert rec["source"] == "both"
        assert rec["hostname"] == "leased"

    @pytest.mark.xfail(
        reason="BUG: when Sophos and UniFi report the same IP under different "
               "MACs, the IP fallback fires in both directions and emits two "
               "records for one device, each claiming source='both'.",
        strict=True,
    )
    def test_same_ip_different_mac_does_not_duplicate_the_device(self):
        out = merge_clients(
            sophos_leases=[{"mac": "aa:aa:aa:aa:aa:aa", "ip": "10.2.91.150"}],
            sophos_static=[],
            unifi_clients=[{"mac": "bb:bb:bb:bb:bb:bb", "ip": "10.2.91.150"}],
            unifi_aps={},
        )
        assert len(out) == 1


class TestRecordShape:
    def test_every_record_exposes_the_contract_keys(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert set(rec) >= {"mac", "ip", "hostname", "source", "online",
                            "vlan", "is_wired", "sophos", "unifi"}

    def test_sophos_subdict_carries_lease_timestamps(self):
        [rec] = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1",
                            "starts": "2026/09/18 10:00:00",
                            "ends": "2026/09/19 10:00:00"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert rec["sophos"]["lease_start"] == "2026/09/18 10:00:00"
        assert rec["sophos"]["lease_end"] == "2026/09/19 10:00:00"

    def test_duplicate_macs_collapse_to_the_last_entry(self):
        out = merge_clients(
            sophos_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.1"},
                           {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.2"}],
            sophos_static=[], unifi_clients=[], unifi_aps={},
        )
        assert len(out) == 1
        assert out[0]["ip"] == "10.0.0.2"
