"""B 站动态订阅推送：定时逐个拉取订阅 UP 主的动态，推到指定群。

设计要点（都是为了避免风控 / 刷屏）：
- **间隔**：基准 `interval_minutes`（≥30，默认 120），每次实际等待再加 ±`jitter_minutes`
  的随机浮动，所以「1 小时」就是「1 小时左右」，不会每次都在同一秒触发。
- **每个请求都要有间隔**：不是「两个订阅之间等一下」，而是**每一次 B 站请求**
  发出前都要先排队，保证距上一次请求 ≥ `request_gap_seconds × U(0.5, 1.5)` 秒
  （默认 2 秒左右）。节流做在 `client._throttle()`，所以不管有多少订阅、也不管
  一个订阅内部翻了多少页 / 重试了几次，请求一律串行，绝不并发轰炸。
- **防重复**：每个订阅记录已推过的动态 id 与最大发布时间；新订阅第一次只做
  「基线记录」，不推送历史动态。
- **过期不推**：动态发布时间距今超过 `max_age_hours`（默认 24，0=不限）直接丢弃。
- **内容过滤**：订阅可选填 `keyword`（正文需包含的关键词，多个用逗号/换行分隔，
  命中任意一个即推）。填了以后正文不含关键词的动态直接跳过（跳过的不补推）。

配置落盘 `data/bilibili_push.json`，推送记录落盘 `data/bilibili_push_state.json`。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import threading
import time

from loguru import logger

from . import client, paths, sender, subs

_SETTINGS_FILE = paths.DATA_DIR / "bilibili_push.json"
_STATE_FILE = paths.DATA_DIR / "bilibili_push_state.json"

# 取值范围（后端强制夹取，前端怎么填都不会出格）
MIN_INTERVAL = 30        # 分钟：每次拉取间隔不得小于 30 分钟
MAX_INTERVAL = 1440      # 分钟：最多 24 小时
MAX_JITTER = 120         # 分钟
MAX_AGE_LIMIT = 720      # 小时：30 天
MAX_GAP = 60             # 秒

# 推送形态：
#   auto —— 文字 + 封面图（push_images 控制图）
#   text —— 一律纯文字
PUSH_MODES = ("auto", "text")
MODE_LABELS = {"auto": "文字 + 图片", "text": "纯文字"}

DEFAULTS = {
    "enabled": False,            # 默认关闭，需要用户在页面开启
    "interval_minutes": 120,     # 基准拉取间隔
    "jitter_minutes": 15,        # 随机浮动 ±（分钟）
    "max_age_hours": 24,         # 发布超过 N 小时的动态不推送（0 = 不限）
    "request_gap_seconds": 2,    # 请求间隔基准（秒），实际 × U(0.5,1.5)
    "push_images": True,         # 文字 + 封面图
    "mode": "auto",
}

_lock = threading.Lock()
_settings: dict = dict(DEFAULTS)
_state: dict = {"pushed": {}}          # {sub_id: {"last_ts": int, "ids": [...]}}
_runtime: dict = {
    "running": False,
    "last_run": 0,
    "next_run": 0,
    "last_pushed": 0,
    "last_checked": 0,
    "last_error": "",
}
_IDS_KEEP = 200
MAX_PUSH_PER_CYCLE = 5    # 单个订阅一轮最多推几条（防刷屏，重置重推时也生效）


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
            if isinstance(data, dict) and isinstance(data.get("pushed"), dict):
                _state["pushed"] = data["pushed"]
    except (OSError, json.JSONDecodeError):
        pass
    normalize_settings()
    _apply_gap()


# ---------------- 设置 ----------------


def _clamp_int(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def normalize_settings() -> None:
    """把设置夹回合法范围（间隔不得小于 30 分钟）。"""
    _settings["interval_minutes"] = _clamp_int(
        _settings.get("interval_minutes"), MIN_INTERVAL, MAX_INTERVAL, DEFAULTS["interval_minutes"]
    )
    _settings["jitter_minutes"] = _clamp_int(
        _settings.get("jitter_minutes"), 0, MAX_JITTER, DEFAULTS["jitter_minutes"]
    )
    _settings["max_age_hours"] = _clamp_int(
        _settings.get("max_age_hours"), 0, MAX_AGE_LIMIT, DEFAULTS["max_age_hours"]
    )
    _settings["request_gap_seconds"] = _clamp_int(
        _settings.get("request_gap_seconds"), 0, MAX_GAP, DEFAULTS["request_gap_seconds"]
    )
    _settings["enabled"] = bool(_settings.get("enabled"))
    _settings["push_images"] = bool(_settings.get("push_images", True))
    mode = str(_settings.get("mode") or "").strip().lower()
    if mode not in PUSH_MODES:
        mode = "auto"
    _settings["mode"] = mode


def settings() -> dict:
    with _lock:
        return dict(_settings)


def update(**kwargs) -> dict:
    """保存设置（自动夹取范围），并据此重算下次运行时间。"""
    global _settings
    with _lock:
        for k, v in kwargs.items():
            if k in DEFAULTS:
                _settings[k] = v
        normalize_settings()
        _save_settings()
        _apply_gap()
        _runtime["next_run"] = int(time.time() + next_delay())
        return dict(_settings)


def _apply_gap() -> None:
    """把「请求间隔」下发给 client（每次请求都会按它排队）。"""
    client.set_request_gap(_settings.get("request_gap_seconds", DEFAULTS["request_gap_seconds"]))


def next_delay() -> int:
    """下一次等待秒数 = 基准间隔 ± 随机浮动（不小于 30 分钟）。"""
    st = _settings
    base = st.get("interval_minutes", DEFAULTS["interval_minutes"]) * 60
    jitter = st.get("jitter_minutes", 0) * 60
    delay = base + random.uniform(-jitter, jitter) if jitter else base
    return int(max(MIN_INTERVAL * 60, delay))


def request_gap() -> float:
    """当前「每个请求之间」的间隔基准（秒）；实际等待在 client._throttle() 里随机化。"""
    return float(_settings.get("request_gap_seconds", DEFAULTS["request_gap_seconds"]))


def status() -> dict:
    with _lock:
        return {**_settings, **_runtime}


# ---------------- 去重 ----------------


def _key(item: dict) -> str:
    dyn_id = str(item.get("id") or "").strip()
    if dyn_id:
        return dyn_id
    return f"{item.get('mid') or 0}-{item.get('ts') or 0}"


def _expired(item: dict) -> bool:
    """超过 max_age_hours 发布的动态不推送。"""
    hours = _settings.get("max_age_hours", DEFAULTS["max_age_hours"])
    if not hours:
        return False
    ts = int(item.get("ts") or 0)
    if not ts:  # 拿不到发布时间的按不处理，避免误判成很老的动态
        return False
    return (time.time() - ts) > hours * 3600


def _new_items(sub_id: str, items: list[dict]) -> list[dict]:
    """筛出没推过、且没过期的新动态（按发布时间正序）。"""
    rec = _state["pushed"].get(sub_id) or {}
    seen = set(rec.get("ids") or [])
    last_ts = int(rec.get("last_ts") or 0)
    out = []
    for it in items:
        if _key(it) in seen:
            continue
        ts = int(it.get("ts") or 0)
        if last_ts and ts and ts < last_ts:
            continue
        if _expired(it):
            continue
        out.append(it)
    out.sort(key=lambda x: int(x.get("ts") or 0))
    return out


def _mark(sub_id: str, items: list[dict]) -> None:
    """把这一批动态记为已处理（无论是否推送，避免重复判断）。"""
    rec = _state["pushed"].setdefault(sub_id, {"last_ts": 0, "ids": []})
    ids = list(rec.get("ids") or [])
    last_ts = int(rec.get("last_ts") or 0)
    for it in items:
        k = _key(it)
        if k and k not in ids:
            ids.append(k)
        ts = int(it.get("ts") or 0)
        if ts > last_ts:
            last_ts = ts
    rec["ids"] = ids[-_IDS_KEEP:]
    rec["last_ts"] = last_ts


def forget(sub_id: str) -> None:
    """删除订阅时一并清掉它的推送记录。"""
    _state["pushed"].pop(str(sub_id), None)
    _save_state()


# ---------------- 推送 ----------------


def _kind_ok(item: dict, kinds: set[str]) -> bool:
    """订阅只推指定类型的动态；识别不出类型时放行，避免误杀漏推。"""
    k = str(item.get("kind") or "").strip().lower()
    if k not in client.DYN_KINDS:
        return True
    return k in kinds


# 「图文需包含」**只对这几类动态生效**（kind 见 client.DYN_KINDS）。
# 过滤词只管「图文」，别的类型（投稿/专栏/影视/相册/直播/文字）照常全推。
KEYWORD_KINDS = ("opus",)


def _keyword_ok(item: dict, keywords: list[str]) -> bool:
    """图文内容过滤：**只作用于图文动态**，其它类型一律放行。

    - `keywords` 为空 = 不过滤（默认，老订阅行为不变）；
    - `item["kind"]` 不在 `KEYWORD_KINDS`（图文）→ 直接放行；
    - 命中范围 = `client._dyn_brief` 拼好的 `search_text`（标题 + 正文；图文的
      `desc.text` 与 `opus.summary.text` 两处正文都在里面），老数据没有这个字段时
      退回「标题 + text」；
    - 大小写不敏感；多个关键词是**或**关系（命中任意一个即推送）。
    """
    if not keywords:
        return True
    if str(item.get("kind") or "").strip().lower() not in KEYWORD_KINDS:
        return True
    hay = str(
        item.get("search_text")
        or f"{item.get('title') or ''}\n{item.get('text') or ''}"
    ).casefold()
    return any(k.casefold() in hay for k in keywords)


def _format(item: dict, sub: dict) -> str:
    label = str(item.get("kind_label") or "").strip()
    lines = [f"【B站{label or '动态'}】{sub.get('uname') or item.get('author') or ('UID ' + str(sub.get('mid')))}"]
    when = item.get("pub_time") or ""
    if when:
        lines.append(f"时间：{when}")
    title = (item.get("title") or "").strip()
    text = (item.get("text") or "").strip()
    body = title or text
    if title and text and text != title:
        body = f"{title}\n{text}"
    if len(body) > 300:
        body = body[:300] + "…"
    if body:
        lines.append(body)
    if item.get("url"):
        lines.append(str(item["url"]))
    return "\n".join(lines)


def _cover(item: dict) -> str:
    """取动态封面/首图（B 站 CDN 直链；下载由接收端负责）。"""
    for img in item.get("images") or []:
        url = str(img or "").strip()
        if url:
            return url
    return ""


async def _send(sub: dict, item: dict) -> None:
    """按订阅配置推一条：纯文字，或文字 + 封面图。"""
    mode = str(_settings.get("mode") or "auto").strip().lower()
    text = _format(item, sub)
    cover = ""
    if mode != "text" and _settings.get("push_images", True):
        cover = _cover(item)
    await sender.send_group(
        platform_id=str(sub.get("platform_id") or ""),
        group_id=str(sub.get("group_id") or ""),
        text=text,
        cover=cover,
    )


async def run_cycle(force: bool = False, reset: bool = False) -> dict:
    """跑一轮：逐个订阅拉动态并推送。

    force=True 时忽略总开关（用于页面「立即执行」按钮）。
    reset=True 时先清掉已推记录，本轮会把**未过期**（≤ max_age_hours）的动态
    重新推一遍 —— 用于首次订阅后想补推最近动态，或排查「为什么不推」。
    """
    st = settings()
    if not st["enabled"] and not force:
        return {"ok": False, "message": "定时推送未启用", "checked": 0, "pushed": 0}
    targets = subs.enabled()
    summary = {"ok": True, "checked": 0, "pushed": 0, "baselined": 0,
               "reset": bool(reset), "errors": [], "details": []}
    if not targets:
        summary["message"] = "没有启用的订阅"
        return summary

    _runtime["running"] = True
    _runtime["last_error"] = ""
    try:
        for s in targets:
            sid = str(s.get("id") or "")
            name = s.get("uname") or str(s.get("mid") or "")
            kinds = set(client.normalize_kinds(s.get("types")))
            keywords = client.split_keywords(s.get("keyword"))
            detail = {
                "uname": name,
                "group": f"{s.get('platform_id')}:{s.get('group_name') or s.get('group_id') or ''}",
                "items": 0, "pushed": 0, "skipped": 0, "baselined": False,
                "kinds": sorted(kinds), "keywords": keywords, "error": "",
            }
            try:
                data = await client.dynamics(str(s.get("mid") or ""))
                items = list(data.get("items") or [])
                detail["items"] = len(items)
                first = sid not in _state["pushed"]
                if reset:
                    # 清掉该订阅的已推记录：本轮按「有新动态就推」处理
                    # （仍然过 max_age_hours，太老的照样不推）
                    _state["pushed"].pop(sid, None)
                    first = False
                if first:
                    # 新订阅第一次只记录基线，不把历史动态全推一遍
                    _mark(sid, items)
                    summary["baselined"] += 1
                    detail["baselined"] = True
                else:
                    new_items = _new_items(sid, items)
                    # 只保留订阅勾选的类型；被过滤掉的也照样标记，避免下次重复判断
                    by_kind = [it for it in new_items if _kind_ok(it, kinds)]
                    # 再做「内容过滤」：只推正文含关键词的动态（没填关键词则全部通过）
                    fresh = [it for it in by_kind if _keyword_ok(it, keywords)]
                    detail["skipped"] = len(new_items) - len(fresh)
                    detail["skipped_kind"] = len(new_items) - len(by_kind)
                    detail["skipped_keyword"] = len(by_kind) - len(fresh)
                    if len(fresh) > MAX_PUSH_PER_CYCLE:
                        detail["error"] = (
                            f"新动态 {len(fresh)} 条，本轮只推最新 {MAX_PUSH_PER_CYCLE} 条"
                        )
                        fresh = fresh[-MAX_PUSH_PER_CYCLE:]
                    for item in fresh:
                        try:
                            await _send(s, item)
                            summary["pushed"] += 1
                            detail["pushed"] += 1
                        except Exception as exc:
                            msg = f"{name} → 群 {s.get('group_id')}：{exc}"
                            summary["errors"].append(msg)
                            detail["error"] = str(exc)
                    _mark(sid, items)
                summary["checked"] += 1
            except Exception as exc:
                logger.warning(f"B 站订阅推送失败（{name}）：{exc}")
                summary["errors"].append(f"{name}：{exc}")
                detail["error"] = str(exc)
            summary["details"].append(detail)
            # 注意：这里不再手动 sleep —— 「请求间隔」由 client._throttle() 在
            # 每个请求发出前统一排队（不管多少订阅，每个请求之间都有间隔），
            # 所以一个订阅内部若翻页/重试，每次请求同样会被隔开。
    finally:
        _runtime["running"] = False
        _runtime["last_run"] = int(time.time())
        _runtime["last_pushed"] = summary["pushed"]
        _runtime["last_checked"] = summary["checked"]
        _runtime["last_error"] = "；".join(summary["errors"][:3])
        _save_state()

    parts = [f"已检查 {summary['checked']} 个订阅，推送 {summary['pushed']} 条"]
    if summary["baselined"]:
        parts.append(f"{summary['baselined']} 个订阅是首次运行，只建立基线（未推送历史动态）")
    if summary["errors"]:
        parts.append(f"{len(summary['errors'])} 个失败")
    if not summary["pushed"] and summary["baselined"] and not reset:
        parts.append("要把最近未过期的动态补推一遍，请点「重置基线并重推」")
    if not summary["pushed"] and not summary["baselined"] and not summary["errors"]:
        parts.append(f"没有新动态（或都早于 {st.get('max_age_hours')} 小时的过期阈值）")
    summary["message"] = "，".join(parts)
    logger.info(f"B 站订阅推送：{summary['message']}")
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
            logger.exception("B 站订阅推送循环异常")
            _runtime["last_error"] = str(exc)
            _runtime["next_run"] = int(time.time() + 300)
        await asyncio.sleep(5)


# 读盘放在文件末尾：_load() 里要调 normalize_settings()，
# 必须等本模块所有函数都定义完才能执行（否则 import 阶段就 NameError）。
_load()
