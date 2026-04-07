#!/usr/bin/env bash
# Gwless — Proxmox VE LXC installer
# Run as root on a Proxmox VE host:
#   bash <(curl -fsSL https://raw.githubusercontent.com/jonaskul/gwless/main/install.sh)
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; AMBER='\033[0;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

GWLESS_REPO="https://github.com/jonaskul/gwless"
GWLESS_BRANCH="main"

info()  { echo -e "${CYAN}  ·${NC} $*"; }
ok()    { echo -e "${GREEN}  ✓${NC} $*"; }
warn()  { echo -e "${AMBER}  ⚠${NC} $*"; }
die()   { echo -e "${RED}  ✗ ERROR:${NC} $*" >&2; exit 1; }
ask()   { echo -en "${CYAN}  ?${NC} $1 "; }

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "This script must be run as root."
command -v pvesh &>/dev/null || die "pvesh not found — this script must run on a Proxmox VE host."

echo ""
echo -e "${BOLD}  ╔═══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}  ║   Gwless — Network Dashboard Installer    ║${NC}"
echo -e "${BOLD}  ╚═══════════════════════════════════════════╝${NC}"
echo ""

# ── Default settings ──────────────────────────────────────────────────────────
CT_ID=$(pvesh get /cluster/nextid)
CT_HOSTNAME="gwless"
CT_RAM=512
CT_SWAP=256
CT_DISK=4
CT_CORES=1
CT_BRIDGE="vmbr0"
CT_STORAGE="local-lvm"

ask "Use default settings? [Y/n]:"
read -r REPLY
REPLY="${REPLY:-Y}"

if [[ "${REPLY,,}" == "n" ]]; then
  echo ""
  ask "Container ID [${CT_ID}]:"
  read -r v; [[ -n "${v}" ]] && CT_ID="${v}"

  ask "Hostname [${CT_HOSTNAME}]:"
  read -r v; [[ -n "${v}" ]] && CT_HOSTNAME="${v}"

  ask "RAM in MB [${CT_RAM}]:"
  read -r v; [[ -n "${v}" ]] && CT_RAM="${v}"

  ask "Disk size in GB [${CT_DISK}]:"
  read -r v; [[ -n "${v}" ]] && CT_DISK="${v}"

  ask "CPU cores [${CT_CORES}]:"
  read -r v; [[ -n "${v}" ]] && CT_CORES="${v}"

  ask "Network bridge [${CT_BRIDGE}]:"
  read -r v; [[ -n "${v}" ]] && CT_BRIDGE="${v}"

  ask "Storage pool [${CT_STORAGE}]:"
  read -r v; [[ -n "${v}" ]] && CT_STORAGE="${v}"
fi

echo ""
info "Container ID:  ${CT_ID}"
info "Hostname:      ${CT_HOSTNAME}"
info "RAM / Swap:    ${CT_RAM} MB / ${CT_SWAP} MB"
info "Disk:          ${CT_DISK} GB on ${CT_STORAGE}"
info "Cores:         ${CT_CORES}"
info "Bridge:        ${CT_BRIDGE}"
echo ""

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

info "Template: ${TEMPLATE##*/}"
info "Creating LXC container ${CT_ID}..."

# ── Create container ──────────────────────────────────────────────────────────
pct create "${CT_ID}" "${TEMPLATE}" \
  --hostname "${CT_HOSTNAME}" \
  --memory "${CT_RAM}" \
  --swap "${CT_SWAP}" \
  --cores "${CT_CORES}" \
  --rootfs "${CT_STORAGE}:${CT_DISK}" \
  --net0 name=eth0,bridge="${CT_BRIDGE}",ip=dhcp,firewall=0 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1

info "Starting container..."
pct start "${CT_ID}"

# ── Wait for network ──────────────────────────────────────────────────────────
info "Waiting for IP address..."
CONTAINER_IP=""
for ((i=1; i<=60; i++)); do
  CONTAINER_IP=$(pct exec "${CT_ID}" -- hostname -I 2>/dev/null | awk '{print $1}')
  if [[ -n "${CONTAINER_IP}" ]]; then
    break
  fi
  printf "."
  [[ $i -eq 60 ]] && echo "" && die "Container did not get an IP within 60s — check that ${CT_BRIDGE} has a DHCP server."
  sleep 1
done
[[ $i -gt 1 ]] && echo ""
ok "Container IP: ${CONTAINER_IP}"

info "Waiting for DNS..."
for ((i=1; i<=15; i++)); do
  if pct exec "${CT_ID}" -- getent hosts deb.debian.org &>/dev/null; then
    break
  fi
  printf "."
  [[ $i -eq 15 ]] && echo "" && die "Container DNS not working — check your bridge/DNS configuration."
  sleep 2
done
[[ $i -gt 1 ]] && echo ""

