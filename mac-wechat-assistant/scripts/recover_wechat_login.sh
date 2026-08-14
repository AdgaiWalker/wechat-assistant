#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${WECHAT_ASSISTANT_CONFIG:-$HOME/wechat-assistant/config.yaml}"
WORK_DIR="$(cd "$(dirname "$CONFIG_PATH")" && pwd)"
KEYS_FILE="$WORK_DIR/all_keys.json"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<USAGE
Usage: $0 [--recent-hours HOURS]

Run this after logging back into desktop WeChat on this Mac.

It reuses the stored WeChat DB keys, refreshes decrypted databases, and syncs
recent messages into collector.db.

Environment:
  WECHAT_ASSISTANT_CONFIG   Default: ~/wechat-assistant/config.yaml
USAGE
  exit 0
fi

RECENT_HOURS="${WECHAT_ASSISTANT_RECOVER_HOURS:-168}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recent-hours)
      RECENT_HOURS="${2:?--recent-hours requires a value}"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "config not found: $CONFIG_PATH" >&2
  exit 1
fi

if [[ ! -f "$KEYS_FILE" ]]; then
  echo "stored key file not found: $KEYS_FILE" >&2
  exit 1
fi

echo "[recover] Make sure desktop WeChat is logged in on this Mac."
echo "[recover] Reusing stored WeChat DB keys: $KEYS_FILE"
cd "$WORK_DIR"

echo "[recover] Full decrypt with stored keys..."
python3 "$SCRIPT_DIR/refresh_decrypt.py" --config "$CONFIG_PATH" --full

echo "[recover] Sync recent messages into collector.db..."
python3 "$SCRIPT_DIR/collector.py" --config "$CONFIG_PATH" --sync --recent-hours "$RECENT_HOURS"

echo "[recover] OK. Hermes cron can resume on its next scheduled run."
