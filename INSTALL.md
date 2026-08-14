# 安装

## 1. mac-wechat-assistant（Hermes · macOS）

```bash
# 复制到 Hermes skills 目录
cp -r mac-wechat-assistant ~/.hermes/skills/social-media/wechat-assistant

# 装依赖
pip3 install pycryptodome zstandard pyyaml

# 编译密钥提取工具
cd ~/.hermes/skills/social-media/wechat-assistant/scripts/decrypt
cc -O2 -o find_all_keys_macos find_all_keys_macos.c
```

之后跟 Hermes 说：`帮我设置微信助手`（Agent 会引导提 key、配 config、配飞书、注册 cron）。

## 2. windows-wechat-decrypt（Windows · PowerShell）

```powershell
# 复制到 Claude Code skills 目录（或任意目录独立使用）
cp -r windows-wechat-decrypt ~/.claude/skills/wechat-decrypt

# 在目录内打开 PowerShell：
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1                     # 装依赖
.\windows.ps1 auto --account <wxid目录>  # 首次：自动提 key + 解密全库
```

> `--account` 填 `xwechat_files` 下的账号目录名（如 `wxid_example_device`），不是微信昵称。
