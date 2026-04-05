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
    -C "${GWLESS_DIR}"

echo "[2/3] Updating Python dependencies..."
pip3 install -r "${GWLESS_DIR}/requirements.txt" --break-system-packages --quiet

echo "[3/3] Scheduling service restart..."
nohup bash -c 'sleep 3 && systemctl restart gwless' > /dev/null 2>&1 &

echo "Done. Service restarting in ~3 seconds."
