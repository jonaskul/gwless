# Gwless — Network Client Dashboard

> Single pane of glass for networks running **Sophos XGS (SFOS 22)** + **UniFi Network v10** — a combination UniFi's own dashboards handle poorly.

Merges DHCP lease data from Sophos and client data from UniFi into one unified, real-time table. Runs as a lightweight LXC container on Proxmox VE.

---

## Requirements

- **Proxmox VE** host (tested on PVE 8)
- **Sophos XGS** firewall running **SFOS 22** — WebAdmin API access required (port 4444)
- **UniFi Network Application** v8+ — local or cloud controller

The LXC container needs outbound internet access on first start to download Python packages and the OUI vendor database.

---

## Features

**Dashboard**
- Unified client table — merges Sophos DHCP leases with UniFi data via MAC (IP fallback)
- Source tagging: `both`, `sophos_only`, `unifi_only`
- Live status — green = active in UniFi, grey = lease-only
- Detail popover at mouse cursor — full Sophos + UniFi data, one-click copy, and history
- Sortable columns, full-text search (hostname / IP / MAC / vendor)
- Multi-select filters: VLAN, source, connection type, lease type — with one-click clear
- Auto-refresh every 30 s with visible countdown
- Stale-data warnings when backend sources go silent
- Light/dark mode, 12/24 h clock, EU/US date format

**Data sources**
- Sophos XML API — DHCP server config, static reservations, VLAN IDs
- Sophos SSH — `/tmp/dhcpd.leases` (optional, requires full-admin)
- Sophos UDP syslog — real-time DHCP events, no SSH required, SQLite-persisted
- UniFi Proxy API — live client list, WLAN/port, signal, uptime

**History & events**
- SQLite-backed device history — first/last seen, IP/hostname changes
- Global event feed via `⏱` in the header

**DHCP Management**
- Create and remove static DHCP reservations directly from the device popover
- Expandable form pre-filled with current IP and hostname — editable before submitting
- Real-time validation: warns if the entered IP is inside the dynamic range or already reserved
- Sophos XML API is updated live; dashboard refreshes automatically

**Management**
- In-app update — check, preview changelog, one-click apply with live output
- In-app OS update (`apt upgrade`) streamed live
- Backup & Restore — download/upload a ZIP of `config.yaml` + `history.db`, with password include/exclude
- OUI vendor lookup — local database, auto-downloaded on first start (IEEE source, manual update button in settings)
- Optional login page — username/password auth with 24 h session, enabled from Settings
- Version number displayed in header

---

