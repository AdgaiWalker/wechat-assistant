#!/usr/bin/env python3
"""Local web dashboard for wechat-assistant runtime data."""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _latest_outputs(out_dir: Path) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for raw in glob.glob(str(out_dir / "*.json")):
        path = Path(raw)
        kind = path.name.split("-", 1)[0]
        if kind not in latest or path.stat().st_mtime > latest[kind].stat().st_mtime:
            latest[kind] = path
    return latest


def _query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


def _runtime_summary(work_dir: Path) -> dict:
    db_path = work_dir / "collector.db"
    out_dir = work_dir / "out"
    latest = _latest_outputs(out_dir)
    outputs = {kind: _read_json(str(path)) for kind, path in latest.items()}

    counts = {"messages": 0, "chats": 0, "groups": 0, "dms": 0}
    rows = _query(
        db_path,
        """
        SELECT
          COUNT(*) AS messages,
          COUNT(DISTINCT chatroom_id) AS chats,
          COUNT(DISTINCT CASE WHEN chatroom_id LIKE '%@chatroom' THEN chatroom_id END) AS groups,
          COUNT(DISTINCT CASE WHEN chatroom_id NOT LIKE '%@chatroom' THEN chatroom_id END) AS dms
        FROM messages
        """,
    )
    if rows:
        counts = dict(rows[0])

    top_groups = _query(
        db_path,
        """
        SELECT m.chatroom_id, COALESCE(w.chatroom_name, m.chatroom_id) AS name, COUNT(*) AS count
        FROM messages m
        LEFT JOIN watched_chats w ON w.chatroom_id = m.chatroom_id
        WHERE m.chatroom_id LIKE '%@chatroom'
        GROUP BY m.chatroom_id, name
        ORDER BY count DESC
        LIMIT 12
        """,
    )

    latest_times = _query(
        db_path,
        """
        SELECT
          datetime(MAX(msg_time), 'unixepoch', 'localtime') AS latest_message,
          datetime(MIN(msg_time), 'unixepoch', 'localtime') AS first_message
        FROM messages
        """,
    )

    return {
        "counts": counts,
        "top_groups": [dict(r) for r in top_groups],
        "latest_times": dict(latest_times[0]) if latest_times else {},
        "latest_files": {k: v.name for k, v in latest.items()},
        "outputs": outputs,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _card(title: str, value: str, detail: str = "") -> str:
    return (
        f'<section class="metric"><div class="metric-title">{html.escape(title)}</div>'
        f'<div class="metric-value">{html.escape(value)}</div>'
        f'<div class="metric-detail">{html.escape(detail)}</div></section>'
    )


def _render_dashboard(summary: dict) -> bytes:
    counts = summary["counts"]
    outputs = summary["outputs"]
    trending = outputs.get("trending", {})
    tech = outputs.get("tech", {})
    todos = outputs.get("todos", {})
    calendar = outputs.get("calendar", {})
    preference = outputs.get("preference", {})

    top_topics = trending.get("cross_group_topics", [])[:8]
    tech_categories = tech.get("categories", {})
    top_groups = summary["top_groups"]
    latest_files = summary["latest_files"]
    latest_times = summary["latest_times"]

    def topic_name(item: dict) -> str:
        return str(item.get("keyword") or item.get("topic") or item.get("title") or "-")

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wechat Assistant Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #697586;
      --line: #d9dee7;
      --blue: #2563eb;
      --green: #0f766e;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      padding: 20px 28px 16px;
    }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; }}
    .sub {{ margin-top: 6px; color: var(--muted); font-size: 14px; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric-title {{ color: var(--muted); font-size: 13px; }}
    .metric-value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
    .metric-detail {{ color: var(--muted); font-size: 12px; margin-top: 3px; min-height: 16px; }}
    .columns {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 12px; margin-top: 12px; }}
    h2 {{ font-size: 16px; margin: 0 0 10px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td, th {{ padding: 8px 6px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .pill {{ display: inline-block; padding: 2px 7px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); }}
    .files {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .file {{ padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; background: #fbfcfe; font-size: 12px; }}
    @media (max-width: 860px) {{
      main {{ padding: 14px; }}
      .grid, .columns {{ grid-template-columns: 1fr; }}
      header {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Wechat Assistant Dashboard</h1>
    <div class="sub">本地运行状态 · {html.escape(summary["generated_at"])} · 数据目录 ~/wechat-assistant</div>
  </header>
  <main>
    <div class="grid">
      {_card("消息总数", f"{counts.get('messages', 0):,}", f"会话 {counts.get('chats', 0)}")}
      {_card("群聊", f"{counts.get('groups', 0)}", "collector.db")}
      {_card("私聊", f"{counts.get('dms', 0)}", "collector.db")}
      {_card("今日热点", f"{len(trending.get('cross_group_topics', []))}", f"{trending.get('total_groups', 0)} 群 / {trending.get('total_messages', 0)} 条")}
    </div>

    <div class="columns">
      <section class="panel">
        <h2>提取摘要</h2>
        <table>
          <tr><th>模块</th><th>结果</th><th>文件</th></tr>
          <tr><td>待办</td><td>{todos.get('conversations_count', 0)} 个会话</td><td>{html.escape(latest_files.get('todos', '-'))}</td></tr>
          <tr><td>日程</td><td>{calendar.get('conversations_count', 0)} 个会话 / {calendar.get('total_messages', 0)} 条消息</td><td>{html.escape(latest_files.get('calendar', '-'))}</td></tr>
          <tr><td>技术</td><td>{tech.get('total_tech_mentions', 0)} 条技术提及 / {len(tech_categories)} 类</td><td>{html.escape(latest_files.get('tech', '-'))}</td></tr>
          <tr><td>偏好</td><td>{len(preference.get('preferences', []))} 条偏好 / {len(preference.get('writing_samples', []))} 条写作样本</td><td>{html.escape(latest_files.get('preference', '-'))}</td></tr>
        </table>
      </section>

      <section class="panel">
        <h2>时间范围</h2>
        <table>
          <tr><td>最早消息</td><td>{html.escape(str(latest_times.get('first_message') or '-'))}</td></tr>
          <tr><td>最新消息</td><td>{html.escape(str(latest_times.get('latest_message') or '-'))}</td></tr>
          <tr><td>最新输出</td><td><div class="files">{''.join(f'<span class="file">{html.escape(name)}</span>' for name in latest_files.values())}</div></td></tr>
        </table>
      </section>
    </div>

    <div class="columns">
      <section class="panel">
        <h2>活跃群聊 Top 12</h2>
        <table>
          <tr><th>群</th><th>消息数</th></tr>
          {''.join(f"<tr><td>{html.escape(g['name'])}<br><span class='pill'>{html.escape(g['chatroom_id'])}</span></td><td>{g['count']:,}</td></tr>" for g in top_groups)}
        </table>
      </section>

      <section class="panel">
        <h2>今日热点关键词</h2>
        <table>
          <tr><th>关键词</th><th>群数</th><th>次数</th></tr>
          {''.join(f"<tr><td>{html.escape(topic_name(t))}</td><td>{html.escape(str(t.get('groups_count', t.get('group_count', '-'))))}</td><td>{html.escape(str(t.get('count', t.get('total_count', '-'))))}</td></tr>" for t in top_topics)}
        </table>
      </section>
    </div>
  </main>
</body>
</html>"""
    return html_doc.encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    work_dir: Path = Path.home() / "wechat-assistant"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"ok": True})
            return
        if path == "/data.json":
            self._send_json(_runtime_summary(self.work_dir))
            return
        if path != "/":
            self.send_error(404)
            return
        body = _render_dashboard(_runtime_summary(self.work_dir))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {fmt % args}")

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wechat Assistant local web dashboard")
    parser.add_argument("--work-dir", default="~/wechat-assistant")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    DashboardHandler.work_dir = Path(os.path.expanduser(args.work_dir)).resolve()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Serving dashboard at http://{args.host}:{args.port}")
    print(f"Work dir: {DashboardHandler.work_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
