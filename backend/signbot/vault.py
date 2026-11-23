"""
config.json 里敏感字段的静态加密（BUGS-v2.25 的 P2-3）。

## 为什么需要

Android 侧的凭据早就走 `SecretBox`（AES-GCM + AndroidKeyStore），但电脑端
`backend/data/config.json` 一直是明文：

    "qoder": { "token": "dt-UUlawOCdKEHsNHQtilNDW...",
               "refreshToken": "drt-DtC8J4a8uH7QN99rN0De..." }

任何能读这个文件的东西（同步盘、备份、另一个进程、误发的 zip）都等于拿到了
上游 accessToken / refreshToken。

## 怎么做的

用 Windows DPAPI（`CryptProtectData`）—— 与 `qoder.py` 里解 Qoder 客户端
`Local State` 用的是同一套 API，只是方向相反。密钥由系统按「当前用户」托管，
不落地、不需要用户再记一个口令，重启后照样能解。

存盘形态：`dpapi:<base64(密文)>`。读盘时遇到这个前缀就解密，其它形态原样返回
（兼容老配置文件，也兼容非 Windows 环境）。

## 边界

- 只加密**已知的凭据字段**（见 `SENSITIVE_PATHS`），不动 `mode` / `region` /
  `url` 这些非敏感项 —— 否则用户手改 config.json 时会完全看不懂。
- 带 `***` 的掩码值不加密（那是前端回传的占位，不该进磁盘）。
- 空串不加密（保持 `""`，让 `readiness()` 的「未配置」判断继续成立）。
- 已经是 `dpapi:` 前缀的值不重复加密（幂等）。
- 非 Windows（没有 `crypt32`）时 `protect` 原样返回 —— 不因为「加密不可用」
  就把用户的配置写坏。

## 解密失败不能覆盖密文（BUGS-v2.40 的 P1-1）

`unprotect` 解不开时**不能**只返回空串：`load_config()` 把这个空串当明文交给
调用方，之后任意一次 `save_config()`（设置页保存 / token 续期 / 调度器写库）
都会把 `""` 加密成 `""` 写回，原密文永久丢失。Android 侧有 `DECRYPT_FAILED`
哨兵 + `credsLost` 守卫，电脑端原来没有。

这里补三件事：

1. `unprotect` 失败返回 `SENTINEL`，并在 `_failed` 里记下这个字段路径；
2. `decrypt_config` 把 `SENTINEL` 映射回 `""` 给业务用（运行时语义仍是「未配置」），
   但 `_failed` 保留 —— 谁失败过一目了然；
3. `restore_failed(disk)` 在落盘前把「内存空串 + 磁盘是密文 + 该字段解密失败过」
   的项还原成磁盘原值，于是密文不会被空串覆盖；`reset_creds` 这类**用户主动
   清空**的动作会先 `mark_cleared()`，不进入还原名单。

`failures()` / `reset_failures()` 供 `/api/status` 的 `crypto_ok` 与
`POST /api/crypto_reset` 使用（与 Android 侧口径对齐）。
"""
import base64
import ctypes
import sys
import threading
from ctypes import wintypes

PREFIX = "dpapi:"

# 解不开时的哨兵。用控制字符开头，正常凭据（token / cookie / JWT）不可能包含它，
# 因此可以安全地用它区分「解密失败」与「本来就是空」。
SENTINEL = "\u0000VAULT_FAILED"

# (provider, section, key) —— 与 config.py 的 DEFAULT_CONFIG 结构一一对应。
# 新增 provider 时必须同步这里，否则它的凭据会明文落盘（v2.27 的华为就是这样漏的）。
SENSITIVE_PATHS = [
    ("workbuddy", "wb", "accessToken"),
    ("workbuddy", "wb", "refreshToken"),
    ("traesolo", "trae", "token"),
    ("traesolo", "trae", "session"),
    ("qoder", "qoder", "token"),
    ("qoder", "qoder", "refreshToken"),
    ("minimax", "minimax", "accessToken"),
    ("minimax", "minimax", "refreshToken"),
    ("huawei", "huawei", "cookie"),
    ("huawei", "huawei", "portalCookie"),
]

_failed = set()          # {(prov, sect, key)} 本轮解密失败过的字段
_cleared = set()         # {(prov, sect, key)} 用户主动清空的字段（不参与还原）
_lock = threading.Lock()


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _available() -> bool:
    return sys.platform == "win32"


