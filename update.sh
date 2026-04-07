#!/bin/bash
# Gwless in-place updater — run inside the LXC container
# Usage: bash /opt/gwless/update.sh
set -euo pipefail

GWLESS_REPO="https://github.com/jonaskul/gwless"
GWLESS_BRANCH="main"
GWLESS_DIR="/opt/gwless"

echo "[1/3] Downloading latest code from GitHub..."
curl -fsSL "${GWLESS_REPO}/archive/refs/heads/${GWLESS_BRANCH}.tar.gz" \
  | tar -xz --strip-components=1 \
    --exclude='*/config.yaml' \
    --exclude='*/oui.json' \
    --exclude='*/history.db' \
    -C "${GWLESS_DIR}"

echo "[2/3] Updating Python dependencies..."
pip3 install -r "${GWLESS_DIR}/requirements.txt" \
  --break-system-packages --quiet \
  --no-warn-script-location --root-user-action=ignore

echo "[3/3] Restarting service..."
nohup bash -c '
  sleep 2
  systemctl restart gwless
  for i in $(seq 1 15); do
    sleep 1
    if systemctl is-active --quiet gwless; then
      exit 0
    fi
  done
  # Service did not recover after 15s — reboot as fallback
  systemctl reboot
' > /dev/null 2>&1 &

echo "Done. Service restarting in ~3 seconds."
