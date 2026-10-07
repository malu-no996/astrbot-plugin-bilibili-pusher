"""页面数据的后端缓存（进页签自动读缓存，手动点「刷新」才重新请求 B 站）。

原项目把关注列表缓存在浏览器 localStorage 里；AstrBot 的 Page 跑在受限 iframe
里，localStorage 可能被浏览器禁掉，所以挪到后端：落 <插件>/data/page_cache.json。

- 只缓存「列表类」接口的第一页（offset 为空时）：私信会话 / 动态流 / 空间动态 /
  关注列表 / 关注分组；翻页、搜索、offset 加载更多都直接请求 B 站。
- 缓存**不设 TTL 自动过期**：只有手动刷新（force=1）或退出/重新登录才清空，
  否则进页面永远显示缓存，避免每次切页签都打 B 站接口。
- 写盘用原子替换（tmp + rename），坏了/读不出来就当没缓存，不影响正常拉取。
"""
from __future__ import annotations

import json
import time

from . import paths

_FILE = paths.DATA_DIR / "page_cache.json"

_data: dict = {}      # key -> {"ts": int, "data": ...}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        raw = json.loads(_FILE.read_text("utf-8"))
        if isinstance(raw, dict):
            _data.update(raw)
    except Exception:
        pass  # 没有缓存 / 文件坏了：当没缓存


def _save() -> None:
    try:
        paths.data_dir()
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, ensure_ascii=False), "utf-8")
        tmp.replace(_FILE)
    except Exception:
        pass  # 缓存写失败不影响正常功能


def get(key: str):
    """取缓存；没有返回 None。"""
    _load()
    item = _data.get(key)
    return item.get("data") if isinstance(item, dict) else None


def put(key: str, data) -> None:
    _load()
    _data[key] = {"ts": int(time.time()), "data": data}
    _save()


def cached(key: str) -> bool:
    _load()
    return key in _data


def size() -> int:
    """当前缓存条数（数据管理页展示用）。"""
    _load()
    return len(_data)


def clear() -> None:
    """清空全部页面缓存（退出/重新登录时换账号了，必须清）。"""
    _load()
    if _data:
        _data.clear()
        _save()
