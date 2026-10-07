"""B 站登录凭证持久化：插件 data/bilibili_auth.json。

保存内容：
{
  "cookie": {"SESSDATA": "...", "bili_jct": "...", "DedeUserID": "...", "buvid3": "...", ...},
  "refresh_token": "...",        # 用于续期 Cookie（B 站不一定返回）
  "mid": 123456, "uname": "昵称", "face": "头像 URL",
  "login_at": 1759...,           # 登录时间
  "expires_at": 1759...          # SESSDATA 的 Expires（取不到则为 0）
}

凭证属敏感信息，只落在插件本机 data/ 目录；页面展示时 SESSDATA / bili_jct /
refresh_token 一律掩码，不回传到前端。
"""
import json
import threading
import time
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "bilibili_auth.json"

_lock = threading.Lock()
_auth: dict = {
    "cookie": {},
    "refresh_token": "",
    "mid": 0,
    "uname": "",
    "face": "",
    "login_at": 0,
    "expires_at": 0,
}

try:
    if _FILE.exists():
        loaded = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            _auth.update({k: v for k, v in loaded.items() if v is not None})
except Exception:  # noqa: BLE001 —— 读不出来就当未登录，别拦住插件加载
    pass


# ---------------- 写盘 ----------------


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_auth, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)   # 原子写：先 .tmp 再替换
    except Exception:  # noqa: BLE001 —— 写盘失败不该把调用方带崩
        pass


def save_auth(
    cookie: dict,
    refresh_token: str = "",
    user: dict | None = None,
    expires_at: int = 0,
) -> None:
    """保存一次登录结果。user 形如 {mid, uname, face}。"""
    global _auth
    with _lock:
        _auth["cookie"] = dict(cookie or {})
        if refresh_token:
            _auth["refresh_token"] = refresh_token
        info = user or {}
        if info.get("mid"):
            _auth["mid"] = int(info["mid"])
        if info.get("uname"):
            _auth["uname"] = str(info["uname"])
        if info.get("face"):
            _auth["face"] = str(info["face"])
        _auth["login_at"] = int(time.time())
        if expires_at:
            _auth["expires_at"] = int(expires_at)
        _save()


def clear() -> None:
    """退出登录：清空全部凭证。"""
    global _auth
    with _lock:
        _auth = {
            "cookie": {},
            "refresh_token": "",
            "mid": 0,
            "uname": "",
            "face": "",
            "login_at": 0,
            "expires_at": 0,
        }
        try:
            _FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------- 读 ----------------


def cookie() -> dict:
    with _lock:
        return dict(_auth.get("cookie") or {})


def cookie_str() -> str:
    """拼成 Cookie 请求头（请求时用 cookies=dict 即可，这里给调试/兜底用）。"""
    return "; ".join(f"{k}={v}" for k, v in cookie().items())


def refresh_token() -> str:
    with _lock:
        return str(_auth.get("refresh_token") or "")


def user() -> dict:
    with _lock:
        return {
            "mid": int(_auth.get("mid") or 0),
            "uname": str(_auth.get("uname") or ""),
            "face": str(_auth.get("face") or ""),
        }


def login_at() -> int:
    with _lock:
        return int(_auth.get("login_at") or 0)


def expires_at() -> int:
    with _lock:
        return int(_auth.get("expires_at") or 0)


def logged_in() -> bool:
    """是否已登录：以 SESSDATA 为准。"""
    return bool(cookie().get("SESSDATA"))


def masked() -> dict:
    """给页面展示的脱敏信息（不含任何可用凭证）。"""
    ck = cookie()
    return {
        "logged": logged_in(),
        "user": user(),
        "login_at": login_at(),
        "expires_at": expires_at(),
        "has_refresh_token": bool(refresh_token()),
        "cookie_keys": sorted(ck.keys()),
        "sessdata": (_mask(ck.get("SESSDATA", "")) if ck.get("SESSDATA") else ""),
    }


def _mask(text: str) -> str:
    text = str(text or "")
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]
