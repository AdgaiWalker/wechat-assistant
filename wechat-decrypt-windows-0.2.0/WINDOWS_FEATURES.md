# Windows 版功能列表

本文只说明功能和已验证边界，不是操作手册。实际使用请先阅读 [WINDOWS_README.md](WINDOWS_README.md)，密码学细节见 [WINDOWS_ALGORITHM.md](WINDOWS_ALGORITHM.md)。

## 密钥管理

- 一键 `auto` 完成“检查旧 key → 必要时 Hook → 验证 → 解密”。
- 复用本地已保存的 Windows raw Data Key。
- 使用密钥指纹显示身份，不在终端打印完整密钥。
- 自动通过数据库第一页 HMAC 判断密钥对应哪个账号。
- 多账号环境支持 `--account` 明确选择，避免跨账号误解密。
- 旧密钥失效时，通过 Frida Hook `Weixin.dll` 的 SHA-512/PBKDF2 调用边界重新提取。
- 提取候选必须通过指定账号数据库 HMAC 校验后才能保存。

## 数据库解密

- 支持微信 4.x 当前已验证的 4096 字节页面格式。
- PBKDF2-HMAC-SHA512，256000 次派生每库 encKey。
- AES-256-CBC 页面解密。
- HMAC-SHA512 每页完整性验证。
- 支持一个 raw key 派生同一 Windows 数据存储下多个数据库密钥。
- 流式处理大数据库，避免整库加载内存。
- 原子输出，失败时清理 `.part` 文件。
- 解密后运行 SQLite `PRAGMA quick_check`。
- 保留原账号和 `db_storage` 相对目录结构。

## 聊天数据工具

- MCP Server 浏览和搜索聊天。
- 会话列表、最近消息和结构化摘要。
- 按联系人、年份或日期范围导出聊天。
- 文本、图片、视频、贴纸、语音、引用、链接和小程序类型识别。
- 媒体文件导出。
- 数据库查询和只读访问工具。
- Windows 使用解密后的标准 SQLite 数据库，无需额外 SQLCipher 可执行文件。

## 发布与安全

- Windows ZIP 使用运行文件白名单生成。
- 不包含 `.git`、Git log、密钥、聊天数据库、明文数据库、联系人配置、缓存和日志。
- 构建时扫描私钥标记和疑似 64 位十六进制密钥。
- ZIP 内附 SHA-256 文件清单。
- Windows ZIP 不包含仍依赖 macOS `key.txt`/容器路径的 repair、salvage 和 SQLCipher 辅助脚本。

## 已验证边界

- Windows 微信 `4.1.11.55`。
- 当前账号 26 个加密数据库全部通过 HMAC 验证并成功解密。
- 26 个明文数据库均可读取 SQLite schema。
- 若未来微信更改 KDF、页面布局、密码算法或 SHA-512 实现，现有验证会安全失败，需要重新分析对应版本。
