# 待办扫描 — Cron Prompt（决策脑 v2）

## 任务

从微信私聊中提取待办事项，结合用户状态推断优先级，推送到飞书。

## 执行步骤

### 1. 检查预刷新结果 + 同步消息

定时任务的 pre-run script `wechat_health_gate.py` 已经完成解密刷新。先读取 prompt
顶部的 `Script Output`：只有 `wakeAgent: true` 且状态为 `ok` 或
`ok_after_full_refresh` 时才继续。**不要再次运行 `refresh_decrypt.py`**。

直接同步到 collector.db：

```bash
cd ~/.hermes/skills/social-media/wechat-assistant/scripts
python3 collector.py --config ~/wechat-assistant/config.yaml --sync
```

> 健康检查未通过时 scheduler 会跳过本轮。不要根据输出中的 HMAC 警告文字
> 自行断言“密钥过期”或要求重新提取密钥。

### 2. 感知用户状态（Layer A）

在提取待办之前，先推断用户当前状态：

```bash
python3 -c "
import sys
sys.path.insert(0, '~/.hermes/skills/social-media/wechat-assistant/scripts')
from state_manager import StateManager
sm = StateManager('~/wechat-assistant/scan_state.json')
status, context = sm.infer_user_status()
print(f'USER_STATUS={status}')
print(f'USER_CONTEXT={context}')
# 自动确认超过2小时的旧 todo
acked = sm.auto_ack_old_todos(hours=2)
if acked > 0:
    print(f'AUTO_ACKED={acked}')
# 自动完成过期/陈旧 todo，避免很久以前的事项持续提醒
expired = sm.auto_complete_expired_todos(grace_days=2, stale_days=14)
if expired.get('total', 0) > 0:
    print(
        'AUTO_COMPLETED_EXPIRED='
        f"{expired['total']} "
        f"deadline={expired.get('deadline_expired', 0)} "
        f"stale={expired.get('stale_no_deadline', 0)}"
    )
"
```

这会输出当前用户状态：`sleeping` / `busy` / `working` / `idle`。
后续步骤据此调整推送策略。

### 2.5 检查手动状态设置

检查 user_state.json 中是否有未过期的手动状态设置：

```bash
python3 -c "
import sys, json, os
from datetime import datetime, timezone, timedelta

tz8 = timezone(timedelta(hours=8))
state_path = '~/wechat-assistant/user_state.json'
if os.path.exists(state_path):
    with open(state_path) as f:
        us = json.load(f)
    current = us.get('current', {})
    if current.get('source') == 'user_set':
        manual_at = current.get('manual_set_at', '')
        if manual_at:
            elapsed = (datetime.now(tz=tz8) - datetime.fromisoformat(manual_at)).total_seconds() / 3600
            if elapsed < 4:
                print(f'MANUAL_STATUS={current["status"]}')
                print(f'MANUAL_CONTEXT={current.get("context", "")}')
                print(f'MANUAL_REMAINING={4 - elapsed:.1f}h')
            else:
                print('MANUAL_STATUS=expired')
        else:
            print('MANUAL_STATUS=none')
    else:
        print('MANUAL_STATUS=none')
else:
    print('MANUAL_STATUS=none')
"
```

如果输出 `MANUAL_STATUS=xxx` 且不是 expired/none：
- 用手动设置的状态覆盖 infer_user_status() 的结果
- 推送状态栏中显示 `状态: {MANUAL_STATUS} (手动设置，剩余 Xh)`

### 3. 提取私聊数据

```bash
python3 extract_todos.py --config ~/wechat-assistant/config.yaml
```

> 输出 JSON 到 stdout，包含 `conversations`、`existing_todos`（来自 scan_state.json）和 `scan_state_path`。

### 4. 轻量偏好归档

每次扫描顺便归档今天的偏好消息（不调 AI，纯关键词匹配）：