def protect(plain: str) -> str:
    """明文 → `dpapi:<b64>`。空串 / 掩码值 / 已加密 / 非 Windows 时原样返回。"""
    if not plain or PREFIX in plain[:8]:
        return plain
    if "***" in plain:
        return plain
    if not _available():
        return plain
    try:
        din = _blob(plain.encode("utf-8"))
        dout = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(din), None, None, None, None, 0, ctypes.byref(dout)
        )
        if not ok:
            return plain
        try:
            raw = ctypes.string_at(dout.pbData, dout.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(dout.pbData)
        return PREFIX + base64.b64encode(raw).decode("ascii")
    except Exception:
        return plain


def unprotect(value: str) -> str:
    """`dpapi:<b64>` → 明文。非该形态原样返回；解不开返回 `SENTINEL`。"""
    if not value or not value.startswith(PREFIX):
        return value
    if not _available():
        return SENTINEL
    try:
        raw = base64.b64decode(value[len(PREFIX):])
        din = _blob(raw)
        dout = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(din), None, None, None, None, 0, ctypes.byref(dout)
        )
        if not ok:
            return SENTINEL
        try:
            return ctypes.string_at(dout.pbData, dout.cbData).decode("utf-8", "replace")
        finally:
            ctypes.windll.kernel32.LocalFree(dout.pbData)
    except Exception:
        return SENTINEL


def failures():
    """本轮解密失败过的字段路径（`prov.sect.key` 字符串列表）。"""
    with _lock:
        return sorted("%s.%s.%s" % p for p in _failed)


def reset_failures():
    """清空失败名单（前端横幅点「知道了」/ 重新填完凭据后调用）。"""
    with _lock:
        _failed.clear()
        _cleared.clear()


def mark_cleared(paths):
    """登记「用户主动清空」的字段，落盘时不再从磁盘还原旧密文。"""
    with _lock:
        for p in paths:
            _cleared.add(tuple(p))
            _failed.discard(tuple(p))


def _node(cfg: dict, prov, sect):
    node = ((cfg.get("providers") or {}).get(prov) or {}).get(sect)
    return node if isinstance(node, dict) else None


def encrypt_config(cfg: dict) -> dict:
    """就地加密敏感字段并返回同一对象（调用方传副本，别直接给 load_config 的结果）。"""
    for prov, sect, key in SENSITIVE_PATHS:
        node = _node(cfg, prov, sect)
        if node is None:
            continue
        cur = node.get(key)
        if isinstance(cur, str):
            node[key] = protect(cur)
    return cfg


def decrypt_config(cfg: dict) -> dict:
    """
    就地解密敏感字段并返回同一对象。

    解不开的字段：值改成 `""`（业务语义 = 未配置），但路径记进 `_failed`，
    供 `restore_failed()` 在落盘时把原密文保住。
    """
    for prov, sect, key in SENSITIVE_PATHS:
        node = _node(cfg, prov, sect)
        if node is None:
            continue
        cur = node.get(key)
        if not isinstance(cur, str):
            continue
        out = unprotect(cur)
        path = (prov, sect, key)
        if out == SENTINEL:
            with _lock:
                _failed.add(path)
            node[key] = ""
        else:
            with _lock:
                _failed.discard(path)
            node[key] = out
    return cfg


def restore_failed(disk_cfg: dict) -> dict:
    """
    落盘前守卫：把「解密失败过 + 内存是空串 + 磁盘仍是密文」的字段还原成磁盘原值。

    不这样做的话，`_save_locked()` 会把 `""` 加密成 `""` 写回，
    DPAPI 密钥一变（换用户 / 重装系统 / 拷到别的机器）用户的凭据就被永久抹掉。
    """
    with _lock:
        todo = set(_failed) - set(_cleared)
    if not todo:
        return disk_cfg
    raw_cfg = _load_raw()
    for path in todo:
        prov, sect, key = path
        node = _node(disk_cfg, prov, sect)
        if node is None:
            continue
        if (node.get(key) or "") != "":
            continue  # 内存里已有值（用户刚重新填过），正常写入
        raw = (_node(raw_cfg, prov, sect) or {}).get(key)
        if isinstance(raw, str) and raw.startswith(PREFIX):
            node[key] = raw  # 原密文原样写回，避免被空串覆盖
    return disk_cfg


def _load_raw() -> dict:
    """直接读磁盘上的 config.json 原文（不解密），只用于还原守卫。"""
    import json
    from .config import CONFIG_PATH
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}
