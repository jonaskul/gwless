"""
API-level tests for behaviour documented in README.md.

Each test gets a fresh working directory and SQLite file, so nothing touches
a real config.yaml or history.db.
"""
import copy
import tempfile
from pathlib import Path

import pytest

import backend.history as history

# main.py opens the history DB at import time — point it somewhere harmless first.
history.DB_PATH = Path(tempfile.mkdtemp(prefix="gwless-test-")) / "history.db"
import backend.main as main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_BASE_CONFIG = copy.deepcopy(main.CONFIG)

SERVERS = [{"name": "WIFI", "vlan": 91, "gateway": "10.2.91.1", "subnet": "255.255.255.0",
            "range_start": "10.2.91.101", "range_end": "10.2.91.200"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml.example").write_text("")
    if history._db is not None:
        history._db.close()
    monkeypatch.setattr(history, "_db", None)
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "history.db")
    history.init_db()
    main.CONFIG.clear()
    main.CONFIG.update(copy.deepcopy(_BASE_CONFIG))
    main._sessions.clear()
    main._cache_leases.set([{"mac": "aa:aa:aa:aa:aa:01", "ip": "10.2.91.150", "hostname": "laptop"}])
    main._cache_sophos_cfg.set({"servers": SERVERS, "static_entries": []})
    main._cache_unifi.set({"clients": [{"mac": "aa:aa:aa:aa:aa:01", "ip": "10.2.91.150"}],
                           "ap_map": {}})
    yield TestClient(main.app)
    history._db.close()
    history._db = None


def test_renaming_a_device_keeps_dynamic_leases(client):
    """Regression: setting a name invalidated the lease cache."""
    assert client.patch("/api/device/aa:aa:aa:aa:aa:01/name", json={"name": "Office"}).status_code == 200
    [c] = client.get("/api/clients").json()["clients"]
    assert (c["custom_name"], c["source"]) == ("Office", "both")
    assert client.delete("/api/device/aa:aa:aa:aa:aa:01/name").status_code == 200
    [c] = client.get("/api/clients").json()["clients"]
    assert (c["custom_name"], c["source"]) == (None, "both")


def test_scopes_report_active_lease_count(client):
    [s] = client.get("/api/scopes").json()["scopes"]
    assert (s["leases_used"], s["leases_total"]) == (1, 100)


@pytest.mark.parametrize("body", [
    {"server_name": "WIFI", "mac": "zz", "ip": "10.2.91.23"},
    {"server_name": "WIFI", "mac": "aa:aa:aa:aa:aa:01", "ip": "nope"},
    {"server_name": "WIFI", "mac": "aa:aa:aa:aa:aa:01", "ip": "10.2.91.300"},
])
def test_reserve_rejects_malformed_input(client, body):
    assert client.post("/api/sophos/dhcp/reserve", json=body).status_code == 422


def test_unreserve_rejects_malformed_mac(client):
    assert client.post("/api/sophos/dhcp/unreserve",
                       json={"server_name": "WIFI", "mac": "bad"}).status_code == 422


def test_passwords_are_masked_and_preserved(client):
    cfg = client.get("/api/config").json()
    cfg["sophos"]["password"] = "hunter2"
    client.post("/api/config", json=cfg)
    masked = client.get("/api/config").json()
    assert masked["sophos"]["password"] == "••••••••"
    client.post("/api/config", json=masked)  # round-trip the sentinel
    assert main.CONFIG["sophos"]["password"] == "hunter2"


def test_api_secret_guards_writes_but_not_reads(client):
    main.CONFIG["app"]["secret"] = "s3cret"
    assert client.post("/api/config", json=client.get("/api/config").json()).status_code == 403
    assert client.get("/api/clients").status_code == 200
    ok = client.post("/api/config", json=client.get("/api/config").json(),
                     headers={"X-Gwless-Secret": "s3cret"})
    assert ok.status_code == 200


def test_login_flow(client):
    main.CONFIG["app"].update(auth_enabled=True, auth_username="admin", auth_password="pw")
    assert client.get("/api/clients").status_code == 401
    assert client.get("/").status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "no"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/api/clients").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/clients").status_code == 401


def test_backup_restore_round_trip(client):
    import io, zipfile
    cfg = client.get("/api/config").json()
    cfg["sophos"].update(host="10.0.0.1", password="hunter2")
    client.post("/api/config", json=cfg)
    full = client.get("/api/backup").content
    assert "hunter2" in zipfile.ZipFile(io.BytesIO(full)).read("config.yaml").decode()
    bare = client.get("/api/backup?skip_passwords=true").content
    assert "hunter2" not in zipfile.ZipFile(io.BytesIO(bare)).read("config.yaml").decode()
    main.CONFIG["sophos"]["host"] = "changed"
    r = client.post("/api/restore", files={"file": ("b.zip", full, "application/zip")})
    assert r.status_code == 200 and main.CONFIG["sophos"]["host"] == "10.0.0.1"
    assert client.post("/api/restore",
                       files={"file": ("x.zip", b"junk", "application/zip")}).status_code == 400