## Quick Install (Proxmox VE)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jonaskul/gwless/main/install.sh)
```

The installer prompts for optional customisation (CT ID, RAM, disk, bridge), then:

1. Downloads the Debian 13 template if needed
2. Creates an unprivileged LXC and configures it
3. Installs Python dependencies and the `gwless` systemd service
4. Sets a random root password and enables SSH
5. Prints the dashboard URL, SSH address, and root password

Open the dashboard URL, click **⚙ Settings**, enter your UniFi and Sophos credentials, and use the **Test** buttons before saving. No credentials are required during install.

---

## Updating

### In-app (recommended)

**⚙ Settings → Update → Check for update** — fetches the latest version and changelog, then lets you apply with one click. `config.yaml`, `oui.json`, and `history.db` are preserved.

### Manual

```bash
bash /opt/gwless/update.sh
```

---

## Syslog DHCP Events

An optional embedded UDP syslog receiver lets Sophos push DHCP events in real time — no SSH admin access required.

| | SSH | Syslog |
|---|---|---|
| Permission required | Full admin + Advanced Shell | Any admin (Log Settings access) |
| Latency | Poll interval (60 s default) | Real-time (seconds) |
| Survives restarts | Re-polled on startup | SQLite-backed |

When syslog has received at least one event it takes priority over SSH. SSH remains as fallback until then.

### Setup

**1. Enable in Gwless** — ⚙ Settings → Sophos → Syslog DHCP Events → tick *Enable*, set port (default `514`), save.

> Port `514` requires root. The default LXC installer runs as root. For unprivileged setups use port > 1024 (e.g. `5140`).

**2. Configure Sophos SFOS** — Logging & Monitoring → Log Settings → Syslog Servers:
- IP: Gwless container IP
- Port: `514` (or your port)
- Protocol: UDP
- Log format: Default (BSD syslog)

Ensure **Event** logs are enabled and the **DHCP** component is included. Click Apply.

**3. Verify** — the Settings status row turns green (*"Receiving — N lease(s), last event Xs ago"*) on the first DHCP event. You can trigger one immediately by releasing/renewing a lease on any device.

---

## Security

### Login Page

An optional username/password login can be enabled to protect the entire dashboard.

1. Open **⚙ Settings → App → Login**
2. Tick **Enable login page**, set a username and password, click **Save**
3. All `/api/*` endpoints are immediately protected — the browser will show the login screen on next load

Sessions are stored in memory (cleared on service restart) and expire after 24 hours of inactivity. The session cookie is `HttpOnly` and `SameSite=Strict`.

> The API Secret (below) and the login page are independent. The login page controls who can view the dashboard; the API Secret controls who can change settings.

### API Secret

Mutating endpoints can be protected with a shared secret.

1. Set `app.secret: your-secret` in `config.yaml` and restart
2. Enter the same value in **⚙ Settings → App → API Secret** — it is stored in `sessionStorage` and sent as an `X-Gwless-Secret` header

Read-only endpoints (`GET /api/clients`, masked `GET /api/config`) remain open.

### SSH Host Key Pinning

On first SSH connection the host key fingerprint is saved to `config.yaml` under `sophos.ssh_host_key` and verified on every subsequent connection. Clear this field to re-trust after a firmware reinstall.

---

## Configuration

All settings can be managed from the **⚙ Settings** panel. Direct file reference:

| Key | Default | Description |
|-----|---------|-------------|
| `sophos.host` | — | Sophos XGS IP or hostname |
| `sophos.api_port` | `4444` | WebAdmin API port |
| `sophos.ssh_enabled` | `false` | Enable SSH access (requires full admin) |
| `sophos.ssh_port` | `22` | SSH port |
| `sophos.ssh_host_key` | — | SSH host key (auto-populated on first connect) |
| `unifi.host` | — | UniFi Network Application host |
| `unifi.port` | `443` | UniFi HTTPS port |
| `unifi.site` | `default` | UniFi site name |
| `syslog.enabled` | `false` | Enable embedded UDP syslog receiver |
| `syslog.port` | `514` | UDP port |
| `syslog.bind_host` | `0.0.0.0` | Bind address |
| `app.oui_update_on_start` | `true` | Download OUI database if missing |
| `app.secret` | — | Optional API secret (protects mutating endpoints) |
| `app.auth_enabled` | `false` | Enable login page |
| `app.auth_username` | — | Login username |
| `app.auth_password` | — | Login password |

---

## API Reference

### Data

| Endpoint | Description |
|----------|-------------|
| `GET /api/clients` | All merged clients — supports `?q=`, `?vlan=`, `?source=`, `?status=` |
| `GET /api/clients/{mac}` | Full detail for one client |
| `GET /api/scopes` | DHCP scopes with used/total lease counts |
| `GET /api/stats` | Summary counts and data freshness |
| `GET /api/syslog/status` | Syslog receiver status and recent log |
| `GET /api/version` | Installed version |
| `GET /health` | Source health: `ok` / `stale` / `error` |

### History

| Endpoint | Description |
|----------|-------------|
| `GET /api/history/device/{mac}` | First/last seen + event log for a MAC |
| `GET /api/history/events` | Recent events across all devices (`?limit=`, max 500) |

### Update

| Endpoint | Description |
|----------|-------------|
| `GET /api/update/info` | Installed version + local changelog |
| `GET /api/update/check` | Compare with GitHub + remote changelog |
| `POST /api/update/apply` | Run `update.sh`, stream output (SSE) |
| `POST /api/update/os` | Run `apt upgrade`, stream output (SSE) |

### Auth *(only present when `app.auth_enabled` is true)*

| Endpoint | Description |
|----------|-------------|
| `GET /api/auth/status` | Returns `{auth_enabled, authenticated}` |
| `POST /api/auth/login` | Login with `{username, password}` — sets session cookie |
| `POST /api/auth/logout` | Clears session |

### Configuration *(protected if `app.secret` is set)*

| Endpoint | Description |
|----------|-------------|
| `GET /api/config` | Current config (passwords masked) |
| `POST /api/config` | Save config |
| `GET /api/refresh` | Invalidate all caches |
| `GET /api/backup` | Download backup ZIP (`config.yaml` + `history.db`) |
| `POST /api/restore` | Restore from backup ZIP |
| `POST /api/oui/update` | Re-download OUI vendor database |
| `POST /api/restart` | Restart the gwless service |
| `POST /api/sophos/dhcp/reserve` | Create a static DHCP reservation on Sophos |
| `POST /api/sophos/dhcp/unreserve` | Remove a static DHCP reservation from Sophos |
| `POST /api/test/sophos-ssh` | Test Sophos SSH |
| `POST /api/test/sophos-api` | Test Sophos XML API |
| `POST /api/test/unifi` | Test UniFi connectivity |
| `POST /api/test/sophos-ssh/stream` | Test Sophos SSH — live log (SSE) |
| `POST /api/test/sophos-api/stream` | Test Sophos XML API — live log (SSE) |
| `POST /api/test/unifi/stream` | Test UniFi — live log (SSE) |

---

## Persistent Data

Stored in `/opt/gwless/` — never overwritten by updates:

| File | Purpose |
|------|---------|
| `config.yaml` | All credentials and settings |
| `oui.json` | OUI vendor database (~5 MB) |
| `history.db` | Device history, events, syslog DHCP leases |

---

## Container Management

```bash
# Restart service
pct exec <CTID> -- systemctl restart gwless

# Follow logs
pct exec <CTID> -- journalctl -u gwless -f

# Edit config directly (or use ⚙ Settings in the dashboard)
pct exec <CTID> -- nano /opt/gwless/config.yaml

# Inspect history database
pct exec <CTID> -- sqlite3 /opt/gwless/history.db \
  "SELECT * FROM events ORDER BY ts DESC LIMIT 20;"
```

---

## Manual Setup

```bash
git clone https://github.com/jonaskul/gwless.git
cd gwless
cp config.yaml.example config.yaml   # or configure via ⚙ Settings after starting
pip install -r requirements.txt
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 · FastAPI · paramiko · requests · xmltodict · SQLite |
| Frontend | Single-file HTML — vanilla JS, no build step |
| Fonts | Inter + JetBrains Mono |
| Container | Debian 13 LXC on Proxmox VE |

---

## Source Icons

| Icon | Meaning |
|------|---------|
| ✅ | Matched — data from both Sophos and UniFi |
| 📶 | UniFi wireless client |
| 🔌 | UniFi wired client |
| 🔥 | Sophos DHCP lease only — no UniFi data |

---

*Built with [Claude Code](https://claude.ai/code)*