```bash
# 提取今日偏好数据，追加到按天归档文件
python3 extract_preferences.py --config ~/wechat-assistant/config.yaml > /tmp/pref_today.json
python3 -c "
import json, os, datetime
pref_dir = '~/wechat-assistant/preferences'
os.makedirs(pref_dir, exist_ok=True)
date_str = datetime.date.today().isoformat()
path = os.path.join(pref_dir, f'{date_str}.json')

# 合并：同一天的多次归档，去重
new_data = json.load(open('/tmp/pref_today.json'))
if os.path.exists(path):
    existing = json.load(open(path))
    seen = {p['msg_time'] for p in existing.get('preferences', [])}
    for p in new_data.get('preferences', []):
        if p['msg_time'] not in seen:
            existing['preferences'].append(p)
            seen.add(p['msg_time'])
    existing['stats'] = new_data['stats']
    existing['scan_time'] = new_data['scan_time']
    with open(path, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
else:
    with open(path, 'w') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
"
```

> 这步不需要推送，只是默默归档。preference-scan cron 会读这些归档文件做深度分析。

### 5. 分析 JSON 输出

从 conversations 中识别待办事项。

#### 什么算待办
- 对方**请求我做的事**（明确的 action item）
- **我承诺要做的事**（"好的我去处理"、"我来搞"）
- 涉及**金钱、合同、法律**的事项（urgent=true）
- 有**明确 deadline** 的事项（urgent=true）
- **重大事项**：即使没有明确 action，但涉及金钱交易、付款、收款、投资决定、重要约定、人事变动等，也应标记为待关注（urgent=true）
- **重要承诺**：双方达成一致的约定（不限于我单方面承诺）

#### 什么不算待办
- 纯聊天、寒暄、问好
- 已经当场解决的问题
- 咨询性质的对话（我在回答别人问题）
- 广告、推销、群发消息
- 纯表情、图片消息
- 已在 existing_todos 中且 status=done 的（不重复）

#### 去重规则
- 检查 `existing_todos` 中是否已存在相似待办（同一联系人 + 相似 summary）
- 已存在的不重复添加
- 检查是否有待办在对话中被解决（resolved）
- 对话中有"搞定了"、"已完成"、"不用了" → 标记对应 todo 为 done

### 6. 去重、状态流转与优先级排序（Layer B）

分析完成后，对每个待办进行优先级评估。考虑因素：
- **urgent 字段**：已标记 urgent 的 → 🔴 高优
- **时效性**：有明确 deadline 的 → 🔴；deadline 临近（<24h）→ 🔴🔴
- **用户当前状态**：如果 `USER_STATUS=sleeping`，所有推送降级；如果 `USER_STATUS=busy`，只推 🔴
- **存续时间**：已 open 超过 3 天且未 acknowledged → 🟡（提醒）
- **acknowledged 状态**：
  - `acknowledged=false` 的新 todo → 用 🔔 标记（未读）
  - `acknowledged=true` 的旧 todo → 正常展示（已读）

#### 稳定去重指纹

每个待办必须生成稳定 `fingerprint`，用于跨轮扫描去重。格式：

```text
contact|normalized_summary|deadline
```

规则：
- `contact` 使用联系人名或 chatroom_id。
- `normalized_summary` 去掉语气词、标点、空白、重复描述，保留核心动作和对象。
- `deadline` 没有明确截止时间时为空字符串。
- 如果 `existing_todos` 中已有相同或高度相似 fingerprint，不创建新待办，只更新 `last_seen_at`、`mention_count`、`latest_evidence`。
- 同一个主题被多次提到时合并为一条，展示“出现 N 次 / 最近一次 HH:MM”，不要重复列多条。

#### 状态流转

每条待办维护这些字段：
- `status`: `new` / `seen` / `due_soon` / `overdue` / `done` / `dismissed`
- `reminder_state`: `unnotified` / `notified` / `snoozed`
- `last_notified_at`: 最近一次推送时间，未推送则为空
- `last_seen_at`: 最近一次在微信里再次出现的时间
- `mention_count`: 累计出现次数

