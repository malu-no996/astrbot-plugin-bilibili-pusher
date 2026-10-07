"""B 站直播订阅推送：定时检查订阅主播是否在直播，在**开播 / 下播**时推到指定群。

与 `pusher.py`（动态推送）的区别：
- 动态推送是「拉 UP 主的动态列表 → 找新的条目」，一条动态只推一次（id 去重）；
- 直播推送是「拉主播的直播状态 → 和上次状态比对」，**只在状态跳变时推**
  （未开播→直播中 = 开播；直播中→未开播 = 下播），所以天然不会重复推。

设计要点（同样为了避免风控 / 刷屏）：
- **间隔**：基准 `interval_minutes`（≥5，默认 10），再加 ±`jitter_minutes` 随机浮动。
  直播状态变化比较及时才有用，所以允许的间隔下限比动态推送（30 分钟）小得多。
- **请求间隔**：复用 `client._throttle()`（由 `pusher.update()` 下发的 request_gap_seconds
  决定），所以每个 B 站请求仍然串行排队。
- **首次只建基线**：新订阅第一次检查只记录当前状态，不推「正在直播」的历史事件。
- **批量查状态**：一轮里把所有订阅的主播 UID 去重后走批量接口，每 50 个一次请求。

配置落盘 `data/bilibili_live_push.json`，状态落盘 `data/bilibili_live_state.json`。
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time

from loguru import logger

from . import client, live_subs, mdgen, paths, pusher, sender

_SETTINGS_FILE = paths.DATA_DIR / "bilibili_live_push.json"
_STATE_FILE = paths.DATA_DIR / "bilibili_live_state.json"

# 取值范围（后端强制夹取）。直播要「及时」，所以间隔下限比动态推送小很多。
MIN_INTERVAL = 5           # 分钟
MAX_INTERVAL = 1440        # 分钟
MAX_JITTER = 60            # 分钟
MAX_GAP = 60               # 秒

DEFAULTS = {
    "enabled": False,            # 默认关闭，需要用户在页面开启
    "interval_minutes": 10,      # 基准检查间隔
    "jitter_minutes": 3,         # 随机浮动 ±（分钟）
    "request_gap_seconds": 2,    # 请求间隔基准（秒），实际 × U(0.5,1.5)
    "push_images": True,         # 文字 + 封面图
    "mode": "auto",
}

_lock = threading.Lock()
_settings: dict = dict(DEFAULTS)
# {sub_id: {"live": bool, "title": str, "room_id": int, "since": int, "ts": int}}
_state: dict = {"rooms": {}}
_runtime: dict = {
    "running": False,
    "last_run": 0,
    "next_run": 0,
    "last_pushed": 0,
    "last_checked": 0,
    "last_error": "",
}


# ---------------- 落盘 ----------------


def _save_settings() -> None:
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(
            json.dumps(_settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _save_state() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _load() -> None:
    global _settings, _state
    try:
        if _SETTINGS_FILE.exists():
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _settings.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, json.JSONDecodeError):
        pass
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("rooms"), dict):
                _state["rooms"] = data["rooms"]
    except (OSError, json.JSONDecodeError):
        pass
    normalize_settings()


# ---------------- 设置 ----------------


def _clamp_int(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def normalize_settings() -> None:
    """把设置夹回合法范围（间隔不得小于 5 分钟）。"""
    _settings["interval_minutes"] = _clamp_int(
        _settings.get("interval_minutes"), MIN_INTERVAL, MAX_INTERVAL, DEFAULTS["interval_minutes"]
    )
    _settings["jitter_minutes"] = _clamp_int(
        _settings.get("jitter_minutes"), 0, MAX_JITTER, DEFAULTS["jitter_minutes"]
    )
    _settings["request_gap_seconds"] = _clamp_int(
        _settings.get("request_gap_seconds"), 0, MAX_GAP, DEFAULTS["request_gap_seconds"]
    )
    _settings["enabled"] = bool(_settings.get("enabled"))
    _settings["push_images"] = bool(_settings.get("push_images", True))
    mode = str(_settings.get("mode") or "").strip().lower()
    if mode not in pusher.PUSH_MODES:
        mode = "auto"
    _settings["mode"] = mode


def settings() -> dict:
    with _lock:
        return dict(_settings)


def update(**kwargs) -> dict:
    """保存设置（自动夹取范围），并据此重算下次运行时间。"""
    with _lock:
        for k, v in kwargs.items():
            if k in DEFAULTS:
                _settings[k] = v
        normalize_settings()
        _save_settings()
        _runtime["next_run"] = int(time.time() + next_delay())
        return dict(_settings)


def next_delay() -> int:
    """下一次等待秒数 = 基准间隔 ± 随机浮动（不小于 5 分钟）。"""
    st = _settings
    base = st.get("interval_minutes", DEFAULTS["interval_minutes"]) * 60
    jitter = st.get("jitter_minutes", 0) * 60
    delay = base + random.uniform(-jitter, jitter) if jitter else base
    return int(max(MIN_INTERVAL * 60, delay))


def status() -> dict:
    with _lock:
        return {**_settings, **_runtime}


def forget(sub_id: str) -> None:
    """删除订阅时一并清掉它的状态记录。"""
    _state["rooms"].pop(str(sub_id), None)
    _save_state()


# ---------------- 推送文案 ----------------


def _sub_host(sub: dict) -> str:
    return str(sub.get("uname") or f"UID {sub.get('mid')}")


def format_live(sub: dict, room: dict) -> str:
    """开播文案。"""
    lines = [f"【B站直播】{_sub_host(sub)} 开播了"]
    title = str(room.get("title") or "").strip()
    if title:
        lines.append(f"标题：{title}")
    area = str(room.get("area_name") or "").strip()
    if area:
        lines.append(f"分区：{area}")
    url = str(room.get("url") or sub.get("room_url") or "").strip()
    if url:
        lines.append(url)
    return "\n".join(lines)


def format_offline(sub: dict, room: dict) -> str:
    """下播文案；有开播时长就一并带上。"""
    lines = [f"【B站直播】{_sub_host(sub)} 下播了"]
    title = str(room.get("title") or "").strip()
    if title:
        lines.append(f"直播标题：{title}")
    started = int(room.get("live_start_time") or 0)
    if started:
        span = int(time.time()) - started
        if 0 < span < 7 * 24 * 3600:
            h, m = span // 3600, (span % 3600) // 60
            lines.append(f"本场时长：{h} 小时 {m} 分钟" if h else f"本场时长：{m} 分钟")
    url = str(room.get("url") or sub.get("room_url") or "").strip()
    if url:
        lines.append(url)
    return "\n".join(lines)


async def _send(sub: dict, room: dict, online: bool) -> None:
    """按订阅配置推一条开播/下播通知。"""
    text = format_live(sub, room) if online else format_offline(sub, room)
    cover = ""
    if str(_settings.get("mode") or "auto") != "text" and _settings.get("push_images", True):
        cover = str(room.get("cover") or "")
    await sender.send_group(
        platform_id=str(sub.get("platform_id") or ""),
        group_id=str(sub.get("group_id") or ""),
        text=text,
        cover=cover,
        markdown=mdgen.live_markdown(sub, room, online),
    )


# ---------------- 主循环体 ----------------


def _room_state(sub_id: str) -> dict | None:
    rec = _state["rooms"].get(sub_id)
    return rec if isinstance(rec, dict) else None


def _set_room_state(sub_id: str, room: dict) -> None:
    prev = _room_state(sub_id) or {}
    now = int(time.time())
    live = bool(room.get("live"))
    since = int(prev.get("since") or 0)
    if bool(prev.get("live")) != live or not since:
        since = now
    _state["rooms"][sub_id] = {
        "live": live,
        "title": str(room.get("title") or ""),
        "room_id": int(room.get("room_id") or 0),
        "since": since,
        "ts": now,
    }


async def run_cycle(force: bool = False, reset: bool = False) -> dict:
    """跑一轮：把订阅的主播直播状态批量查一遍，状态跳变就推送。

    force=True 时忽略总开关（页面「立即执行」按钮）。
    reset=True 时先清掉状态记录，本轮只重建基线（不会补推历史开播事件）。
    """
    st = settings()
    if not st["enabled"] and not force:
        return {"ok": False, "message": "直播定时推送未启用", "checked": 0, "pushed": 0}
    targets = live_subs.enabled()
    summary = {
        "ok": True, "checked": 0, "pushed": 0, "baselined": 0, "live_count": 0,
        "reset": bool(reset), "errors": [], "details": [],
    }
    if not targets:
        summary["message"] = "没有启用的直播订阅"
        return summary

    if reset:
        for s in targets:
            _state["rooms"].pop(str(s.get("id") or ""), None)

    _runtime["running"] = True
    _runtime["last_error"] = ""
    try:
        # 一轮把所有主播 UID 去重后批量查状态（每 50 个一次请求）
        uids: list[str] = []
        for s in targets:
            mid = str(s.get("mid") or "").strip()
            if mid and mid not in uids:
                uids.append(mid)
        statuses: dict[str, dict] = {}
        if uids:
            try:
                statuses = await client.live_statuses(uids)
            except Exception as exc:
                logger.warning(f"B 站直播状态批量查询失败：{exc}")
                summary["errors"].append(f"直播状态查询：{exc}")
                _runtime["last_error"] = str(exc)

        for s in targets:
            sid = str(s.get("id") or "")
            name = _sub_host(s)
            mid = str(s.get("mid") or "")
            detail = {
                "uname": name,
                "group": f"{s.get('platform_id')}:{s.get('group_name') or s.get('group_id') or ''}",
                "title": "", "live": False, "pushed": 0, "baselined": False,
                "event": "", "error": "",
            }
            try:
                room = statuses.get(mid)
                if room is None:
                    # 批量接口没返回该 UID（未关注/被封）：退回单查房间信息
                    room = await client.live_status(mid)
                room = dict(room or {})
                room["uname"] = room.get("uname") or s.get("uname") or ""
                detail["title"] = str(room.get("title") or "")
                detail["live"] = bool(room.get("live"))
                if detail["live"]:
                    summary["live_count"] += 1

                prev = _room_state(sid)
                first = prev is None
                if first:
                    # 新订阅第一次只记基线，不推「正在直播」的历史事件
                    _set_room_state(sid, room)
                    summary["baselined"] += 1
                    detail["baselined"] = True
                else:
                    was_live = bool(prev.get("live"))
                    now_live = bool(room.get("live"))
                    # 下播时接口不一定还带标题，用上次记的补上
                    if not room.get("title"):
                        room["title"] = str(prev.get("title") or "")
                    if not now_live and prev.get("since"):
                        room["live_start_time"] = int(prev.get("since") or 0)
                    if now_live != was_live:
                        want = bool(s.get("notify_live", True)) if now_live else bool(
                            s.get("notify_offline", True)
                        )
                        detail["event"] = "开播" if now_live else "下播"
                        if want:
                            try:
                                await _send(s, room, now_live)
                                summary["pushed"] += 1
                                detail["pushed"] = 1
                            except Exception as exc:
                                msg = f"{name} → 群 {s.get('group_id')}：{exc}"
                                summary["errors"].append(msg)
                                detail["error"] = str(exc)
                        else:
                            detail["event"] += "（该事件已关闭通知）"
                    _set_room_state(sid, room)
                summary["checked"] += 1
            except Exception as exc:
                logger.warning(f"B 站直播订阅检查失败（{name}）：{exc}")
                summary["errors"].append(f"{name}：{exc}")
                detail["error"] = str(exc)
            summary["details"].append(detail)
    finally:
        _runtime["running"] = False
        _runtime["last_run"] = int(time.time())
        _runtime["last_pushed"] = summary["pushed"]
        _runtime["last_checked"] = summary["checked"]
        _runtime["last_error"] = "；".join(summary["errors"][:3])
        _save_state()

    parts = [f"已检查 {summary['checked']} 个主播，推送 {summary['pushed']} 条"]
    if summary["live_count"]:
        parts.append(f"其中 {summary['live_count']} 个正在直播")
    if summary["baselined"]:
        parts.append(f"{summary['baselined']} 个订阅是首次运行，只建立基线")
    if summary["errors"]:
        parts.append(f"{len(summary['errors'])} 个失败")
    if not summary["pushed"] and not summary["baselined"] and not summary["errors"]:
        parts.append("状态没有变化（没有开播/下播）")
    summary["message"] = "，".join(parts)
    logger.info(f"B 站直播推送：{summary['message']}")
    return summary


async def loop_forever() -> None:
    """后台循环：到点就跑一轮，5 秒检查一次（改设置能立刻生效）。"""
    while True:
        try:
            now = time.time()
            if not _runtime["next_run"]:
                _runtime["next_run"] = int(now + 60)
            if now >= _runtime["next_run"]:
                if _settings.get("enabled"):
                    await run_cycle()
                _runtime["next_run"] = int(time.time() + next_delay())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("B 站直播推送循环异常")
            _runtime["last_error"] = str(exc)
            _runtime["next_run"] = int(time.time() + 300)
        await asyncio.sleep(5)


# 读盘放在文件末尾：_load() 里要调 normalize_settings()，
# 必须等本模块所有函数都定义完才能执行（否则 import 阶段就 NameError）。
_load()
