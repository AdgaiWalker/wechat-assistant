# WeChat Decrypt for Windows

适用于用户本人已登录的 Windows 微信 4.x 数据。工具包不包含任何密钥、聊天数据库或解密结果。完整密码学参数见 [WINDOWS_ALGORITHM.md](WINDOWS_ALGORITHM.md)，功能和兼容边界见 [WINDOWS_FEATURES.md](WINDOWS_FEATURES.md)。

## 1. 使用前准备

- Windows x64。
- Python 3.10 或更高版本，建议安装 x64 版本并勾选“Add Python to PATH”。
- 微信已经登录到要解密的账号。
- 提取密钥必须在当前 Windows 桌面里打开的 PowerShell 中执行，不能从 SSH、服务或 session 0 执行。

先确认 Python 可用：

```powershell
python --version
python -m pip --version
```

把 ZIP 解压到普通本地目录，例如：

```text
D:\wechat-decrypt-windows
```

在该目录空白处按住 Shift 并单击鼠标右键，选择“在此处打开 PowerShell”，或者手工进入目录：

```powershell
cd D:\wechat-decrypt-windows
Get-ChildItem setup_windows.ps1, windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
```

最后一条命令会安装运行依赖，包括密钥提取所需的 Frida。

## 2. 找到数据根目录和账号目录

默认支持：

```text
D:\xwechat_files
%USERPROFILE%\Documents\xwechat_files
```

例如数据结构是：

```text
D:\xwechat_files
└── wxid_example_device
    └── db_storage
        └── message
            └── message_0.db
```

那么：

- `--data-root` 填 `D:\xwechat_files`；
- `--account` 填 `wxid_example_device`，不是 `db_storage`，也不是微信昵称。

可以用下面的命令查看有哪些账号目录：

```powershell
Get-ChildItem D:\xwechat_files -Directory
```

如果存在多个账号目录，每条命令都应显式填写 `--account`，避免选错账号。

## 3. 先判断自己属于哪种情况

### 没有 `key_windows.txt`：推荐直接使用 `auto`

```powershell
.\windows.ps1 auto --account wxid_example_device --data-root D:\xwechat_files
```

`auto` 会检查本地密钥；没有可用密钥时，它会关闭并重新启动微信，使用 Hook 捕获 raw key，验证成功后自动解密。小白首次使用只需要优先尝试这一条命令。

### 已有 raw key：保存后验证并解密

raw key（也可称初始密钥）是 32 字节数据，写成文本时应是 64 个十六进制字符。把它单独保存到解压目录的：

```text
key_windows.txt
```

然后执行：

```powershell
.\windows.ps1 status --account wxid_example_device --data-root D:\xwechat_files
.\windows.ps1 verify --account wxid_example_device --data-root D:\xwechat_files
.\windows.ps1 decrypt --account wxid_example_device --data-root D:\xwechat_files
```

- `status`：只用一个代表数据库检查 raw key，速度最快，不生成明文。
- `verify`：校验账号下每个数据库的每一页，不生成明文。
- `decrypt`：校验并写出明文数据库。
- `auto`：必要时先获取 raw key，然后执行解密。

## 4. 没有初始密钥时如何获取

如果 `auto` 没有完成，可以单独执行提取：

```powershell
.\windows.ps1 extract --account wxid_example_device --data-root D:\xwechat_files
```

默认过程如下：

1. 工具关闭当前微信。
2. Frida 在当前 Windows 交互会话中以挂起状态启动 `Weixin.exe`。
3. 工具在恢复微信前，对 `Weixin.dll` 的 SHA-512/PBKDF2 调用边界安装 Hook。
4. 微信打开数据库时，工具捕获候选 raw key。
5. 工具使用所选账号数据库第一页的 HMAC 验证候选。
6. 只有验证成功的 key 才会保存到 `key_windows.txt`；终端只显示指纹，不显示完整密钥。

少数环境不兼容默认 spawn 模式时，可尝试人工启动模式：

