#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG_PATH="${WECHAT_ASSISTANT_CONFIG:-$HOME/wechat-assistant/config.yaml}"
WORK_DIR="${WECHAT_ASSISTANT_WORKDIR:-$(dirname "$CONFIG_PATH")}"
OUT_DIR="${WECHAT_ASSISTANT_OUTDIR:-$WORK_DIR/out}"
LOG_DIR="${WECHAT_ASSISTANT_LOGDIR:-$WORK_DIR/logs}"
LOCK_DIR="${WECHAT_ASSISTANT_LOCKDIR:-$WORK_DIR/.run.lock}"
RECOVER_HOURS="${WECHAT_ASSISTANT_RECOVER_HOURS:-168}"

mkdir -p "$WORK_DIR" "$OUT_DIR" "$LOG_DIR"

usage() {
  cat <<USAGE
Usage: $0 <command>

Commands:
  sync         Refresh decrypted DBs and sync recent messages into collector.db
  reminders    Sync scan_state todos with macOS Reminders
  todos        Run sync, then extract todo candidates
  calendar     Run sync, then extract calendar candidates
  digest       Run sync, then extract digest data for configured groups
  trending     Run sync, then extract current trending topics
  tech         Run sync, then extract technical discussions
  preference   Run sync, then extract preference/writing samples
  insight      Collect digest files for insight analysis
  all          Run sync, then todos/calendar/trending/tech/preference

Environment:
  WECHAT_ASSISTANT_CONFIG   Default: $HOME/wechat-assistant/config.yaml
  WECHAT_ASSISTANT_WORKDIR  Default: config file directory
  WECHAT_ASSISTANT_OUTDIR   Default: \$WORK_DIR/out
  WECHAT_ASSISTANT_LOGDIR   Default: \$WORK_DIR/logs
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

COMMAND="$1"
shift || true

timestamp() {
  date '+%Y%m%d-%H%M%S'
}

run_json() {
  local name="$1"
  shift
  local ts
  ts="$(timestamp)"
  local out="$OUT_DIR/${name}-${ts}.json"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $name -> $out"
  python3 "$@" --config "$CONFIG_PATH" > "$out"
}

reminders_enabled() {
  python3 - "$CONFIG_PATH" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f) or {}
print('1' if cfg.get('reminders', {}).get('enabled') else '0')
PY
}

sync_reminders_if_enabled() {
  if [[ "$(reminders_enabled)" == "1" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] sync macOS Reminders"
    python3 "$SCRIPT_DIR/sync_reminders.py" --config "$CONFIG_PATH"
  fi
}

sync_messages() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] refresh decrypted databases"
  set +e
  python3 "$SCRIPT_DIR/refresh_decrypt.py" --config "$CONFIG_PATH"
  local refresh_code=$?
  set -e
  if [[ "$refresh_code" == "2" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WeChat DB is not ready; retrying full refresh with stored key"
    "$SCRIPT_DIR/recover_wechat_login.sh" --recent-hours "$RECOVER_HOURS"
    return
  elif [[ "$refresh_code" != "0" ]]; then
    exit "$refresh_code"
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] sync collector.db"
  python3 "$SCRIPT_DIR/collector.py" --config "$CONFIG_PATH" --sync
}

with_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another wechat-assistant run is active: $LOCK_DIR" >&2
    exit 75
  fi
  trap 'rmdir "$LOCK_DIR"' EXIT
}

with_lock

case "$COMMAND" in
  sync)
    sync_messages
    ;;
  reminders)
    python3 "$SCRIPT_DIR/sync_reminders.py" --config "$CONFIG_PATH"
    ;;
  todos)
    sync_reminders_if_enabled
    sync_messages
    sync_reminders_if_enabled
    run_json todos "$SCRIPT_DIR/extract_todos.py"
    ;;
  calendar)
    sync_messages
    run_json calendar "$SCRIPT_DIR/extract_calendar.py"
    ;;
  digest)
    sync_messages
    run_json digest "$SCRIPT_DIR/extract_digest.py"
    ;;
  trending)
    sync_messages
    run_json trending "$SCRIPT_DIR/extract_trending.py"
    ;;
  tech)
    sync_messages
    run_json tech "$SCRIPT_DIR/extract_tech.py"
    ;;
  preference)
    sync_messages
    run_json preference "$SCRIPT_DIR/extract_preferences.py"
    ;;
  insight)
    run_json insight "$SCRIPT_DIR/insight.py"
    ;;
  all)
    sync_reminders_if_enabled
    sync_messages
    sync_reminders_if_enabled
    run_json todos "$SCRIPT_DIR/extract_todos.py"
    run_json calendar "$SCRIPT_DIR/extract_calendar.py"
    run_json trending "$SCRIPT_DIR/extract_trending.py"
    run_json tech "$SCRIPT_DIR/extract_tech.py"
    run_json preference "$SCRIPT_DIR/extract_preferences.py"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage
    exit 2
    ;;
esac
