# Windows 微信数据库解密算法记录

本文记录当前工具已验证的 Windows 微信 4.x 数据库密码学流程。普通用户不需要手工执行这些计算；`verify`、`decrypt` 和 `auto` 会自动完成。

## 1. 密钥层级

- `rawKey`：从已登录微信进程捕获或从 `key_windows.txt` 读取的 32 字节初始密钥。
- `salt`：每个加密数据库第一页开头的 16 字节。
- `encKey`：由 `rawKey` 和当前数据库的 `salt` 派生出的 32 字节数据库解密密钥。
- `macKey`：由 `encKey` 和变换后的 salt 派生出的 32 字节页面校验密钥。

派生公式：

```text
encKey = PBKDF2-HMAC-SHA512(
    password = rawKey,
    salt = db[0:16],
    iterations = 256000,
    dkLen = 32
)

macSalt = salt XOR 0x3A

macKey = PBKDF2-HMAC-SHA512(
    password = encKey,
    salt = macSalt,
    iterations = 2,
    dkLen = 32
)
```

这里的 XOR 是对 salt 的每个字节分别与 `0x3A` 做异或。

## 2. 页面布局

当前已验证格式：

- 页面大小：4096 字节。
- 每页保留区：80 字节。
- IV：16 字节，偏移 4016–4031。
- 已存 HMAC-SHA512：64 字节，偏移 4032–4095。
- 页面加密：AES-256-CBC。
- 页码：从 1 开始，以 4 字节小端序加入 HMAC 输入。

第一页的偏移 0–15 是 salt，不属于 AES 密文。第一页 AES 密文范围是偏移 16–4015；后续页面是偏移 0–4015。

## 3. HMAC 校验

对第一页，HMAC 输入是：

```text
page[16:4032] || uint32_le(1)
```

对第 N 页（N > 1），HMAC 输入是：

```text
page[0:4032] || uint32_le(N)
```

使用 `macKey` 计算 HMAC-SHA512，并与页面最后 64 字节比较。任何一页不匹配都停止解密，避免用错误 key 输出伪明文。

## 4. 页面解密和 SQLite 还原

1. 从页面偏移 4016–4031 读取 IV。
2. 使用 `encKey` 和该 IV 执行 AES-256-CBC 解密。
3. 第一页在明文开头恢复 16 字节 `SQLite format 3\0` 文件头。
4. 每页末尾的 80 字节保留区在明文输出中写为零。
5. 全库完成后执行 SQLite schema 读取和 `PRAGMA quick_check`。

## 5. raw key 的获取与验证

没有 raw key 时，Windows 提取器在已登录微信启动并访问数据库期间，Hook `Weixin.dll` 的 SHA-512/PBKDF2 调用边界并收集候选值。候选值只有通过目标账号数据库第一页 HMAC 校验后，才会写入 `key_windows.txt`。

这意味着 Hook 获取的是可用于派生的 `rawKey`，不是每个数据库各自的 `encKey`。每库 `encKey` 和 `macKey` 始终由数据库 salt 在本地即时派生，不需要单独提取或持久化。

## 6. 兼容性边界

上述参数已在 Windows 微信 `4.1.11.55` 的当前数据格式上验证。如果微信以后修改 KDF、迭代次数、页面大小、保留区、HMAC 输入或底层散列调用，验证会失败，需要重新分析新版本，不能跳过 HMAC 强制输出。
