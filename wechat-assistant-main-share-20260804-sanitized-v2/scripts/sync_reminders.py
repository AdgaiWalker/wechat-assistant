#!/usr/bin/env python3
"""
sync_reminders.py — Sync scan_state.json todos with macOS Reminders.

The sync is intentionally state-file first:
- completed Reminders mark matching local todos as done
- open local todos are created/updated in a dedicated Reminders list
- done local todos mark matching Reminders as completed
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

_TZ8 = timezone(timedelta(hours=8))
_OPEN_TODO_STATUSES = {'open', 'new', 'seen', 'due_soon', 'overdue'}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from state_manager import StateManager


def parse_args():
    parser = argparse.ArgumentParser(description='同步待办到 macOS 提醒事项')
    parser.add_argument('--config', required=True, help='YAML 配置文件路径')
    parser.add_argument('--state', help='scan_state.json 路径（默认从 config 同级目录推导）')
    parser.add_argument('--list', dest='list_name', help='提醒事项列表名称（默认读取配置或 WeChat Assistant）')
    parser.add_argument('--dry-run', action='store_true', help='只打印将要执行的变更，不写入 Reminders/state')
    return parser.parse_args()


def load_config(config_path):
    sys.path.insert(0, os.path.join(SCRIPT_DIR, 'decrypt'))
    from config import load_config as _load
    return _load(config_path)


def get_state_path(args):
    if args.state:
        return os.path.abspath(os.path.expanduser(args.state))
    return os.path.join(os.path.dirname(os.path.abspath(args.config)), 'scan_state.json')


def as_applescript_string(value):
    text = '' if value is None else str(value)
    text = text.replace('\\', '\\\\').replace('"', '\\"')
    text = text.replace('\r', ' ').replace('\n', ' ')
    return f'"{text}"'


def run_osascript(script):
    proc = subprocess.run(
        ['osascript', '-e', script],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'osascript failed')
    return proc.stdout.strip()


def reminder_title(item):
    contact = item.get('contact') or ''
    summary = item.get('summary') or item.get('content') or ''
    title = f'{contact} — {summary}' if contact else summary
    return title[:180] or item.get('id') or '微信待办'


def reminder_body(item):
    lines = [
        'Source: wechat-assistant',
        f"Todo ID: {item.get('id', '')}",
    ]
    if item.get('fingerprint'):
        lines.append(f"Fingerprint: {item.get('fingerprint')}")
    if item.get('latest_evidence'):
        lines.append(f"Evidence: {item.get('latest_evidence')}")
    elif item.get('context'):
        lines.append(f"Context: {item.get('context')}")
    return '\n'.join(lines)


def get_reminder_status(list_name, reminder_id):
    script = f'''
tell application "Reminders"
  if not (exists list {as_applescript_string(list_name)}) then return "missing"
  set targetList to list {as_applescript_string(list_name)}
  set matchedReminders to reminders of targetList whose id is {as_applescript_string(reminder_id)}
  if (count of matchedReminders) is 0 then return "missing"
  set targetReminder to item 1 of matchedReminders
  if completed of targetReminder then
    return "completed"
  else
    return "open"
  end if
end tell
'''
    return run_osascript(script)


def create_reminder(list_name, item):
    script = f'''
tell application "Reminders"
  if not (exists list {as_applescript_string(list_name)}) then
    make new list with properties {{name:{as_applescript_string(list_name)}}}
  end if
  set targetList to list {as_applescript_string(list_name)}
  set newReminder to make new reminder at end of reminders of targetList with properties {{name:{as_applescript_string(reminder_title(item))}, body:{as_applescript_string(reminder_body(item))}}}
  return id of newReminder
end tell
'''
    return run_osascript(script)


def update_reminder(list_name, reminder_id, item, completed=None):
    completed_line = ''
    if completed is True:
        completed_line = '  set completed of targetReminder to true\n'
    elif completed is False:
        completed_line = '  set completed of targetReminder to false\n'

    script = f'''
tell application "Reminders"
  if not (exists list {as_applescript_string(list_name)}) then return "missing"
  set targetList to list {as_applescript_string(list_name)}
  set matchedReminders to reminders of targetList whose id is {as_applescript_string(reminder_id)}
  if (count of matchedReminders) is 0 then return "missing"
  set targetReminder to item 1 of matchedReminders
  set name of targetReminder to {as_applescript_string(reminder_title(item))}
  set body of targetReminder to {as_applescript_string(reminder_body(item))}
{completed_line}  return "updated"
end tell
'''
    return run_osascript(script)


def mark_item_done(item, now):
    if item.get('status') == 'done':
        return False
    item['previous_status'] = item.get('status')
    item['status'] = 'done'
    item['acknowledged'] = True
    item['resolved'] = now.isoformat()
    item['resolved_ts'] = int(now.timestamp())
    item['resolved_date'] = now.date().isoformat()
    item['resolved_source'] = 'mac_reminders'
    return True


def main():
    args = parse_args()
    cfg = load_config(args.config)
    state_path = get_state_path(args)
    list_name = args.list_name or cfg.get('mac_reminders_list') or 'WeChat Assistant'

    sm = StateManager(state_path)
    state = sm.get_todos()
    items = state.get('items', [])
    now = datetime.now(tz=_TZ8)
    stats = {
        'created': 0,
        'updated': 0,
        'completed_from_reminders': 0,
        'completed_in_reminders': 0,
        'missing_recreated': 0,
        'errors': [],
    }

    changed = False
    for item in items:
        todo_id = item.get('id')
        if not todo_id:
            continue

        status = item.get('status')
        reminder_id = item.get('mac_reminder_id')

        try:
            reminder_status = get_reminder_status(list_name, reminder_id) if reminder_id else 'missing'
        except RuntimeError as exc:
            stats['errors'].append({'id': todo_id, 'error': str(exc)})
            continue

        if reminder_status == 'completed' and status in _OPEN_TODO_STATUSES:
            if args.dry_run:
                stats['completed_from_reminders'] += 1
                continue
            if mark_item_done(item, now):
                changed = True
                stats['completed_from_reminders'] += 1
            continue

        if status in _OPEN_TODO_STATUSES:
            if args.dry_run:
                if reminder_status == 'missing':
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                continue

            if reminder_status == 'missing':
                new_id = create_reminder(list_name, item)
                item['mac_reminder_id'] = new_id
                item['mac_reminder_list'] = list_name
                item['mac_reminder_synced_at'] = now.isoformat()
                changed = True
                stats['created'] += 1
                if reminder_id:
                    stats['missing_recreated'] += 1
            else:
                result = update_reminder(list_name, reminder_id, item)
                if result == 'updated':
                    item['mac_reminder_synced_at'] = now.isoformat()
                    changed = True
                    stats['updated'] += 1
            continue

        if status == 'done' and reminder_id and reminder_status == 'open':
            if args.dry_run:
                stats['completed_in_reminders'] += 1
                continue
            result = update_reminder(list_name, reminder_id, item, completed=True)
            if result == 'updated':
                item['mac_reminder_synced_at'] = now.isoformat()
                changed = True
                stats['completed_in_reminders'] += 1

    if changed and not args.dry_run:
        sm.update_todos(items)

    print(json.dumps({
        'state_path': state_path,
        'reminders_list': list_name,
        'dry_run': args.dry_run,
        **stats,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
