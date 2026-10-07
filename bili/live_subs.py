"""B 站直播 → 群 的订阅配置：插件 data/bilibili_live_subs.json。

一条订阅 = 「某个主播的开播/下播」→「某个 AstrBot 平台实例」→「某个群」。
结构与 `subs.py`（动态订阅）完全对称，只是没有 `types`（直播只有开播/下播两种事件）。

只负责配置的增删改查与落盘，实际推送由 `live_pusher` 模块按这里的配置执行。
"""
import json
import threading
import time
import uuid

from . import paths

_FILE = paths.DATA_DIR / "bilibili_live_subs.json"

_lock = threading.Lock()
_subs: list[dict] = []

try:
    if _FILE.exists():
        loaded = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded = loaded.get("subs") or []
        if isinstance(loaded, list):
            _subs = [s for s in loaded if isinstance(s, dict)]
except (OSError, json.JSONDecodeError):
    _subs = []


# ---------------- 写盘 ----------------


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(
            json.dumps({"subs": _subs}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# ---------------- 归一化 ----------------


def _bool(value, default: bool = True) -> bool:
    """老数据缺字段时给默认值；显式写成 false / "false" / 0 的都当 False。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _norm(sub: dict) -> dict:
    """对外输出补全缺失字段（老数据没有 notify_live / notify_offline）。"""
    d = dict(sub)
    d["notify_live"] = _bool(d.get("notify_live"), True)
    d["notify_offline"] = _bool(d.get("notify_offline"), True)
    d["room_url"] = str(d.get("room_url") or (
        f"https://live.bilibili.com/{d['room_id']}" if d.get("room_id") else ""
    ))
    return d


# ---------------- 读 ----------------


def all() -> list[dict]:
    with _lock:
        return [_norm(s) for s in _subs]


def get(sub_id: str) -> dict | None:
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                return _norm(s)
    return None


def find(mid, platform_id: str, group_id: str) -> dict | None:
    """按 (主播, 平台实例, 群) 查重，同一个组合只保留一条订阅。"""
    with _lock:
        for s in _subs:
            if (
                _int(s.get("mid")) == _int(mid)
                and str(s.get("platform_id") or "") == str(platform_id or "")
                and str(s.get("group_id") or "") == str(group_id or "")
            ):
                return _norm(s)
    return None


def enabled() -> list[dict]:
    return [s for s in all() if s.get("enabled")]


# ---------------- 改 ----------------


def add(
    mid,
    uname: str,
    face: str,
    platform_id: str,
    group_id: str,
    platform_name: str = "",
    group_name: str = "",
    room_id=None,
    notify_live=None,
    notify_offline=None,
) -> tuple[bool, str, dict | None]:
    """新增订阅；同一 (mid, 平台, 群) 组合已存在则直接返回旧的（不重复添加）。"""
    mid_i = _int(mid)
    if not mid_i:
        return False, "缺少主播 UID", None
    if not str(platform_id or "").strip():
        return False, "请选择推送用的平台实例", None
    if not str(group_id or "").strip():
        return False, "请填写推送到的群 ID", None
    with _lock:
        for s in _subs:
            if (
                _int(s.get("mid")) == mid_i
                and str(s.get("platform_id") or "") == str(platform_id)
                and str(s.get("group_id") or "") == str(group_id)
            ):
                return (
                    False,
                    f"已存在：{uname or s.get('uname')} → 群 {group_name or group_id}",
                    _norm(s),
                )
        room_i = _int(room_id)
        sub = {
            "id": uuid.uuid4().hex[:10],
            "mid": mid_i,
            "uname": str(uname or ""),
            "face": str(face or ""),
            "room_id": room_i,
            "room_url": f"https://live.bilibili.com/{room_i}" if room_i else "",
            "platform_id": str(platform_id),
            "platform_name": str(platform_name or ""),
            "group_id": str(group_id),
            "group_name": str(group_name or ""),
            "notify_live": _bool(notify_live, True),
            "notify_offline": _bool(notify_offline, True),
            "enabled": True,
            "created_at": int(time.time()),
        }
        _subs.append(sub)
        _save()
        return True, f"已订阅：{sub['uname'] or mid_i} → 群 {group_name or group_id}", _norm(sub)


def remove(sub_id: str) -> tuple[bool, str]:
    with _lock:
        for i, s in enumerate(_subs):
            if str(s.get("id")) == str(sub_id):
                name = s.get("uname") or s.get("mid")
                _subs.pop(i)
                _save()
                return True, f"已删除直播订阅：{name}"
    return False, "直播订阅不存在"


def set_enabled(sub_id: str, enabled: bool) -> tuple[bool, str]:
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                s["enabled"] = bool(enabled)
                _save()
                name = s.get("uname") or s.get("mid")
                return True, f"已{'启用' if enabled else '停用'}直播订阅：{name}"
    return False, "直播订阅不存在"


def set_notify(sub_id: str, notify_live=None, notify_offline=None) -> tuple[bool, str, dict | None]:
    """修改「开播推 / 下播推」开关（至少留一个，两个都关等于订阅失效）。"""
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                # 老数据缺字段时按「都推」处理
                live = _bool(s.get("notify_live"), True)
                offline = _bool(s.get("notify_offline"), True)
                if notify_live is not None:
                    live = _bool(notify_live, True)
                if notify_offline is not None:
                    offline = _bool(notify_offline, True)
                if not live and not offline:      # 两个都关 → 自动回落到两个都开
                    live = offline = True
                s["notify_live"] = live
                s["notify_offline"] = offline
                _save()
                name = s.get("uname") or s.get("mid")
                got = [x for x, on in (("开播", live), ("下播", offline)) if on]
                return True, f"已更新推送事件：{name} → {'+'.join(got)}", _norm(s)
    return False, "直播订阅不存在", None


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
