"""消息发送：把推送内容经 AstrBot 的 context.send_message() 发到指定群。

推送目标 = 统一会话 origin：`{platform_id}:GroupMessage:{group_id}`
（platform_id 是 WebUI「消息平台」里的实例 ID，group_id 是该平台群消息事件里的
session_id，例如 aiocqhttp 的 QQ 群号）。

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


async def send_group(platform_id: str, group_id: str, text: str, cover: str = "") -> None:
    """把一条消息发到指定群：文字（必需）+ 封面图（可选）。

    AstrBot 会按平台 ID 找到对应的平台适配器实例并发送；发送失败（平台不存在 /
    目标不合法 / 适配器返回失败）时抛异常，由调用方记入本轮错误摘要。
    """
    if _context is None:
        raise RuntimeError("插件尚未初始化完成，无法发送消息")
    session = session_of(platform_id, group_id)
    if not str(platform_id or "").strip() or not str(group_id or "").strip():
        raise ValueError(f"推送目标不完整：platform_id={platform_id!r}, group_id={group_id!r}")

    from astrbot.api.message_components import Image, Plain
    from astrbot.core.message.message_event_result import MessageChain

    comps = [Plain(text)]
    if cover:
        comps.append(Image.fromURL(cover))
    ok = await _context.send_message(session, MessageChain(chain=comps))
    # AstrBot 的 send_message 返回 bool（False = 未找到平台或发送失败）
    if ok is False:
        raise RuntimeError(
            f"消息发送失败（platform={platform_id}, group={group_id}）："
            "请确认平台实例 ID 正确、平台已连接，且群 ID 是该平台群消息事件里的 session_id"
        )
