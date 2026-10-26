"""QQ 官方机器人的**主动推送**通道：直连 botpy，必要时补一下场景缓存。

为什么要有这个模块
--------------------
AstrBot 的 QQ 官方适配器在 `send_by_session()` 里有这么一段（v4.28.x，
`qqofficial_platform_adapter.py` 约 L384）::

    msg_id = self._session_last_message_id.get(session.session_id)
    scene  = self._session_scene.get(session.session_id)
    allow_group_proactive_send = (
        session.message_type == MessageType.GROUP_MESSAGE
        and scene == "group"
        and getattr(self, "_allow_group_proactive_send", False)   # 源码里写死 True
    )
    if (not msg_id
            and session.message_type != MessageType.FRIEND_MESSAGE
            and not allow_group_proactive_send):
        logger.warning("[QQOfficial] No cached msg_id for session: %s, skip send_by_session",
                       session.session_id)
        return                                    # ← 一条不发，只在日志留一行 WARN

`_session_last_message_id` 与 `_session_scene` 都是**纯进程内存的 dict**，
只在「机器人收到消息」时才写入。于是：

  · 机器人刚启动、目标群还没人说话 → 两个缓存都空 → **定时推送被静默丢弃**；
  · 群里有人发一句话之后          → 缓存填上   → 推送立刻恢复正常。

这就是「跑一段时间 / 重启后推送发不出去，群里说句话又好了」的真正原因 ——
**不是连接泄漏**：同一时刻同一进程里，别的会话照样发得出去。

更麻烦的是：被 skip 时 `send_by_session` 走的是 `return`（返回 `None`），
**不是**返回 `False`。所以插件里「`ok is False` 才算失败」的判断永远不会命中，
推送丢了还以为发成功了。

两条出路（本模块都提供，逐级降级）
----------------------------------
① **直连 botpy**（首选）：直接用底层 `client.api.post_group_message` 发，
   完全绕开那两个内存缓存。QQ 官方适配器本身就持有 botpy 的 Client
   （`self.client`，`self.client.api` 就是它），外语学习插件已实测可用。
   · 文字：`msg_type=2`（markdown）/ `msg_type=0`（纯文本）
   · 图片：先 `POST /v2/groups/{gid}/files` 上传换 `file_info`，再 `msg_type=7` 富媒体

② **补场景缓存**（拿不到 botpy 时的后手）：调适配器的公有方法
   `remember_session_scene(group_openid, "group")`，把 `scene` 补成 "group"
   —— 之后照旧走 `context.send_message()`，适配器自带的 markdown 降级 /
   图片上传 / 消息拆分逻辑全部保留。
   ⚠️ 这是**非文档化**方法，astrbot 升级可能改名或消失，所以必须
   try/except 包住，且只能当兜底，不能当主力。
"""
from __future__ import annotations

import base64 as b64
import random
from pathlib import Path

from loguru import logger

OFFICIAL_PREFIX = "qq_official"
_context = None


# ---------------- 绑定与实例查找 ----------------


def bind(context) -> None:
    """插件初始化时把 AstrBot Context 挂进来（main.py 里调 sender.bind 会顺带调这里）。"""
    global _context
    _context = context


def _insts() -> list:
    if _context is None:
        return []
    try:
        return list(_context.platform_manager.get_insts() or [])
    except Exception:      # noqa: BLE001 —— 未绑定 / 版本差异都不该炸
        return []


def _inst_id(p) -> str:
    """平台实例 ID。⚠️ Platform 实例没有 .id 属性（getattr 恒空），真实 ID 在 meta().id。"""
    try:
        return str(p.meta().id or "") or str((getattr(p, "config", None) or {}).get("id") or "")
    except Exception:       # noqa: BLE001
        return ""


def platform_of(platform_id: str):
    """按实例 ID 找平台对象；没有返回 None。"""
    pid = str(platform_id or "").strip()
    if not pid:
        return None
    for p in _insts():
        if _inst_id(p) == pid:
            return p
    return None


def is_official(platform_id: str) -> bool:
    """该实例是不是 QQ 官方机器人（含 webhook 变体）。"""
    p = platform_of(platform_id)
    if p is None:
        return False
    try:
        return str(p.meta().name or "").strip().lower().startswith(OFFICIAL_PREFIX)
    except Exception:       # noqa: BLE001
        return False


def api_of(platform_id: str):
    """该官方实例底下的 botpy `api` 对象（发消息用）；拿不到返回 None。"""
    p = platform_of(platform_id)
    if p is None:
        return None
    return getattr(getattr(p, "client", None), "api", None)


