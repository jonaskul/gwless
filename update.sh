#!/bin/bash
# Gwless in-place updater — run inside the LXC container
# Usage: bash /opt/gwless/update.sh
set -euo pipefail

GWLESS_REPO="https://github.com/jonaskul/gwless"
GWLESS_BRANCH="main"
GWLESS_DIR="/opt/gwless"
CONFIG_FILE="${GWLESS_DIR}/config.yaml"
CONFIG_BACKUP="/tmp/gwless-config.yaml.bak"
LOG_FILE="/tmp/gwless-update.log"
FORCE="${1:-}"

# ── Version comparison ────────────────────────────────────────────────────────
ver_tuple() { echo "$1" | tr -d 'v' | awk -F. '{printf "%05d%05d%05d", $1, $2, $3}'; }

CURRENT_VERSION="$(cat "${GWLESS_DIR}/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "0.0.0")"
echo "[version] Installed: ${CURRENT_VERSION}"

echo "[version] Fetching remote version..."
REMOTE_VERSION="$(curl -fsSL "https://raw.githubusercontent.com/jonaskul/gwless/${GWLESS_BRANCH}/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "")"

if [ -z "$REMOTE_VERSION" ]; then
    echo "[version] WARNING: Could not fetch remote version — proceeding anyway"
elif [ "$(ver_tuple "$REMOTE_VERSION")" -le "$(ver_tuple "$CURRENT_VERSION")" ] && [ "$FORCE" != "--force" ]; then
    echo "[version] Already up to date (${CURRENT_VERSION}). Use --force to reinstall."
    exit 0
else
    echo "[version] Update available: ${CURRENT_VERSION} → ${REMOTE_VERSION}"
fi

# ── Backup config before touching anything ────────────────────────────────────
if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$CONFIG_BACKUP"
    echo "[pre-update] Config backed up to $CONFIG_BACKUP"
else
    echo "[pre-update] WARNING: $CONFIG_FILE does not exist before update"
fi

echo "[1/3] Downloading latest code from GitHub..."
curl -fsSL "${GWLESS_REPO}/archive/refs/heads/${GWLESS_BRANCH}.tar.gz" \
  | tar -xz --strip-components=1 \
    --exclude='*/config.yaml' \
    --exclude='*/oui.json' \
    --exclude='*/history.db' \
    -C "${GWLESS_DIR}"

# ── Restore config (safety net in case tar somehow overwrote it) ──────────────
if [ -f "$CONFIG_BACKUP" ]; then
    cp "$CONFIG_BACKUP" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    echo "[post-extract] Config restored from backup"
fi

# ── Verify syntax before restarting ──────────────────────────────────────────
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
  echo "[gwless] Service failed to start after update:" >> '"$LOG_FILE"'
  journalctl -u gwless -n 30 --no-pager >> '"$LOG_FILE"' 2>&1
' >> "$LOG_FILE" 2>&1 &

echo "Done. Service restarting in ~3 seconds."
echo "Log: $LOG_FILE"
