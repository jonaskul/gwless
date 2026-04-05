#!/usr/bin/env bash
# Gwless — Proxmox VE LXC installer
# Run as root on a Proxmox VE host:
#   bash <(curl -fsSL https://raw.githubusercontent.com/jonaskul/gwless/main/install.sh)
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[gwless]${NC} $*"; }
ok()    { echo -e "${GREEN}[gwless]${NC} $*"; }
warn()  { echo -e "${AMBER}[gwless]${NC} $*"; }
die()   { echo -e "${RED}[gwless] ERROR:${NC} $*" >&2; exit 1; }

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "This script must be run as root."
command -v pvesh &>/dev/null || die "pvesh not found — this script must run on a Proxmox VE host."

echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║   Gwless — Network Dashboard Installer    ║"
echo "  ╚═══════════════════════════════════════════╝"
echo ""

# ── Find next CTID ────────────────────────────────────────────────────────────
CTID=$(pvesh get /cluster/nextid)
info "Using container ID: ${CTID}"

# ── Debian 12 template ────────────────────────────────────────────────────────
TEMPLATE_DIR="/var/lib/vz/template/cache"
TEMPLATE=$(ls "${TEMPLATE_DIR}"/debian-12-standard_*.tar.zst 2>/dev/null | sort -V | tail -1 || true)

if [[ -z "${TEMPLATE}" ]]; then
  info "Debian 12 template not found — downloading..."
  pveam update
  DEBIAN_PKG=$(pveam available --section system | grep 'debian-12-standard' | sort -V | tail -1 | awk '{print $2}')
  [[ -n "${DEBIAN_PKG}" ]] || die "Could not find debian-12-standard in pveam available."
  pveam download local "${DEBIAN_PKG}"
  TEMPLATE=$(ls "${TEMPLATE_DIR}"/debian-12-standard_*.tar.zst | sort -V | tail -1)
fi

info "Using template: ${TEMPLATE}"

# ── Interactive config ────────────────────────────────────────────────────────
echo ""
echo "  Configure your network sources:"
echo "  ─────────────────────────────────────────────"
echo ""

read -r -p "  Sophos host/IP: " SOPHOS_HOST
[[ -n "${SOPHOS_HOST}" ]] || die "Sophos host is required."

read -r -p "  Sophos username [admin]: " SOPHOS_USER
SOPHOS_USER="${SOPHOS_USER:-admin}"

read -r -s -p "  Sophos SSH password: " SOPHOS_SSH_PASS; echo
[[ -n "${SOPHOS_SSH_PASS}" ]] || die "Sophos SSH password is required."

read -r -s -p "  Sophos WebAdmin API password (blank = same as SSH): " SOPHOS_API_PASS; echo
SOPHOS_API_PASS="${SOPHOS_API_PASS:-${SOPHOS_SSH_PASS}}"

echo ""
read -r -p "  UniFi host/IP: " UNIFI_HOST
[[ -n "${UNIFI_HOST}" ]] || die "UniFi host is required."

read -r -p "  UniFi username: " UNIFI_USER
[[ -n "${UNIFI_USER}" ]] || die "UniFi username is required."

read -r -s -p "  UniFi password: " UNIFI_PASS; echo
[[ -n "${UNIFI_PASS}" ]] || die "UniFi password is required."

read -r -p "  UniFi site [default]: " UNIFI_SITE
UNIFI_SITE="${UNIFI_SITE:-default}"

echo ""
info "Creating LXC container ${CTID}..."

# ── Create container ──────────────────────────────────────────────────────────
pct create "${CTID}" "${TEMPLATE}" \
  --hostname gwless \
  --memory 512 \
  --swap 256 \
  --cores 1 \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=0 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1

info "Starting container..."
pct start "${CTID}"
sleep 8

# ── Install Python & dependencies ─────────────────────────────────────────────
info "Installing Python and dependencies (this may take a minute)..."
pct exec "${CTID}" -- bash -c "
  apt-get update -qq &&
  apt-get install -y python3 python3-pip --no-install-recommends -qq &&
  pip3 install fastapi 'uvicorn[standard]' paramiko requests pyyaml xmltodict aiofiles \
    --break-system-packages --quiet
"

# ── Copy application code ─────────────────────────────────────────────────────
info "Copying application code into container..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create app directory
pct exec "${CTID}" -- mkdir -p /opt/gwless

# Pack and push the codebase
TMPTAR="$(mktemp /tmp/gwless-XXXXXX.tar.gz)"
tar -czf "${TMPTAR}" \
  --exclude='.git' \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  --exclude='*.egg-info' \
  -C "${SCRIPT_DIR}" .

pct push "${CTID}" "${TMPTAR}" /tmp/gwless.tar.gz
pct exec "${CTID}" -- bash -c "tar -xzf /tmp/gwless.tar.gz -C /opt/gwless && rm /tmp/gwless.tar.gz"
rm -f "${TMPTAR}"

# ── Write config.yaml ─────────────────────────────────────────────────────────
info "Writing config.yaml..."
pct exec "${CTID}" -- bash -c "cat > /opt/gwless/config.yaml" <<CONFEOF
sophos:
  host: ${SOPHOS_HOST}
  ssh_port: 22
  api_port: 4444
  username: ${SOPHOS_USER}
  password: "${SOPHOS_SSH_PASS}"
  api_password: "${SOPHOS_API_PASS}"
  verify_ssl: false
  poll_interval_leases: 60
  poll_interval_config: 300

unifi:
  host: ${UNIFI_HOST}
  username: ${UNIFI_USER}
  password: "${UNIFI_PASS}"
  site: ${UNIFI_SITE}
  verify_ssl: false
  poll_interval: 30

app:
  port: 8080
  log_level: info
  oui_update_on_start: true
CONFEOF

# ── Install systemd service ───────────────────────────────────────────────────
info "Installing systemd service..."
pct push "${CTID}" "${SCRIPT_DIR}/gwless.service" /etc/systemd/system/gwless.service
pct exec "${CTID}" -- systemctl daemon-reload
pct exec "${CTID}" -- systemctl enable --now gwless

sleep 3

# ── Get container IP ──────────────────────────────────────────────────────────
CONTAINER_IP=$(pct exec "${CTID}" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")

echo ""
ok "╔══════════════════════════════════════════════════╗"
ok "║   Gwless installed successfully!                 ║"
ok "╠══════════════════════════════════════════════════╣"
ok "║                                                  ║"
ok "║   Dashboard:  http://${CONTAINER_IP}:8080         "
ok "║   CTID:       ${CTID}                             "
ok "║                                                  ║"
ok "║   Manage:                                        ║"
ok "║   pct exec ${CTID} -- systemctl restart gwless   "
ok "║   pct exec ${CTID} -- journalctl -u gwless -f    "
ok "║                                                  ║"
ok "╚══════════════════════════════════════════════════╝"
echo ""