状态更新规则：
- 首次发现：`status="new"`, `reminder_state="unnotified"`, `acknowledged=false`
- 已推送的新待办：推送完成后改为 `status="seen"`, `reminder_state="notified"`, `acknowledged=true`
- 截止时间在 24 小时内：改为 `status="due_soon"`，允许再次提醒
- 已超过截止时间：改为 `status="overdue"`，每天最多提醒一次
- 截止时间超过 2 天仍未处理：由 `auto_complete_expired_todos(grace_days=2)` 自动标记 `done`，不再提醒
- 没有明确截止时间、已经确认/提醒过、且创建超过 14 天：自动标记 `done`，不再提醒
- 对话中出现“搞定了 / 已完成 / 不用了 / 取消了”：改为 `done` 或 `dismissed`

优先级标签：
| 标签 | 含义 | 条件 |
|------|------|------|
| 🔴 | 紧急 | urgent=true 或 deadline<24h |
| 🟡 | 需跟进 | 非 urgent 但需要行动 |
| 🟢 | 已确认 | acknowledged=true，等待结果 |
| ⚪ | 可延后 | 非 urgent + 无 deadline + >3天 |

### 7. 更新 scan_state.json

读取 `scan_state_path`（`~/wechat-assistant/scan_state.json`），用 Python 脚本更新：

```bash
python3 -c "
import json, sys, time
state_path = '~/wechat-assistant/scan_state.json'
with open(state_path) as f:
    state = json.load(f)

# 新增的待办：追加到 items，status='open', acknowledged=false
# 已解决的：标记 status='done'，加 resolved 时间戳
# last_scan_ts 更新为当前时间戳

state['todos']['last_scan_ts'] = int(time.time())

with open(state_path, 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
"
```

更新规则：
- 新增的待办：`status: "new"`, `reminder_state: "unnotified"`, `acknowledged: false`，含 `id`, `fingerprint`, `contact`, `summary`, `urgent`, `created`, `last_seen_at`, `mention_count`
- 已存在的相似待办：不要新增；只更新 `last_seen_at`, `mention_count`, `latest_evidence`
- 已推送的新增待办：写回 `status: "seen"`, `reminder_state: "notified"`, `acknowledged: true`, `last_notified_at`
- 临近截止：写回 `status: "due_soon"`
- 逾期：写回 `status: "overdue"`；同一天已经提醒过的逾期项不重复提醒
- 自动完成的过期/陈旧待办：保留 `auto_completed=true` 和 `auto_completed_reason`，只作为清理状态，不作为本轮“完成事项”推送
- 已解决的：`status: "done"`，加 `resolved` 时间戳

### 8. 更新用户状态（Layer A 写回）

每次扫描后更新 user_state.json：

```bash
python3 -c "
import sys, json, os
sys.path.insert(0, '~/.hermes/skills/social-media/wechat-assistant/scripts')
from state_manager import StateManager
sm = StateManager('~/wechat-assistant/scan_state.json')
state = sm._read()
todos = state.get('todos', {}).get('items', [])
open_todos = [t for t in todos if t.get('status') == 'open']
urgent_open = [t for t in open_todos if t.get('urgent')]
sm.update_user_state({
    'current': {
        'active_todos': len(open_todos),
        'urgent_unresolved': len(urgent_open),
        'last_active': __import__('datetime').datetime.now(__import__('datetime').timezone(__import__('datetime').timedelta(hours=8))).isoformat(),
        'source': 'scan_inferred'
    }
})
print('[OK] user_state updated')
"
```

### 9. 静默时间

**23:00 ~ 08:00 不推送飞书**。但状态照常更新（state + user_state）。
- 当前时间在此范围内 → 跳过推送步骤，只更新 state

