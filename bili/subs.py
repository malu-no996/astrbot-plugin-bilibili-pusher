"""B 站动态 → 群 的订阅配置：插件 data/bilibili_subs.json。

一条订阅 = 「某个 UP 主的动态」→「某个 AstrBot 平台实例」→「某个群」。

推送目标在 AstrBot 里就是一个统一会话 origin：`{platform_id}:GroupMessage:{group_id}`
（platform_id 是 WebUI「消息平台」里的实例 ID，group_id 是该平台群消息事件里的
session_id，例如 aiocqhttp 的 QQ 群号）。实际推送由 pusher 模块按这里的配置执行。

结构：
{
  "subs": [
    {
      "id": "a1b2c3d4e5",
      "mid": 123456, "uname": "UP 名", "face": "头像 URL",
      "platform_id": "aiocqhttp", "platform_name": "NapCat",
      "group_id": "87654321", "group_name": "群名",
      "types": ["archive"],     # 只推这些类型的动态（archive=投稿，见 client.DYN_KINDS）
      "keyword": "",            # 图文内容过滤：非空时只推「标题/正文含其中任一关键词」的**图文**动态
                                # （只对 kind=opus 生效，投稿/专栏/直播等照常推；空 = 不过滤）
      "enabled": true, "created_at": 1769...
    }
  ]
}
"""
import json
import threading
import time
import uuid

from . import paths
from .client import clean_keyword, normalize_kinds, split_keywords

_FILE = paths.DATA_DIR / "bilibili_subs.json"

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


# ---------------- 读 ----------------


def all() -> list[dict]:
    with _lock:
        out = []
        for s in _subs:
            d = dict(s)
            # 老数据没有 types 字段：对外一律补成默认（只推投稿）
            d["types"] = normalize_kinds(d.get("types"))
            # 老数据没有 keyword：空串 = 不做内容过滤（保持原行为）
            d["keyword"] = str(d.get("keyword") or "").strip()
            out.append(d)
        return out


def get(sub_id: str) -> dict | None:
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                return dict(s)
    return None


def find(mid, platform_id: str, group_id: str) -> dict | None:
    """按 (UP 主, 平台实例, 群) 查重，同一个组合只保留一条订阅。"""
    with _lock:
        for s in _subs:
            if (
                _int(s.get("mid")) == _int(mid)
                and str(s.get("platform_id") or "") == str(platform_id or "")
                and str(s.get("group_id") or "") == str(group_id or "")
            ):
                return dict(s)
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
    types=None,
    keyword: str = "",
) -> tuple[bool, str, dict | None]:
    """新增订阅；同一 (mid, 平台, 群) 组合已存在则直接返回旧的（不重复添加）。

    `keyword` = **图文**内容过滤（正文需包含的关键词，多个用逗号/换行分隔，命中任一即推），
    **只对图文动态（kind=opus）生效**，其它类型一律照推；空串 = 不过滤。
    """
    mid_i = _int(mid)
    if not mid_i:
        return False, "缺少 UP 主 UID", None
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
                    dict(s),
                )
        sub = {
            "id": uuid.uuid4().hex[:10],
            "mid": mid_i,
            "uname": str(uname or ""),
            "face": str(face or ""),
            "platform_id": str(platform_id),
            "platform_name": str(platform_name or ""),
            "group_id": str(group_id),
            "group_name": str(group_name or ""),
            "types": normalize_kinds(types),
            "keyword": clean_keyword(keyword),
            "enabled": True,
            "created_at": int(time.time()),
        }
        _subs.append(sub)
        _save()
        return True, f"已订阅：{sub['uname'] or mid_i} → 群 {group_name or group_id}", dict(sub)


def remove(sub_id: str) -> tuple[bool, str]:
    with _lock:
        for i, s in enumerate(_subs):
            if str(s.get("id")) == str(sub_id):
                name = s.get("uname") or s.get("mid")
                _subs.pop(i)
                _save()
                return True, f"已删除订阅：{name}"
    return False, "订阅不存在"


def set_enabled(sub_id: str, enabled: bool) -> tuple[bool, str]:
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                s["enabled"] = bool(enabled)
                _save()
                name = s.get("uname") or s.get("mid")
                return True, f"已{'启用' if enabled else '停用'}订阅：{name}"
    return False, "订阅不存在"


def set_types(sub_id: str, types) -> tuple[bool, str, dict | None]:
    """修改订阅的推送类型（至少会被夹成一个，空则回落默认）。"""
    kinds = normalize_kinds(types)
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                s["types"] = kinds
                _save()
                name = s.get("uname") or s.get("mid")
                return True, f"已更新订阅类型：{name} → {','.join(kinds)}", dict(s)
    return False, "订阅不存在", None


def set_keyword(sub_id: str, keyword) -> tuple[bool, str, dict | None]:
    """修改订阅的**图文**内容过滤关键词（空串 = 清除过滤，恢复「图文也全推」）。

    过滤只作用于图文动态（pusher.KEYWORD_KINDS），其它类型本来就不受它影响。
    """
    text = clean_keyword(keyword)
    with _lock:
        for s in _subs:
            if str(s.get("id")) == str(sub_id):
                s["keyword"] = text
                _save()
                name = s.get("uname") or s.get("mid")
                kws = split_keywords(text)
                if kws:
                    return True, f"已更新图文过滤：{name} → 图文只推含「{'、'.join(kws[:5])}」的", dict(s)
                return True, f"已清除图文过滤（{name}）：图文也全部推送", dict(s)
    return False, "订阅不存在", None


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
