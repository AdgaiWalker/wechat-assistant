#!/usr/bin/env python3
"""
extract_digest.py — 从 collector.db 提取群聊消息，输出 JSON（不调 AI）

用法：
  python3 extract_digest.py --config config.yaml                                 # 默认：昨天
  python3 extract_digest.py --config config.yaml --date yesterday
  python3 extract_digest.py --config config.yaml --date 2026-03-12
  python3 extract_digest.py --config config.yaml --date today                    # 今天（用于测试）

输出 JSON 到 stdout:
{
  "date": "2026-03-12",
  "already_done": false,
  "groups": [
    {"id": "...", "name": "...", "total": 100, "filtered": 80, "messages": [...]}
  ],
  "scan_state_path": "..."
}
"""
import sqlite3
import json
import os
import sys
import argparse
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

_TZ8 = timezone(timedelta(hours=8))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from state_manager import StateManager


_ENGAGEMENT_STRONG_PHRASES = (
    '帮忙点赞', '帮我点赞', '求点赞', '点个赞', '点点赞', '点赞收藏',
    '点赞评论', '点赞转发', '点赞收藏评论', '一键三连', '提高热度',
    '冲热度', '蹭热度', '数据加热', '做数据', '刷互动', '暖帖',
    '养号', '互赞', '互粉', '互评', '回赞', '回评',
)

_ENGAGEMENT_RELAY_TERMS = (
    '点赞', '点个赞', '点点赞', '赞一下', '收藏', '评论', '转发',
    '互赞', '互粉', '互评', '加热', '热度', '做数据', '冲榜',
    '自媒体', '小红书', '公众号', '视频号', '抖音', '快手', '按格式',
)


def _is_engagement_farming(content):
    """过滤自媒体互赞收藏、接龙冲热度等低价值加热消息。"""
    text = re.sub(r'\s+', '', content or '').lower()
    if not text:
        return False

    if any(phrase in text for phrase in _ENGAGEMENT_STRONG_PHRASES):
        return True

    if '接龙' in text and any(term in text for term in _ENGAGEMENT_RELAY_TERMS):
        return True

    engagement_hits = sum(1 for term in _ENGAGEMENT_RELAY_TERMS if term in text)
    if engagement_hits >= 3:
        return True

    if re.search(r'(帮忙|帮我|麻烦|求|互相|大家|顺手).{0,8}(点赞|点个赞|点点赞|收藏|评论|转发)', text):
        return True
    if re.search(r'(点赞|收藏|评论|转发).{0,8}(一下|互相|回赞|回评|提高|热度|加热)', text):
        return True

    return False


def _dedupe_urls(content):
    urls = []
    for url in re.findall(r'https?://[^\s<>"\']+', content or ''):
        url = url.rstrip(').,;:，。；：）')
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            path = parsed.path.rstrip('/')
            if host in ('mp.weixin.qq.com', 'weixin.sogou.com'):
                biz = parse_qs(parsed.query).get('__biz', [''])[0]
                mid = parse_qs(parsed.query).get('mid', [''])[0]
                if biz:
                    urls.append(f'{host}?__biz={biz}&mid={mid}')
                else:
                    urls.append(f'{host}{path}')
            else:
                urls.append(f'{host}{path}'[:120])
        except Exception:
            if url:
                urls.append(url.lower())
    return tuple(sorted(set(urls)))


def _message_dedupe_key(sender, content):
    if not sender:
        return None

    urls = _dedupe_urls(content)
    if urls:
        return sender, 'url', urls

    text = re.sub(r'<[^>]+>', ' ', content or '')
    text = re.sub(r'\s+', '', text).lower()
    if len(text) < 30:
        return None
    return sender, 'text', text[:300]


def parse_args():
    parser = argparse.ArgumentParser(description='从 collector.db 提取群聊消息')
    parser.add_argument('--config', required=True, help='YAML 配置文件路径')
    parser.add_argument('--groups', help='群 ID 列表，逗号分隔（默认用 config 中的 monitor.groups）')
    parser.add_argument('--date', default='yesterday', help='日期: yesterday, today 或 YYYY-MM-DD')
    parser.add_argument('--state', help='scan_state.json 路径（默认从 config 推导）')
    return parser.parse_args()


def load_config(config_path):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypt'))
    from config import load_config as _load
    return _load(config_path)


