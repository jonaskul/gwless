#!/usr/bin/env bash
# Gwless — Proxmox VE LXC installer
# Run as root on a Proxmox VE host:
#   bash <(curl -fsSL https://raw.githubusercontent.com/jonaskul/gwless/main/install.sh)
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

GWLESS_REPO="https://github.com/jonaskul/gwless"
GWLESS_BRANCH="main"

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
info "Credentials are configured from the web UI after installation."
echo ""

# ── Find next CTID ────────────────────────────────────────────────────────────
CTID=$(pvesh get /cluster/nextid)
info "Using container ID: ${CTID}"

# ── Debian 13 template ────────────────────────────────────────────────────────
TEMPLATE_DIR="/var/lib/vz/template/cache"
TEMPLATE=$(ls "${TEMPLATE_DIR}"/debian-13-standard_*.tar.zst 2>/dev/null | sort -V | tail -1 || true)

if [[ -z "${TEMPLATE}" ]]; then
  info "Debian 13 template not found — downloading..."
  pveam update
  DEBIAN_PKG=$(pveam available --section system | grep 'debian-13-standard' | sort -V | tail -1 | awk '{print $2}')
  [[ -n "${DEBIAN_PKG}" ]] || die "Could not find debian-13-standard in pveam available."
  pveam download local "${DEBIAN_PKG}"
  TEMPLATE=$(ls "${TEMPLATE_DIR}"/debian-13-standard_*.tar.zst | sort -V | tail -1)
fi

info "Using template: ${TEMPLATE}"
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

# ── Wait for network ──────────────────────────────────────────────────────────
info "Waiting for container IP address..."
CONTAINER_IP=""
for ((i=1; i<=60; i++)); do
  CONTAINER_IP=$(pct exec "${CTID}" -- hostname -I 2>/dev/null | awk '{print $1}')
  if [[ -n "${CONTAINER_IP}" ]]; then
    info "Container IP: ${CONTAINER_IP}"
    break
  fi
  printf "."
  [[ $i -eq 60 ]] && echo "" && die "Container did not get an IP within 60s — check that vmbr0 has a DHCP server."
  sleep 1
done
[[ $i -gt 1 ]] && echo ""

info "Waiting for DNS..."
for ((i=1; i<=15; i++)); do
  if pct exec "${CTID}" -- getent hosts deb.debian.org &>/dev/null; then
    break
  fi
  printf "."
  [[ $i -eq 15 ]] && echo "" && die "Container DNS not working after 30s — check your bridge/DNS configuration."
  sleep 2
done
[[ $i -gt 1 ]] && echo ""

# ── Deploy application code ───────────────────────────────────────────────────
# When run via "bash <(curl ...)", BASH_SOURCE[0] resolves to the current
# working directory (not the repo), so we detect whether the source tree is
# actually present before deciding how to get the code into the container.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pct exec "${CTID}" -- mkdir -p /opt/gwless

if [[ -f "${SCRIPT_DIR}/backend/main.py" ]]; then
  # ── Local clone: tar and copy ────────────────────────────────────────────────
  info "Copying local application code into container..."
  TMPTAR="$(mktemp /tmp/gwless-XXXXXX.tar.gz)"
  tar -czf "${TMPTAR}" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='config.yaml' \
    -C "${SCRIPT_DIR}" .
  pct push "${CTID}" "${TMPTAR}" /tmp/gwless.tar.gz
  pct exec "${CTID}" -- bash -c \
    "tar -xzf /tmp/gwless.tar.gz -C /opt/gwless && rm /tmp/gwless.tar.gz" \
    || die "Failed to extract application code inside container."
  rm -f "${TMPTAR}"
else
  # ── Remote/curl install: download from GitHub ────────────────────────────────
  info "Downloading gwless from GitHub (branch: ${GWLESS_BRANCH})..."
  pct exec "${CTID}" -- bash -c "
    export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 &&
    apt-get update -qq &&
    apt-get install -y curl --no-install-recommends -qq &&
    curl -fsSL '${GWLESS_REPO}/archive/refs/heads/${GWLESS_BRANCH}.tar.gz' \
      | tar -xz --strip-components=1 -C /opt/gwless
  " || die "Failed to download gwless from GitHub — check network and that the branch '${GWLESS_BRANCH}' exists."
fi

# ── Install Python & dependencies ─────────────────────────────────────────────
info "Installing Python and dependencies (this may take a minute)..."
pct exec "${CTID}" -- bash -c "
  export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 &&
  apt-get update -qq &&
  apt-get install -y python3 python3-pip --no-install-recommends -qq &&
  pip3 install -r /opt/gwless/requirements.txt --break-system-packages --quiet
" || die "Dependency installation failed inside container."

# ── Write blank config.yaml ───────────────────────────────────────────────────
info "Writing default config.yaml..."
pct exec "${CTID}" -- bash -c "cp /opt/gwless/config.yaml.example /opt/gwless/config.yaml" \
  || die "config.yaml.example not found — cannot create default config."

# ── Install systemd service ───────────────────────────────────────────────────
info "Installing systemd service..."
pct exec "${CTID}" -- cp /opt/gwless/gwless.service /etc/systemd/system/gwless.service
pct exec "${CTID}" -- systemctl daemon-reload
pct exec "${CTID}" -- systemctl enable --now gwless

sleep 3

echo ""
ok "╔══════════════════════════════════════════════════════════╗"
ok "║   Gwless installed successfully!                         ║"
ok "╠══════════════════════════════════════════════════════════╣"
ok "║                                                          ║"
ok "║   Dashboard:  http://${CONTAINER_IP}:8080                 "
ok "║   CTID:       ${CTID}                                     "
ok "║                                                          ║"
ok "║   Open the dashboard and click the ⚙ Settings button     ║"
ok "║   to configure Sophos and UniFi credentials.             ║"
ok "║   Use the Test buttons to verify connectivity            ║"
ok "║   before saving.                                         ║"
ok "║                                                          ║"
ok "║   Manage:                                                ║"
ok "║   pct exec ${CTID} -- systemctl restart gwless           "
ok "║   pct exec ${CTID} -- journalctl -u gwless -f            "
ok "║                                                          ║"
ok "╚══════════════════════════════════════════════════════════╝"
echo ""
