# Gwless — DHCP & Network Client Dashboard

Single pane of glass for networks using UniFi switching/WiFi with a Sophos XGS gateway — a combination UniFi's own dashboards handle poorly.

Aggregates DHCP lease data from **Sophos SFOS 22** and client data from **UniFi Network v10** into one unified, real-time dashboard. Runs as a self-hosted LXC container on Proxmox VE.

---

## Features

- **Unified client table** — merges Sophos DHCP leases with UniFi client data via MAC address (IP fallback)
- **Source tagging** — each client is tagged as `both` (matched), `sophos_only`, or `unifi_only`
- **Live status** — green dot = active in UniFi, grey dot = lease-only
- **Slide-over detail panel** — full Sophos + UniFi data per client with one-click copy
- **VLAN / source / status filtering** + full-text search across hostname, IP, MAC, vendor
- **Sortable columns** — click any column header
- **Auto-refresh** every 30s with visible countdown
- **Stale data warnings** — amber/red banners when backend sources go stale
- **OUI vendor lookup** — local JSON database, auto-downloaded on first start
- **DHCP scope overview** — `/api/scopes` shows used/total per scope
- **Light/dark mode** — `◑` toggle in header, preference saved across sessions
- **Live connection log** — test buttons stream step-by-step output in real time
- **In-app update** — one-click update from GitHub with live progress and automatic reload
- **API secret** — optional header-based protection for mutating endpoints
- **Embedded syslog receiver** — optional UDP syslog listener; Sophos sends DHCP events directly, no SSH admin access required

---

## Architecture

```
Sophos XGS (SFOS 22)                UniFi Network v10
  SSH → /tmp/dhcpd.leases (optional)   Proxy API /proxy/network/…
  XML API → DHCPServer config          Cookie-based session auth
  UDP syslog → DHCP events (optional)
        │                                      │
        └──────────────┬───────────────────────┘
                       │
                  FastAPI backend
                  (merger + TTL cache + syslog receiver)
                       │
                  SQLite history.db
                  (devices + events)
                       │
                  Vanilla JS frontend
                  (single HTML file)
```

---

## Syslog DHCP Events

Gwless includes an optional embedded UDP syslog receiver. When enabled, Sophos SFOS sends DHCP events (Acknowledge, Release) as syslog datagrams and Gwless builds a live lease table from them — **no SSH admin access required**.

### Why use syslog instead of SSH?

| | SSH | Syslog |
|---|---|---|
| Sophos permission required | Admin with Advanced Shell access | Any admin account (Log Settings access) |
| Data latency | Poll interval (default 60 s) | Real-time (events arrive within seconds) |
| Setup complexity | SSH key/password + TOFU | Add syslog server IP in Sophos UI |

When syslog is enabled and has received at least one event, it takes priority over SSH for DHCP lease data. SSH remains as a fallback if no syslog events have been received yet (e.g. just after startup).

### Setup

#### 1. Enable in Gwless Settings

Open **⚙ Settings → Syslog DHCP Events**, tick **Enable syslog receiver**, set the port (default `514`), and save.

> **Port 514 note:** Linux requires root to bind to ports below 1024. The default LXC installer runs as root. If you run Gwless unprivileged, use a port > 1024 (e.g. `5140`) and configure Sophos to send to that port.

#### 2. Configure Sophos SFOS

1. Go to **Logging & Monitoring → Log Settings**
2. Under **Syslog Servers**, add a new entry:
   - **IP address**: the Gwless container's IP
   - **Port**: `514` (or your configured port)
   - **Protocol**: UDP
   - **Log format**: Default (BSD syslog)
3. Under **Log Type and Severity**, ensure **Event** logs are enabled
4. Confirm that the **DHCP** component is included in event logs
5. Click **Apply**

Lease events appear in the **Status** field in Settings within seconds of the next DHCP activity on the network. You can also trigger it immediately by releasing/renewing a lease on any device.

#### 3. Verify

The Settings status row will change from *"No events received yet"* to *"Receiving — N lease(s), last event Xs ago"* once the first DHCP Acknowledge syslog message arrives.

---

## Quick Install (Proxmox VE)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/jonaskul/gwless/main/install.sh)
```

The installer will:
1. Find the next available CTID
2. Download Debian 13 template if needed
3. Create an unprivileged LXC (512 MB RAM, 4 GB disk)
4. Install Python dependencies
5. Copy app code and write a blank `config.yaml`
6. Enable and start the `gwless` systemd service

**No credentials are needed during install.** Open the dashboard URL printed at the end, click **⚙ Settings**, enter your Sophos and UniFi credentials, and use the **Test** buttons to verify connectivity before saving.

---

## Manual Setup

```bash
git clone https://github.com/jonaskul/gwless.git
cd gwless
cp config.yaml.example config.yaml
# Edit config.yaml with your credentials
pip install -r requirements.txt
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

---

## Configuration

See [`config.yaml.example`](config.yaml.example) for all options.

Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `sophos.host` | — | Sophos XGS IP or hostname |
| `sophos.ssh_port` | `22` | SSH port |
| `sophos.api_port` | `4444` | WebAdmin API port |
| `unifi.host` | — | UniFi Network Application host |
| `unifi.site` | `default` | UniFi site name |
| `syslog.enabled` | `false` | Enable embedded UDP syslog receiver |
| `syslog.port` | `514` | UDP port to listen on |
| `syslog.bind_host` | `0.0.0.0` | Bind address |
| `app.oui_update_on_start` | `true` | Download OUI DB if missing |

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/clients` | All merged clients. Supports `?q=`, `?vlan=`, `?source=`, `?status=` |
| `GET /api/clients/{mac}` | Full detail for one client |
| `GET /api/scopes` | DHCP scopes with used/total lease counts |
| `GET /api/stats` | Summary counts and data freshness |
| `GET /api/syslog/status` | Syslog receiver status, lease count, last event timestamp |
| `GET /health` | Source health: `ok` / `stale` / `error` |

### History

| Endpoint | Description |
|----------|-------------|
| `GET /api/history/device/{mac}` | First/last seen + event log for a MAC address |
| `GET /api/history/events` | Recent events across all devices. Supports `?limit=` (max 500) |

### Configuration *(protected if `app.secret` is set)*

| Endpoint | Description |
|----------|-------------|
| `GET /api/config` | Current config (passwords masked) |
| `POST /api/config` | Save new config |
| `POST /api/test/sophos-ssh` | Test Sophos SSH connectivity |
| `POST /api/test/sophos-api` | Test Sophos XML API connectivity |
| `POST /api/test/unifi` | Test UniFi connectivity |
| `GET /api/refresh` | Invalidate all caches |

---

## Container Management

```bash
# Restart service
pct exec <CTID> -- systemctl restart gwless

# Follow logs
pct exec <CTID> -- journalctl -u gwless -f

# Edit config (or use the ⚙ Settings panel in the dashboard)
pct exec <CTID> -- nano /opt/gwless/config.yaml
pct exec <CTID> -- systemctl restart gwless
```

---

## Stack

- **Backend**: Python 3 / FastAPI / paramiko / requests / xmltodict
- **Frontend**: Single-file HTML — vanilla JS, no build step
- **Fonts**: Inter + JetBrains Mono (Google Fonts)
- **Container**: Debian 13 LXC on Proxmox VE

---

## Source Icons

| Icon | Meaning |
|------|---------|
| ✅ | Matched — data from both Sophos and UniFi |
| 📶 | UniFi wireless client |
| 🔌 | UniFi wired client |
| 🔥 | Sophos DHCP lease only — no UniFi data |
