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

_context = None


def bind(context) -> None:
    """插件启动时绑定 AstrBot Context。"""
    global _context
    _context = context


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
    if ptype.startswith("qq_official") and markdown:
        # QQ 官方机器人：markdown 通道，封面外链写进文案（紧跟标题行）。
        md = markdown
        if cover:
            from . import mdgen
            md = await mdgen.md_with_cover(md, cover)
        chain = MessageChain(chain=[Plain(md)])
        chain.use_markdown_ = True          # 强制走 msg_type=2；失败适配器自动退纯文本
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