def get_state_path(cfg, args):
    if args.state:
        return args.state
    config_dir = os.path.dirname(os.path.abspath(args.config))
    return os.path.join(config_dir, 'scan_state.json')


def get_group_name(conn, group_id, names_cache):
    """获取群名"""
    if group_id in names_cache:
        return names_cache[group_id]
    try:
        row = conn.execute(
            "SELECT chatroom_name FROM watched_chats WHERE chatroom_id = ?",
            (group_id,)
        ).fetchone()
        name = row[0] if row and row[0] else group_id
    except sqlite3.OperationalError:
        name = group_id
    names_cache[group_id] = name
    return name


def main():
    args = parse_args()
    cfg = load_config(args.config)

    collector_db = cfg['collector_db']
    state_path = get_state_path(cfg, args)
    sm = StateManager(state_path)

    # 确定群列表
    if args.groups:
        group_ids = [g.strip() for g in args.groups.split(',') if g.strip()]
    else:
        group_ids = cfg.get('monitor_groups', [])

    if not group_ids:
        print(json.dumps({'error': '未指定群 ID，请用 --groups 参数或在 config.yaml 的 monitor.groups 配置'}))
        sys.exit(1)

    # 确定日期范围
    now = datetime.now(tz=_TZ8)
    if args.date == 'yesterday':
        d = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    elif args.date == 'today':
        d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            d = datetime.strptime(args.date, '%Y-%m-%d').replace(tzinfo=_TZ8)
        except ValueError:
            print(json.dumps({'error': f'日期格式错误: {args.date}，请用 YYYY-MM-DD'}))
            sys.exit(1)

    ts_start = int(d.timestamp())
    ts_end = ts_start + 86400
    date_label = d.strftime('%Y-%m-%d')

    # 检查是否已处理过该日期
    digest_state = sm.get_digest_state()
    already_done = digest_state.get('daily_done', '') == date_label

    if already_done:
        output = {
            'date': date_label,
            'already_done': True,
            'message': f'{date_label} 的干货已收集过，跳过重复处理',
            'scan_state_path': state_path,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    conn = sqlite3.connect(collector_db)
    conn.text_factory = lambda b: b.decode('utf-8', 'replace')

    names_cache = {}
    result_groups = []
    seen_sender_messages = set()

    for gid in group_ids:
        rows = conn.execute(
            """SELECT sender, content, msg_time FROM messages
               WHERE chatroom_id=? AND msg_time >= ? AND msg_time < ?
               AND msg_type NOT IN (3, 47)
               ORDER BY msg_time""",
            (gid, ts_start, ts_end)
        ).fetchall()

        filtered = []
        for sender, content, ts in rows:
            if not content or len(content) < 5:
                continue
            if sender == '__self__':
                continue
            if content.startswith('[img:') or content.startswith('[🖼️'):
                continue
            if content.startswith('<?xml') or content.startswith('<msg'):
                continue
            if content.startswith('[📎 消息类型'):
                continue
            if _is_engagement_farming(content):
                continue
            # 纯表情过滤
            import unicodedata
            stripped = content.replace(' ', '')
            non_emoji = [c for c in stripped if c not in '[]' and not c.isspace()
                         and unicodedata.category(c) not in ('So', 'Sk', 'Cn')
                         and not (0x1F000 <= ord(c) <= 0x1FFFF)
                         and not (0x2600 <= ord(c) <= 0x27BF)
                         and not (0xFE00 <= ord(c) <= 0xFE0F)
                         and not (0x200D == ord(c))]
            if stripped and not non_emoji:
                continue
            dedupe_key = _message_dedupe_key(sender, content)
            if dedupe_key:
                if dedupe_key in seen_sender_messages:
                    continue
                seen_sender_messages.add(dedupe_key)
            filtered.append({
                'sender': sender,
                'content': content[:500],
                'time': datetime.fromtimestamp(ts, _TZ8).strftime('%H:%M')
            })

        # 消息过多时只保留有实质内容的
        if len(filtered) > 300:
            filtered = [m for m in filtered if len(m['content']) > 20][:300]

        name = get_group_name(conn, gid, names_cache)
        result_groups.append({
            'id': gid,
            'name': name,
            'total': len(rows),
            'filtered': len(filtered),
            'messages': filtered,
        })

    conn.close()

    output = {
        'date': date_label,
        'already_done': False,
        'groups': result_groups,
        'scan_state_path': state_path,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
