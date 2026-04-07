#!/bin/bash
# Gwless in-place updater — run inside the LXC container
# Usage: bash /opt/gwless/update.sh
set -euo pipefail

GWLESS_REPO="https://github.com/jonaskul/gwless"
GWLESS_BRANCH="main"
GWLESS_DIR="/opt/gwless"
LOG_FILE="/tmp/gwless-update.log"

echo "[1/3] Downloading latest code from GitHub..."
curl -fsSL "${GWLESS_REPO}/archive/refs/heads/${GWLESS_BRANCH}.tar.gz" \
  | tar -xz --strip-components=1 \
    --exclude='*/config.yaml' \
    --exclude='*/oui.json' \
    --exclude='*/history.db' \
    -C "${GWLESS_DIR}"

# Verify the downloaded code parses correctly before restarting
python3 -c "import py_compile; py_compile.compile('${GWLESS_DIR}/backend/main.py', doraise=True)" \
  || { echo "ERROR: syntax error in backend/main.py — aborting restart"; exit 1; }

echo "[2/3] Updating Python dependencies..."
pip3 install -r "${GWLESS_DIR}/requirements.txt" \
  --break-system-packages --quiet \
  --no-warn-script-location --root-user-action=ignore

echo "[3/3] Restarting service..."
nohup bash -c '
  sleep 2
  systemctl restart gwless 2>&1
  for i in $(seq 1 20); do
    sleep 1
    if systemctl is-active --quiet gwless; then
      echo "[gwless] Service started OK" >> '"$LOG_FILE"'
      exit 0
    fi
  done
  # Service did not recover — log the error but do NOT reboot
  echo "[gwless] Service failed to start after update:" >> '"$LOG_FILE"'
  journalctl -u gwless -n 30 --no-pager >> '"$LOG_FILE"' 2>&1
  echo "[gwless] See '"$LOG_FILE"' for details" >> '"$LOG_FILE"'
' >> "$LOG_FILE" 2>&1 &

echo "Done. Service restarting in ~3 seconds."
echo "Log: $LOG_FILE"
