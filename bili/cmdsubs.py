"""命令订阅：群成员发「订阅B站推送 / 取消B站推送」命令产生的推送目标。

记录字段（用户指定的保存格式）：
- qq_id:       订阅者（发命令的人）；QQ 官方机器人拿不到真实 QQ 号时是平台给的 open id
- group_id:    群 ID（该平台群消息事件里的 session_id；aiocqhttp = QQ 群号）
- platform:    平台适配器类型（aiocqhttp = OneBot/NapCat 等，qq_official = QQ 官方机器人）
- bilibili_id: 订阅的 UP 主 UID
另存 platform_id（AstrBot 平台实例 ID，推送用它拼统一会话 origin）、uname、created_at。

命令触发配置落盘 data/bilibili_cmds.json（触发词/开关/仅管理员可在「命令」页改），
订阅记录落盘 data/bilibili_cmd_subs.json。两个文件都是**进程内直改 + 原子写回**。

推送接入：`targets()` 把记录整形成 pusher 认识的「伪订阅」（全类型、无关键词过滤），
去重/基线/防风控逻辑全部复用 pusher 现有的（state key 就是这里的记录 id）。
"""
from __future__ import annotations

import json
import threading
import time

from loguru import logger

from . import client, paths

_CMDS_FILE = paths.DATA_DIR / "bilibili_cmds.json"
_SUBS_FILE = paths.DATA_DIR / "bilibili_cmd_subs.json"
_BINDS_FILE = paths.DATA_DIR / "bilibili_binds.json"

# bind 命令也走 CMD_DEFAULTS，但权限固定（群主/群管理员/AstrBot 管理员），不受页面开关影响
_CMD_KINDS = ("sub", "unsub", "bind")

# 命令触发配置（「命令」页可改）
CMD_DEFAULTS: dict = {
    "sub": {   # 订阅B站推送 <UID>
        "enabled": True,
        "triggers": ["订阅B站推送"],
        "admin_only": False,
        "desc": "订阅 UP 主的动态推送到本群",
    },
    "unsub": {  # 取消B站推送 [UID]
        "enabled": True,
        "triggers": ["取消B站推送"],
        "admin_only": False,
        "desc": "取消订阅（不带 UID = 取消本群全部）",
    },
    "bind": {   # 绑定B站推送
        "enabled": True,
        "triggers": ["绑定B站推送"],
        "admin_only": True,   # 权限固定为「群主/群管理员/AstrBot 管理员」（见 main._bind_allowed），页面不提供开关
        "desc": "绑定本群到 B 站推送服务：记录群 ID + 群名（仅群主/群管理员/AstrBot 管理员可用）",
    },
}

# 内置固定命令（@filter.command 静态注册，改不了触发词；「命令」页只读展示）
STATIC_COMMANDS = [
    {
        "name": "b站状态",
        "aliases": ["B站状态", "b站登录状态", "bilibili状态"],
        "admin_only": True,
        "desc": "查看 B 站登录状态与未读私信",
        "usage": "b站状态",
    },
    {
        "name": "b站私信",
        "aliases": ["B站私信", "bilibili私信"],
        "admin_only": True,
        "desc": "查看 B 站私信会话列表（最近 10 条）",
        "usage": "b站私信",
    },
    {
        "name": "b站动态",
        "aliases": ["B站动态", "bilibili动态"],
        "admin_only": True,
        "desc": "查看某 UP 主的最近 3 条动态",
        "usage": "b站动态 <UID>",
    },
]

_lock = threading.Lock()
_cmds: dict = {}   # {"sub": {...}, "unsub": {...}, "bind": {...}}
_recs: list[dict] = []
_binds: list[dict] = []   # 绑定的群：{id, group_id, group_name, platform, platform_id, bound_by, bound_at}


# ---------------- 落盘 ----------------


def _save_cmds() -> None:
    try:
        paths.data_dir()
        tmp = _CMDS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cmds, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(_CMDS_FILE)
    except OSError:
        logger.warning("bilibili 命令配置写盘失败")


def _save_recs() -> None:
    try:
        paths.data_dir()
        tmp = _SUBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_recs, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(_SUBS_FILE)
    except OSError:
        logger.warning("bilibili 命令订阅记录写盘失败")


def _save_binds() -> None:
    try:
        paths.data_dir()
        tmp = _BINDS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_binds, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(_BINDS_FILE)
    except OSError:
        logger.warning("bilibili 绑定群写盘失败")


