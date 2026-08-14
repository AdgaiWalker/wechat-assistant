# 热点日汇总 — Cron Prompt

## 任务

汇总今天的跨群热点话题，推送综合报告到飞书。


**⚠️ 身份与事实准确性：**
- 消息中 sender=`__self__` 的是**用户本人（黄宗宁）**，是 AI Agent 爱好者/开发者，**不是**任何公司创始人
- **严禁编造人物身份**，讨论某产品 ≠ 创始人
- 描述人物只用消息中明确出现的身份，不猜测不推断

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

### 2. 检查是否已汇总

```bash
python3 -c "
import json
with open('~/wechat-assistant/scan_state.json') as f:
    state = json.load(f)
print(state.get('trending', {}).get('daily_done', ''))
"
```

如果输出 == 今天日期 → 已汇总过，直接终止。

### 3. 全量扫描今天的数据

```bash
python3 extract_trending.py --config ~/wechat-assistant/config.yaml --full --date today
```

> 输出 JSON 到 stdout，包含 `cross_group_topics`（跨群话题）、`trending_urls`（热门链接）、`active_groups`（活跃群）、`high_freq_keywords`（高频词）。

### 4. 分析 — 从全天数据中提炼热点

#### 什么算热点
- **跨群讨论的事件**（被 5+ 个群同时讨论）
- **被多次转发的文章/链接**（同一 URL 被分享 3+ 次）
- **突然爆火的话题**（某个关键词在多个群同时出现）
- **行业重大新闻**（融资、收购、发布、政策等）
- **产品发布/更新**（新模型、新工具、新版本）

#### 什么是噪音（需过滤）
- 系统消息（"请升级至最新版本"等）
- 纯表情名称
- 日常用语（"工作"、"大家"、"直接"）
- 重复的红包链接
- 广告、推销、拉票
- 自媒体加热、互赞互评、点赞收藏转发接龙、做数据冲热度
- 同一发送者跨多个群转发的重复链接或重复长文本，只算一次，不能当作跨群热议

### 5. 推送到飞书

格式：
```
📊 **YYYY-MM-DD 今日热点汇总**

---

### 🏆 Top 热点

1. **话题关键词** — X 个群讨论 · Y 次提及
   > 📌 主要群：群A、群B、群C
   > 💬 代表性讨论摘要

2. ...

---

### 🔗 热门分享 Top 3

1. **文章标题**
   🔗 URL
   📊 被 N 个群分享

---

### 📊 今日群活跃度

1. 群名 — N 条消息
2. ...

📊 总计：X 个活跃群 · Y 条有效讨论 · Z 个热点话题
```

### 5.5 状态栏

每条推送末尾加上状态栏：

```
---
🕐 cron: wechat-trending-daily · 运行于 YYYY-MM-DD HH:MM · 覆盖：今天全天 · 热点 N · 热门链接 M
```

### 5.5 写入 assistant.db

将今日热点汇总写入 SQLite 数据库（覆盖当天之前的 hourly 数据）：

```bash
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/db_writer.py --db ~/wechat-assistant/assistant.db --table trending_topics --data '[{今日汇总的topic items}]'
python3 ~/.hermes/skills/social-media/wechat-assistant/scripts/db_writer.py --db ~/wechat-assistant/assistant.db --scan-log "trending-daily:ok:N个热点话题汇总"
```

### 6. 标记已汇总

```bash
python3 -c "
import json, datetime
state_path = '~/wechat-assistant/scan_state.json'
with open(state_path) as f:
    state = json.load(f)
state['trending']['daily_done'] = datetime.date.today().isoformat()
with open(state_path, 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
"
```