# ---------------------------------------------------------------------------
# History backup / restore (WAL-safe, validated)
# ---------------------------------------------------------------------------

def _zip(**files):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name.replace("_", "."), data)
    return buf.getvalue()


def _events(client):
    return len(client.get("/api/history/events").json()["events"])


def _see(client, macs):
    main._cache_leases.set([{"mac": m, "ip": f"10.0.0.{i}"} for i, m in enumerate(macs, 1)])
    main._cache_unifi.set({"clients": [], "ap_map": {}})  # only the leases above
    main._history_last_ts = 0
    client.get("/api/clients")


def _backup_db(client, tmp_path):
    import io, sqlite3, zipfile
    raw = zipfile.ZipFile(io.BytesIO(client.get("/api/backup").content)).read("history.db")
    path = tmp_path / "from-backup.db"
    path.write_bytes(raw)
    return sqlite3.connect(path)


def test_backup_includes_writes_still_in_the_wal(client, tmp_path):
    """Regression: a plain file copy of a WAL database left the tables out."""
    _see(client, ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"])
    db = _backup_db(client, tmp_path)
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == _events(client)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_restore_replaces_the_live_history(client):
    """Regression: restore reported ok but the open connection kept the old data."""
    _see(client, ["aa:aa:aa:aa:aa:01"])
    backup = client.get("/api/backup").content
    snapshot = _events(client)
    _see(client, [f"bb:bb:bb:bb:bb:0{i}" for i in range(1, 6)])
    assert _events(client) > snapshot

    r = client.post("/api/restore", files={"file": ("b.zip", backup, "application/zip")})
    assert r.status_code == 200
    assert _events(client) == snapshot
    _see(client, ["cc:cc:cc:cc:cc:01"])  # and the reopened DB keeps working
    assert _events(client) == snapshot + 1


@pytest.mark.parametrize("payload, message", [
    (_zip(history_db=b"not a database"), "not a valid SQLite database"),
    (_zip(config_yaml=b"sophos: [unclosed"), "not valid YAML"),
    (_zip(config_yaml=b""), "empty or not a mapping"),
], ids=["corrupt-db", "broken-yaml", "empty-yaml"])
def test_restore_rejects_corrupt_backups_and_keeps_running(client, payload, message):
    """Regression: a corrupt history.db was accepted and broke the app."""
    _see(client, ["aa:aa:aa:aa:aa:01"])
    before = _events(client)
    r = client.post("/api/restore", files={"file": ("x.zip", payload, "application/zip")})
    assert r.status_code == 400 and message in r.json()["detail"]
    assert _events(client) == before


def test_restore_rejects_a_database_without_gwless_tables(client, tmp_path):
    import sqlite3
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE unrelated (x)").connection.commit()
    r = client.post("/api/restore", files={"file": (
        "x.zip", _zip(history_db=other.read_bytes()), "application/zip")})
    assert r.status_code == 400 and "missing tables" in r.json()["detail"]


def test_bad_database_aborts_the_whole_restore(client):
    """Nothing is applied unless every file in the backup is usable."""
    main.CONFIG["sophos"]["host"] = "current"
    payload = _zip(config_yaml=b"sophos:\n  host: from-backup\n", history_db=b"junk")
    r = client.post("/api/restore", files={"file": ("x.zip", payload, "application/zip")})
    assert r.status_code == 400
    assert main.CONFIG["sophos"]["host"] == "current"


def test_restoring_an_older_schema_is_migrated(client, tmp_path):
    """Backups from before custom names existed must still restore and work."""
    import sqlite3
    old = tmp_path / "old.db"
    db = sqlite3.connect(old)
    db.executescript("""
        CREATE TABLE devices (mac TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL, last_ip TEXT, last_hostname TEXT, vendor TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
            mac TEXT NOT NULL, event TEXT NOT NULL, detail TEXT);
        INSERT INTO devices VALUES ('aa:aa:aa:aa:aa:01', 1, 1, '10.0.0.1', 'old', 'x');
    """)
    db.commit(); db.close()
    r = client.post("/api/restore", files={"file": (
        "x.zip", _zip(history_db=old.read_bytes()), "application/zip")})
    assert r.status_code == 200
    assert client.patch("/api/device/aa:aa:aa:aa:aa:01/name", json={"name": "Kept"}).status_code == 200
    [c] = client.get("/api/clients").json()["clients"]
    assert c["custom_name"] == "Kept"