def _load() -> None:
    global _cmds, _recs, _binds
    try:
        if _CMDS_FILE.exists():
            data = json.loads(_CMDS_FILE.read_text("utf-8"))
            if isinstance(data, dict):
                for kind in _CMD_KINDS:
                    d = data.get(kind)
                    if isinstance(d, dict):
                        base = dict(CMD_DEFAULTS[kind])
                        base.update(d)
                        _cmds[kind] = base
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"bilibili 命令配置读取失败：{exc}")
    try:
        if _SUBS_FILE.exists():
            data = json.loads(_SUBS_FILE.read_text("utf-8"))
            if isinstance(data, list):
                _recs = [r for r in data if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"bilibili 命令订阅记录读取失败：{exc}")
    try:
        if _BINDS_FILE.exists():
            data = json.loads(_BINDS_FILE.read_text("utf-8"))
            if isinstance(data, list):
                _binds = [r for r in data if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"bilibili 绑定群读取失败：{exc}")
    for kind in _CMD_KINDS:
        _cmds.setdefault(kind, dict(CMD_DEFAULTS[kind]))
    for kind in _CMD_KINDS:
        normalize_cmd(_cmds[kind])


# ---------------- 命令配置 ----------------


def normalize_cmd(c: dict) -> dict:
    """把单条命令配置夹回合法形态（triggers 去空去重、布尔强转）。"""
    triggers: list[str] = []
    for t in c.get("triggers") or []:
        t = str(t).strip()
        if t and t not in triggers:
            triggers.append(t)
    c["triggers"] = triggers
    c["enabled"] = bool(c.get("enabled"))
    c["admin_only"] = bool(c.get("admin_only"))
    c["desc"] = str(c.get("desc") or "")
    return c


def cfg() -> dict:
    """当前命令配置（深拷贝，给前端 / 命令匹配用）。"""
    with _lock:
        return json.loads(json.dumps(_cmds, ensure_ascii=False))


def save_cfg(body: dict) -> dict:
    """保存命令配置（只认 sub / unsub / bind 三把钥匙）。返回保存后的配置。"""
    with _lock:
        for kind in _CMD_KINDS:
            d = body.get(kind)
            if isinstance(d, dict):
                base = dict(_cmds.get(kind) or CMD_DEFAULTS[kind])
                base.update({k: d[k] for k in ("enabled", "triggers", "admin_only") if k in d})
                _cmds[kind] = normalize_cmd(base)
        _save_cmds()
        return json.loads(json.dumps(_cmds, ensure_ascii=False))


# ---------------- 订阅记录 ----------------


def all() -> list[dict]:
    with _lock:
        return [dict(r) for r in _recs]


def find(platform_id: str, group_id: str, bilibili_id: str) -> dict | None:
    pid, gid, bid = str(platform_id), str(group_id), str(bilibili_id)
    for r in _recs:
        if r.get("platform_id") == pid and r.get("group_id") == gid and str(r.get("bilibili_id")) == bid:
            return r
    return None


def add(
    qq_id: str,
    group_id: str,
    platform: str,
    platform_id: str,
    bilibili_id: str,
    uname: str = "",
) -> tuple[bool, str, dict | None]:
    """新增一条命令订阅（同群同 UP 主只保留一条，重复订阅=刷新订阅人）。"""
    qq_id = str(qq_id or "")
    group_id = str(group_id or "")
    platform = str(platform or "")
    platform_id = str(platform_id or "")
    bilibili_id = str(bilibili_id or "").strip()
    if not bilibili_id.isdigit():
        return False, "UP 主 UID 必须是纯数字", None
    if not group_id or not platform_id:
        return False, "拿不到群 ID 或平台实例，无法订阅（请确认是在群内发命令）", None
    with _lock:
        rec = find(platform_id, group_id, bilibili_id)
        if rec:
            rec["qq_id"] = qq_id or rec.get("qq_id", "")
            if uname:
                rec["uname"] = uname
            _save_recs()
            return True, "本群已订阅过该 UP 主（本次刷新了订阅人）", dict(rec)
        rid = f"cmd-{bilibili_id}-{platform_id}-{group_id}"
        rec = {
            "id": rid,
            "qq_id": qq_id,
            "group_id": group_id,
            "platform": platform,
            "platform_id": platform_id,
            "bilibili_id": bilibili_id,
            "uname": str(uname or ""),
            "created_at": int(time.time()),
        }
        _recs.append(rec)
        _save_recs()
        return True, "订阅成功", dict(rec)


