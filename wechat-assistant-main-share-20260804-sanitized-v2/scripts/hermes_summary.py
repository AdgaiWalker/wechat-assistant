#!/usr/bin/env python3
"""Generate a compact WeChat Assistant summary for Hermes cron delivery."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(os.path.expanduser(
    os.environ.get(
        "WECHAT_ASSISTANT_SCRIPT_DIR",
        "~/.hermes/skills/social-media/wechat-assistant/scripts",
    )
)).resolve()
CONFIG = Path(os.path.expanduser(os.environ.get("WECHAT_ASSISTANT_CONFIG", "~/wechat-assistant/config.yaml")))
WORK_DIR = CONFIG.parent
HEALTH_GATE = Path(os.path.expanduser(
    os.environ.get("WECHAT_ASSISTANT_HEALTH_GATE", "~/.hermes/scripts/wechat_health_gate.py")
)).resolve()


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=SCRIPT_DIR, text=True, capture_output=True)
    return proc.returncode, proc.stdout, proc.stderr


def parse_wake_agent(output: str) -> Optional[dict]:
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "wakeAgent" in data:
            return data
    return None


def count_messages() -> tuple[int, int]:
    db = WORK_DIR / "collector.db"
    if not db.exists():
        return 0, 0
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT chatroom_id) FROM messages").fetchone()
        return int(row[0] or 0), int(row[1] or 0)
    finally:
        conn.close()


def main() -> int:
    if not CONFIG.exists():
        print(f"微信助手配置不存在: {CONFIG}")
        return 1

    if HEALTH_GATE.exists():
        gate_code, gate_out, gate_err = run([sys.executable, str(HEALTH_GATE)])
        gate_data = parse_wake_agent(gate_out)
        if gate_code != 0:
            print("⚠️ 微信助手健康检查失败")
            print((gate_err or gate_out).strip()[:1200])
            print(json.dumps({"wakeAgent": False, "status": "health_gate_failed"}, ensure_ascii=False))
            return 0
        if gate_data and gate_data.get("wakeAgent") is False:
            print(gate_out.strip())
            return 0

    refresh_code, refresh_out, refresh_err = run([
        sys.executable,
        str(SCRIPT_DIR / "refresh_decrypt.py"),
        "--config",
        str(CONFIG),
    ])
    if refresh_code != 0:
        print("⚠️ 微信助手刷新解密失败")
        print((refresh_err or refresh_out).strip()[:1200])
        print(json.dumps({"wakeAgent": False, "status": "refresh_failed"}, ensure_ascii=False))
        return 0

    sync_code, sync_out, sync_err = run([
        sys.executable,
        str(SCRIPT_DIR / "collector.py"),
        "--config",
        str(CONFIG),
        "--sync",
    ])
    if sync_code != 0:
        print("⚠️ 微信助手同步 collector.db 失败")
        print((sync_err or sync_out).strip()[:1200])
        print(json.dumps({"wakeAgent": False, "status": "sync_failed"}, ensure_ascii=False))
        return 0

    trending_code, trending_out, trending_err = run([
        sys.executable,
        str(SCRIPT_DIR / "extract_trending.py"),
        "--config",
        str(CONFIG),
    ])
    if trending_code != 0:
        print("⚠️ 微信热点提取失败")
        print((trending_err or trending_out).strip()[:1200])
        print(json.dumps({"wakeAgent": False, "status": "trending_failed"}, ensure_ascii=False))
        return 0

    data = json.loads(trending_out)
    messages, chats = count_messages()
    topics = data.get("cross_group_topics", [])[:5]
    urls = data.get("trending_urls", [])[:3]

    print(f"🔥 微信助手 Hermes 测试 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    print(f"- collector: {messages:,} 条消息 / {chats} 个会话")
    print(f"- 今日累计: {data.get('total_groups', 0)} 个活跃群 / {data.get('total_messages', 0)} 条群消息")
    print(f"- 跨群话题: {len(data.get('cross_group_topics', []))} 个候选")
    print()
    if topics:
        print("Top 话题候选:")
        for i, item in enumerate(topics, 1):
            keyword = item.get("keyword") or item.get("topic") or "-"
            groups = item.get("groups_count") or item.get("group_count") or "-"
            mentions = item.get("total_mentions") or item.get("count") or "-"
            print(f"{i}. {keyword} · {groups} 群 · {mentions} 次")
    else:
        print("暂无明显跨群热点。")
    if urls:
        print()
        print("热门链接:")
        for item in urls:
            url = item.get("url", "-")
            count = item.get("share_count", "-")
            print(f"- {url} · {count} 次")
    print()
    print("状态: Hermes cron script 执行成功，Feishu 投递由 Hermes gateway 处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