# ── Deploy application code ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pct exec "${CT_ID}" -- mkdir -p /opt/gwless

if [[ -f "${SCRIPT_DIR}/backend/main.py" ]]; then
  info "Copying local application code into container..."
  TMPTAR="$(mktemp /tmp/gwless-XXXXXX.tar.gz)"
  tar -czf "${TMPTAR}" \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='config.yaml' \
    -C "${SCRIPT_DIR}" .
  pct push "${CT_ID}" "${TMPTAR}" /tmp/gwless.tar.gz
  pct exec "${CT_ID}" -- bash -c \
    "tar -xzf /tmp/gwless.tar.gz -C /opt/gwless && rm /tmp/gwless.tar.gz" \
    || die "Failed to extract application code inside container."
  rm -f "${TMPTAR}"
else
  info "Downloading Gwless from GitHub (branch: ${GWLESS_BRANCH})..."
  pct exec "${CT_ID}" -- bash -c "
    export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 &&
    apt-get update -qq &&
    apt-get install -y curl --no-install-recommends -qq &&
    curl -fsSL '${GWLESS_REPO}/archive/refs/heads/${GWLESS_BRANCH}.tar.gz' \
      | tar -xz --strip-components=1 -C /opt/gwless
  " || die "Failed to download Gwless from GitHub."
fi

# ── Install Python, dependencies & openssh-server ─────────────────────────────
info "Installing dependencies (this may take a minute)..."
pct exec "${CT_ID}" -- bash -c "
  export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 &&
  apt-get update -qq &&
  apt-get install -y python3 python3-pip openssh-server --no-install-recommends -qq &&
  pip3 install -r /opt/gwless/requirements.txt \
    --break-system-packages --quiet \
    --no-warn-script-location --root-user-action=ignore
" || die "Dependency installation failed inside container."

# ── Set root password and enable SSH ─────────────────────────────────────────
ROOT_PASS=$(tr -dc 'A-Za-z0-9@#%' < /dev/urandom | head -c 16)
pct exec "${CT_ID}" -- bash -c "
  echo 'root:${ROOT_PASS}' | chpasswd &&
  sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config &&
  systemctl enable --now ssh
"
ok "Root password set and SSH enabled."

# ── Write default config ──────────────────────────────────────────────────────
info "Writing default config.yaml..."
pct exec "${CT_ID}" -- bash -c \
  "cp /opt/gwless/config.yaml.example /opt/gwless/config.yaml && chmod 600 /opt/gwless/config.yaml" \
  || die "config.yaml.example not found."

# ── Install systemd service ───────────────────────────────────────────────────
info "Installing systemd service..."
pct exec "${CT_ID}" -- cp /opt/gwless/gwless.service /etc/systemd/system/gwless.service
pct exec "${CT_ID}" -- systemctl daemon-reload
pct exec "${CT_ID}" -- systemctl enable --now gwless

# ── Configure console auto-login ──────────────────────────────────────────────
info "Configuring console auto-login..."
pct exec "${CT_ID}" -- bash -c '
  mkdir -p /etc/systemd/system/container-getty@1.service.d
  cat > /etc/systemd/system/container-getty@1.service.d/override.conf << '"'"'EOF'"'"'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 $TERM
EOF
  systemctl daemon-reload
'

sleep 3

# ── Pad IP line for banner alignment ─────────────────────────────────────────
_pad() { printf "%-54s" "$1"; }

echo ""
echo -e "${GREEN}  ╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║${BOLD}   Gwless installed successfully!                         ${GREEN}║${NC}"
echo -e "${GREEN}  ╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}  ║                                                          ║${NC}"
echo -e "${GREEN}  ║${NC}   Dashboard : ${BOLD}http://${CONTAINER_IP}:8080${NC}$(printf '%*s' $((38 - ${#CONTAINER_IP})) '')${GREEN}║${NC}"
echo -e "${GREEN}  ║${NC}   SSH       : ${BOLD}ssh root@${CONTAINER_IP}${NC}$(printf '%*s' $((42 - ${#CONTAINER_IP})) '')${GREEN}║${NC}"
echo -e "${GREEN}  ║${NC}   Password  : ${BOLD}${ROOT_PASS}${NC}$(printf '%*s' $((56 - ${#ROOT_PASS})) '')${GREEN}║${NC}"
echo -e "${GREEN}  ║                                                          ║${NC}"
echo -e "${GREEN}  ║${NC}   Open Settings in the dashboard to configure          ${GREEN}║${NC}"
echo -e "${GREEN}  ║${NC}   Sophos and UniFi credentials.                        ${GREEN}║${NC}"
echo -e "${GREEN}  ║                                                          ║${NC}"
echo -e "${GREEN}  ╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
warn "Save the password above — it will not be shown again."
echo ""