```powershell
python scripts\windows\extract_raw_key.py --force --mode manual --account wxid_example_device --data-root D:\xwechat_files
```

运行后按照终端提示，从 Windows 桌面双击启动微信。如果微信安装在非标准位置，可在提取命令后增加：

```powershell
--weixin-exe "D:\你的路径\Weixin.exe"
```

## 5. 有初始密钥时如何得到衍生密钥

不需要手工提取、计算或保存衍生密钥。工具读取每个加密数据库开头的 16 字节 salt，然后自动计算：

- `encKey`：该数据库的 AES-256-CBC 解密密钥；
- `macKey`：该数据库的逐页 HMAC-SHA512 完整性校验密钥。

因此，一个匹配当前 Windows 数据存储的 raw key，可以为其中的不同数据库分别派生不同的 `encKey` 和 `macKey`。数据库换了，salt 通常也会换，衍生密钥就会随之变化。完整公式、页面布局和校验范围已经记录在 [WINDOWS_ALGORITHM.md](WINDOWS_ALGORITHM.md)。

同一个微信账号在不同电脑，或 macOS 与 Windows 之间的 raw key 不保证相同，必须以目标数据库 HMAC 验证结果为准。

## 6. 如何判断是否成功

密钥提取成功时会看到类似：

```text
raw key captured and verified; fingerprint: ...
```

已有密钥匹配时会看到：

```text
saved key is valid for account: ...
```

全库验证或解密结束时会看到：

```text
SUMMARY: ... verified, 0 failed, ... total
SUMMARY: ... decrypted, 0 failed, ... total
```

明文数据库默认输出到：

```text
decrypted\<账号目录>\db_storage\...
```

解密器还会对明文数据库执行 SQLite `PRAGMA quick_check`。出现 `failed`、HMAC mismatch 或 quick check 错误时，不应把结果当作成功。

## 7. 查询和导出

启动 MCP Server：

```powershell
.\windows.ps1 serve --account wxid_example_device
```

导出指定联系人聊天：

```powershell
.\windows.ps1 export --account wxid_example_device "联系人名称" --year 2026 -o .\chat-export.txt
```

包内同时提供聊天查询、聊天导出、媒体导出、文档读取和语音转写工具。`serve` 和 `export` 会使用指定账号的已解密数据库。

## 8. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 找不到 `python` 命令 | 重新安装 Python 3.10+ x64，勾选加入 PATH，然后重开 PowerShell。 |
| 找不到账号数据库 | 检查 `--data-root` 是否指向 `xwechat_files`，并用 `Get-ChildItem <路径> -Directory` 确认 `--account`。 |
| 提示缺少 Frida | 重新运行 `.\setup_windows.ps1`，确认安装过程没有报错。 |
| 有多个账号 | 每条命令显式填写正确的完整 `--account` 目录名。 |
| 找不到 `Weixin.exe` | 使用 `--weixin-exe "完整路径\Weixin.exe"` 指定安装位置。 |
| `no valid raw key captured` | 确认微信已登录并从可见桌面 PowerShell 运行；再尝试 `--mode manual`。 |
| `page 1 HMAC mismatch` | 当前 key 与所选账号/数据库不匹配；核对账号目录，必要时重新提取。 |
| 微信没有正常启动或 Hook 被阻止 | 检查 Windows Defender、杀毒软件或企业 EDR 是否拦截 Frida；仅在可信环境中临时放行。 |

## 9. 安全说明

以下内容在使用后都属于敏感数据：

- `key_windows.txt`；
- `all_keys.json`；
- `decrypted\` 下的明文数据库；
- 聊天文本导出、媒体导出和语音转写结果。

发布的原始 ZIP 已经过白名单和敏感信息扫描，但运行过工具的工作目录会产生敏感文件。不要把使用后的整个目录重新压缩发送给别人，也不要放入 Git 仓库或网盘同步目录。不再需要时，应删除密钥、明文数据库和导出文件。

仅处理本人拥有或明确获授权的数据。