### 10. 推送到飞书

默认不推送“无变化”心跳。只有以下情况才推送：
- 有新增待办
- 有待办进入 `due_soon`
- 有待办进入 `overdue`，且今天还没提醒过
- 有待办被标记完成或取消
- 当前是每日摘要时段（10:00 / 15:00 / 20:00），且有未完成待办

自动完成的过期/陈旧待办只是降噪清理，不要作为单独推送理由，也不要列入“✅ 本次完成”。如果本轮只有自动清理、没有新增/临期/人工完成/仍在进行摘要需求，则直接 `[SILENT]`。

普通旧待办不要每次完整重复展示；只进入“仍在进行”计数，最多列 3 条最重要的。

格式：
```
📋 **YYYY-MM-DD HH:MM 微信待办摘要**

🔔 **新增**
1. **联系人** — 待办描述
   来源：HH:MM 最近一次提到 · 出现 N 次

⏰ **临近截止**
1. **联系人** — 待办描述（截止：YYYY-MM-DD HH:MM）

⚠️ **逾期**
1. **联系人** — 待办描述（逾期 X 天）

✅ **本次完成**
- ~~联系人 — 待办描述~~

📌 **仍在进行**
- 共 N 条未完成；仅展示最重要 3 条

📊 N 新增 · N 临期 · N 逾期 · N 完成 · N 未完成
```

**展示规则（重要！）：**
- 不要每次列出所有旧待办。
- 无变化且不在每日摘要时段时，更新 state 后直接结束，不发飞书消息。
- 新待办只提醒一次；后续再次出现只更新 `mention_count` 和 `last_seen_at`。
- 临期待办在进入 24 小时窗口时提醒一次；如果截止时间变化，可以再提醒一次。
- 逾期待办每天最多提醒一次；超过宽限期后自动完成，不再继续提醒。
- 超过 3 天的已确认待办，只在每日摘要中最多展示 3 条。
- 超过 14 天且没有明确截止时间的已确认/已提醒待办，视为陈旧事项自动完成，不再展示。
- 如果用户回复"这个不用了"，标记为 done
- 每条展示都尽量合并同一主题，避免相似事项重复出现。

> **手动状态设置指令**
> 用户可以通过飞书回复以下指令来设置状态（Hermes 解析后执行 set_user_status）：
> - "我在开会" / "busy" → busy
> - "我在忙" / "勿扰" → unavailable
> - "我在休息" → idle
> - "恢复" / "正常" → 恢复为自动推断

### 11. 写入 assistant.db

将本次扫描结果写入 SQLite 数据库（用于跨 cron 查询和历史追踪）：

```bash
# 写入 todos
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/db_writer.py --db ~/wechat-assistant/assistant.db --table todos --data '[{items JSON array}]'

# 写入 push_feedback（记录本次推送的每个 todo）
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/db_writer.py --db ~/wechat-assistant/assistant.db --table push_feedback --data '[{push_type:"todo", content_summary:"联系人-描述", priority:"urgent"}]'

# 写入 scan_log
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/db_writer.py --db ~/wechat-assistant/assistant.db --scan-log "todo:ok:N新增 M完成"
```

如果 `config.yaml` 中 `reminders.enabled=true`，写回 `scan_state.json` 后执行一次 macOS 提醒事项同步，让新待办立即出现在 Reminders，并吸收用户在 Reminders 里的完成状态：

```bash
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/sync_reminders.py --config ~/wechat-assistant/config.yaml
```

### 12. 状态栏

每条推送消息末尾必须加上状态栏（紧跟在正文最后），格式：

```
---
🕐 cron: wechat-todo-scan · 运行于 YYYY-MM-DD HH:MM · 扫描窗口 HH:MM~HH:MM · 状态: {USER_STATUS} · 结果：N新增 N完成
```

**注意：不发送"无变化"的简短心跳，也不要每次重复完整待办列表。**