def remove(rec_id: str) -> tuple[bool, str]:
    rec_id = str(rec_id)
    with _lock:
        n = len(_recs)
        _recs = [r for r in _recs if str(r.get("id")) != rec_id]
        if len(_recs) == n:
            return False, "记录不存在（可能已被删除）"
        _save_recs()
        return True, "已删除该订阅记录"


def remove_uid(platform_id: str, group_id: str, bilibili_id: str) -> tuple[bool, str]:
    """取消本群某个 UP 主的命令订阅。"""
    pid, gid, bid = str(platform_id), str(group_id), str(bilibili_id)
    with _lock:
        n = len(_recs)
        _recs = [
            r
            for r in _recs
            if not (
                r.get("platform_id") == pid
                and r.get("group_id") == gid
                and str(r.get("bilibili_id")) == bid
            )
        ]
        if len(_recs) == n:
            return False, "本群没有订阅过这个 UP 主（命令订阅）"
        _save_recs()
        return True, "已取消订阅"


def remove_group(platform_id: str, group_id: str) -> tuple[bool, str, int]:
    """取消本群全部命令订阅。"""
    pid, gid = str(platform_id), str(group_id)
    with _lock:
        before = len(_recs)
        _recs = [r for r in _recs if not (r.get("platform_id") == pid and r.get("group_id") == gid)]
        n = before - len(_recs)
        _save_recs()
    if not n:
        return False, "本群没有通过命令订阅的推送", 0
    return True, f"已取消本群 {n} 条命令订阅", n


def clear() -> int:
    """清空全部命令订阅记录（数据管理页用），返回清掉的条数。"""
    with _lock:
        n = len(_recs)
        _recs.clear()
        _save_recs()
        return n


# ---------------- 绑定的群（「绑定B站推送」命令落库） ----------------


def all_binds() -> list[dict]:
    with _lock:
        return [dict(r) for r in _binds]


def bind_add(
    platform_id: str,
    group_id: str,
    platform: str,
    group_name: str = "",
    qq_id: str = "",
) -> tuple[bool, str]:
    """绑定一个群：持久化群 ID + 群名。同平台同群只保留一条（重复绑定 = 刷新群名）。"""
    pid, gid = str(platform_id or ""), str(group_id or "")
    group_name = str(group_name or "").strip()
    if not gid or not pid:
        return False, "拿不到群 ID 或平台实例，无法绑定（请确认是在群内发命令）"
    with _lock:
        for b in _binds:
            if b.get("platform_id") == pid and str(b.get("group_id")) == gid:
                changed = False
                if group_name and b.get("group_name") != group_name:
                    b["group_name"] = group_name
                    changed = True
                if qq_id and not b.get("bound_by"):
                    b["bound_by"] = str(qq_id)
                    changed = True
                if changed:
                    _save_binds()
                return True, "本群已绑定过（已刷新群名）"
        _binds.append(
            {
                "id": f"bind-{pid}-{gid}",
                "group_id": gid,
                "group_name": group_name,
                "platform": str(platform or ""),
                "platform_id": pid,
                "bound_by": str(qq_id or ""),
                "bound_at": int(time.time()),
            }
        )
        _save_binds()
        return True, "绑定成功"


def bind_remove(bind_id: str) -> tuple[bool, str]:
    bind_id = str(bind_id)
    with _lock:
        n = len(_binds)
        _binds = [b for b in _binds if str(b.get("id")) != bind_id]
        if len(_binds) == n:
            return False, "绑定记录不存在（可能已被删除）"
        _save_binds()
        return True, "已删除该绑定记录"


def binds_clear() -> int:
    """清空全部绑定记录（数据管理页用），返回清掉的条数。"""
    with _lock:
        n = len(_binds)
        _binds.clear()
        _save_binds()
        return n


def targets() -> list[dict]:
    """整形成 pusher 认识的「伪订阅」：全类型、无关键词过滤。

    id 即 pusher 去重 state 的 key（cmd- 前缀与面板订阅的 id 不会撞），
    首轮自动只记基线不推历史动态，与面板订阅行为一致。
    """
    out = []
    for r in all():
        out.append(
            {
                "id": str(r.get("id") or ""),
                "mid": str(r.get("bilibili_id") or ""),
                "uname": r.get("uname") or f"UID {r.get('bilibili_id')}",
                "platform_id": str(r.get("platform_id") or ""),
                "group_id": str(r.get("group_id") or ""),
                "group_name": f"群 {r.get('group_id')}（命令订阅）",
                "types": list(client.DYN_KINDS),
                "keyword": "",
            }
        )
    return out


_load()
