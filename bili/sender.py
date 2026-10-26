"""消息发送：把推送内容经 AstrBot 的 context.send_message() 发到指定群。

推送目标 = 统一会话 origin：`{platform_id}:GroupMessage:{group_id}`
（platform_id 是 WebUI「消息平台」里的实例 ID，group_id 是该平台群消息事件里的
session_id，例如 aiocqhttp 的 QQ 群号）。

**按平台类型分发**（对齐原插件「OneBot / QQ 官方」双通道的行为）：
- `qq_official`（QQ 官方机器人）→ 发 **Markdown**（msg_type=2）：封面图以 B 站
  CDN 外链直接写进 markdown（官方会转存），**不发 Image 组件**（带 Image 会被
  适配器拆成 msg_type=7 富媒体、markdown 被丢掉）。适配器自带 markdown 失败退
  纯文本，不用自己兜底。
- 其他平台（aiocqhttp 等）→ 发「文字 + 图片」普通消息链，显式 `use_markdown_=False`。

main.py 在插件初始化时调 bind(context)；pusher / live_pusher 只 import 本模块。
"""
from __future__ import annotations

from loguru import logger

from . import qqbot

_context = None


def bind(context) -> None:
    """插件启动时绑定 AstrBot Context（官方直连通道 qqbot 也要用）。"""
    global _context
    _context = context
    qqbot.bind(context)


def session_of(platform_id: str, group_id: str) -> str:
    """订阅里存的 (平台 ID, 群 ID) → 统一会话 origin。"""
    return f"{str(platform_id or '').strip()}:GroupMessage:{str(group_id or '').strip()}"


def _inst_id(p) -> str:
    """平台实例的唯一 ID。⚠️ Platform 实例没有 .id 属性（getattr 恒为空），
    真实 ID 在 meta().id（来自配置项 config["id"]）—— context.send_message
    就是用 meta().id 来匹配 origin 里的平台段的，这里必须保持一致。"""
    try:
        return str(p.meta().id or "") or str((getattr(p, "config", None) or {}).get("id") or "")
    except Exception:
        return str((getattr(p, "config", None) or {}).get("id") or "")


def platform_type(platform_id: str) -> str:
    """按实例 ID 查平台适配器类型（meta().name，如 qq_official / aiocqhttp）。

    每次现查（就是一次内存列表遍历），不做缓存 —— 平台可能随时增删。
    查不到返回空串。
    """
    if _context is None or not str(platform_id or "").strip():
        return ""
    try:
        for p in _context.platform_manager.get_insts():
            if _inst_id(p) == str(platform_id):
                try:
                    return str(p.meta().name or "").strip().lower()
                except Exception:
                    return ""
    except Exception:
        pass
    return ""


async def send_group(
    platform_id: str, group_id: str, text: str, cover: str = "", markdown: str = ""
) -> None:
    """把一条消息发到指定群，按平台类型选通道。

    - text：纯文本版文案（非官方平台 / markdown 兜底时用）；
    - markdown：官方 markdown 版文案（可选；官方平台优先用它）；
    - cover：封面图直链。官方平台把它嵌进 markdown；其他平台作为 Image 组件发。
    发送失败（平台不存在 / 目标不合法 / 适配器返回失败）时抛异常，由调用方记入
    本轮错误摘要。
    """
    if _context is None:
        raise RuntimeError("插件尚未初始化完成，无法发送消息")
    session = session_of(platform_id, group_id)
    if not str(platform_id or "").strip() or not str(group_id or "").strip():
        raise ValueError(f"推送目标不完整：platform_id={platform_id!r}, group_id={group_id!r}")

    from astrbot.api.message_components import Image, Plain
    from astrbot.core.message.message_event_result import MessageChain

    ptype = platform_type(platform_id)
    if ptype.startswith("qq_official"):
        # QQ 官方机器人：**直连 botpy** 发，不走 context.send_message。
        # 原因见 bili/qqbot.py 模块头：适配器在没有 msg_id / scene 缓存时会把主动
        # 推送**静默丢弃**，而且丢弃时是 `return`（返回 None，不是 False）——
        # 下面那个 `ok is False` 根本判不出来，推送丢了还以为发成功了。
        if await _send_official_direct(platform_id, group_id, text, markdown, cover):
            return
        # 拿不到 botpy（client 未就绪 / 版本差异）→ 补场景缓存后走原来的消息链通道
        if not qqbot.warm_group_scene(platform_id, group_id):
            logger.warning(f"B站推送：官方实例拿不到 botpy、也补不了场景缓存"
                           f"（platform={platform_id}）—— 本条推送可能被适配器静默丢弃")
        if markdown:
            # markdown 通道：封面外链写进文案（紧跟标题行）
            md = markdown
            if cover:
                from . import mdgen
                md = await mdgen.md_with_cover(md, cover)
            chain = MessageChain(chain=[Plain(md)])
            chain.use_markdown_ = True      # 强制走 msg_type=2；失败适配器自动退纯文本
        else:
            chain = MessageChain(chain=[Plain(text)])
            chain.use_markdown_ = False
    else:
        # 其他平台（aiocqhttp 等）：普通「文字 + 图片」，明确不发 markdown。
        comps = [Plain(text)]
        if cover:
            comps.append(Image.fromURL(cover))
        chain = MessageChain(chain=comps)
        chain.use_markdown_ = False

    ok = await _context.send_message(session, chain)
    # AstrBot 的 send_message 返回 bool（False = 未找到平台或发送失败）
    if ok is False:
        raise RuntimeError(
            f"消息发送失败（platform={platform_id}, group={group_id}）："
            "请确认平台实例 ID 正确、平台已连接，且群 ID 是该平台群消息事件里的 session_id"
        )


async def _send_official_direct(platform_id: str, group_id: str, text: str,
                                markdown: str, cover: str) -> bool:
    """官方群**直连 botpy** 发一条：markdown（`msg_type=2`）→ 失败退纯文本。

    返回 True = 已发出（调用方直接结束）；False = 拿不到 botpy，调用方走老路。

    主动推送不需要 `msg_id`（官方按「有没有 msg_id」区分主动/被动），所以这里
    不带 msg_id —— 这也正是绕开适配器那条「没缓存就 skip」门禁的关键。
    """
    if qqbot.api_of(platform_id) is None:
        return False
    if str(markdown or "").strip():
        md = markdown
        if cover:
            from . import mdgen
            md = await mdgen.md_with_cover(md, cover)
        try:
            await qqbot.post_group_markdown(platform_id, group_id, md)
            return True
        except Exception as exc:                     # noqa: BLE001
            logger.warning(f"B站推送：官方 markdown 直连失败，退回纯文本：{exc}")
    # 纯文本兜底：这里失败就让它抛出去，由调用方记进本轮错误摘要
    await qqbot.post_group_text(platform_id, group_id, text)
    return True