# ---------------- ① 直连 botpy ----------------


async def post_group_text(platform_id: str, group_openid: str, text: str) -> None:
    """官方群：发一条**纯文本**（`msg_type=0`）。失败抛异常。"""
    api = api_of(platform_id)
    if api is None:
        raise RuntimeError(f"拿不到官方实例的 botpy 接口（{platform_id}）")
    body = str(text or "").strip()
    if not body:
        raise RuntimeError("正文为空，官方机器人会拒收")
    await api.post_group_message(
        group_openid=str(group_openid).strip(),
        msg_type=0,
        content=body,
        msg_seq=random.randint(1, 10000),
    )


async def post_group_markdown(platform_id: str, group_openid: str, md: str) -> None:
    """官方群：发一条 **markdown**（`msg_type=2`）。失败抛异常。"""
    api = api_of(platform_id)
    if api is None:
        raise RuntimeError(f"拿不到官方实例的 botpy 接口（{platform_id}）")
    body = str(md or "").strip()
    if not body:
        raise RuntimeError("正文为空，官方机器人会拒收")
    await api.post_group_message(
        group_openid=str(group_openid).strip(),
        msg_type=2,
        markdown={"content": body},
        msg_seq=random.randint(1, 10000),
    )


async def _upload_image(api, group_openid: str, data: bytes) -> dict | None:
    """把图片字节上传成可发的 `media`（`{"file_info": …}`）；失败返回 None。

    ⚠️ botpy 的 `api.post_group_file` 只接受 `url`，传不了本地二进制 —— 必须直接
    走 `api._http.request` 把 `file_data`(base64) 塞进 JSON body（外语学习插件
    发音频/图片用的就是这条路）。
    """
    http = getattr(api, "_http", None)
    if http is None:
        return None
    try:
        from botpy.http import Route
    except Exception as exc:    # noqa: BLE001
        logger.warning(f"B站推送：botpy 不可用，无法上传图片：{exc}")
        return None
    gid = str(group_openid).strip()
    payload = {
        "file_type": 1,                                # 1 = 图片
        "srv_send_msg": False,                          # 只上传，不代发
        "group_openid": gid,
        "file_data": b64.b64encode(data).decode("ascii"),
    }
    try:
        res = await http.request(Route("POST", f"/v2/groups/{gid}/files"), json=payload)
    except Exception as exc:    # noqa: BLE001
        logger.warning(f"B站推送：官方图片上传失败：{exc}")
        return None
    if isinstance(res, dict) and res.get("file_info"):
        return {"file_info": res["file_info"]}
    return None


async def post_group_image(platform_id: str, group_openid: str,
                           source, caption: str = "") -> None:
    """官方群：发**一张图**（`msg_type=7` 富媒体，可带一行说明文字）。

    `source` 可以是本地路径（`str`/`Path`）或图片字节（`bytes`）。
    上传失败抛异常，由调用方降级（改用 markdown 外链 / 纯文本）。
    """
    api = api_of(platform_id)
    if api is None:
        raise RuntimeError(f"拿不到官方实例的 botpy 接口（{platform_id}）")
    data: bytes
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(str(source)).read_bytes()
    if not data:
        raise RuntimeError("图片数据为空")
    media = await _upload_image(api, group_openid, data)
    if media is None:
        raise RuntimeError("官方图片上传失败")
    await api.post_group_message(
        group_openid=str(group_openid).strip(),
        msg_type=7,
        media=media,
        content=str(caption or "").strip() or None,
        msg_seq=random.randint(1, 10000),
    )


# ---------------- ② 补场景缓存（拿不到 botpy 时的后手） ----------------


def warm_group_scene(platform_id: str, group_openid: str) -> bool:
    """把「这个会话是群」写进适配器的 `_session_scene`，让主动推送不被 skip。

    适配器里 `remember_session_scene()` 是**公有方法**（v4.28.x），本来是收到群
    消息时调的；这里在推送前替它补一次，等价于「先让机器人知道这是个群」。
    成功返回 True；实例不存在 / 方法被改名 / 版本不符 → 静默返回 False。
    """
    gid = str(group_openid or "").strip()
    p = platform_of(platform_id)
    if p is None or not gid:
        return False
    fn = getattr(p, "remember_session_scene", None)
    if not callable(fn):
        return False
    try:
        fn(gid, "group")
        return True
    except Exception as exc:       # noqa: BLE001 —— 兜底失败不该把推送带崩
        logger.debug(f"B站推送：补场景缓存失败（已忽略，改走直连）：{exc}")
        return False
