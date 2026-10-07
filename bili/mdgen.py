"""QQ 官方机器人原生 Markdown（msg_type=2）文案生成。

从 malu_qq_bot 原插件（pusher._markdown / live_pusher._markdown_live）移植，
排版照着 B 站客户端分享卡片：

    > **哔哩哔哩** · 视频
    ### [洛克茜为什么必抽？…](https://b23.tv/BVxxxx)
    三国杂杀鸿天机 投稿了视频
    🔁 12 · 💬 34 · 👍 2706 · 今天 12:00

要点（都是原项目踩过的坑）：
- 原生 markdown 支持的语法很窄：标题、加粗、引用、链接、图片。
- **图片必须带尺寸**：`![#320px #180px](url)` —— 官方客户端靠尺寸提示预留位置，
  缺了只显示空行（官方自己也是把 `![alt](url)` 改写成这个格式再发）。
- 封面图直接把 B 站 CDN 外链写进 markdown（官方会转存外链图），**一条消息搞定**。
- 标题别用一级 `#`（渲染出来字巨大），默认 `###`。
"""
from __future__ import annotations

import re

# ---- 可调常量（原项目同名开关的默认值） ----
MD_TITLE_LINK = True      # True = 跳转挂在标题上；False = 底部单独一行链接
MD_TITLE_LEVEL = "###"    # 标题级别：# 字太大，默认 ###；"" = 退化为加粗链接行
MD_IMG_SIZE = (0, 0)      # (0,0) = 自动探测真实尺寸；非 0 = 强制尺寸
MD_IMG_WIDTH = 320        # 自动探测时展示宽度上限（按比例只缩不放）
MD_IMG_FALLBACK = (320, 180)  # 探测失败兜底（官方兜底 512×512；B 站首图多 16:9）

_KIND_LINK_TEXT = {
    "archive": "▶ 观看视频",
    "pgc": "▶ 观看视频",
    "live": "🔴 进入直播间",
    "article": "📖 阅读专栏",
}

_HEAD_RE = re.compile(r"^#{1,6} ")


def md_text(text: str) -> str:
    """Markdown 里 `*` `_` `[` 等会被当语法，转义掉避免格式错乱。"""
    out = []
    for ch in str(text or ""):
        if ch in "\\`*_{}[]()#+-.!|>~":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def short_line(text: str, limit: int) -> str:
    """压成一行并截断 —— 卡片里每段都要短，长了官方排版反而乱。"""
    text = " ".join(str(text or "").split())
    return text[:limit] + "…" if len(text) > limit else text


def md_title(text: str, url: str = "") -> str:
    """拼标题行：级别 + 链接都在这一个地方决定，动态与直播两处共用。"""
    level = str(MD_TITLE_LEVEL or "").strip()
    if url and MD_TITLE_LINK:
        return f"{level} [{text}]({url})".strip() if level else f"**[{text}]({url})**"
    return f"{level} {text}".strip() if level else f"**{text}**"


def _fit_size(width: int, height: int, max_w: int = MD_IMG_WIDTH) -> tuple[int, int]:
    """探测到的真实尺寸 → markdown 展示尺寸：按比例缩到 max_w 宽（只缩不放）。"""
    if width <= 0 or height <= 0:
        return MD_IMG_FALLBACK
    if width <= max_w:
        return int(width), int(height)
    return int(max_w), max(1, round(height * max_w / width))


def md_image(url: str, width: int, height: int) -> str:
    """markdown 图片语法 —— **带尺寸**：`![#320px #180px](url)`。别省尺寸。"""
    url = str(url or "").strip()
    if not url:
        return ""
    if not url.startswith("https://"):        # 官方这条通道只认 https
        url = url.replace("http://", "https://", 1)
    return f"![#{int(width)}px #{int(height)}px]({url})"


async def md_with_cover(markdown: str, url: str) -> str:
    """把封面图插进 markdown：紧跟标题行之后（B 站卡片就是标题下面跟大图）；
    没有标题就放最后。尺寸优先级：MD_IMG_SIZE > 探测真实宽高 > 兜底。"""
    if not markdown or not url:
        return markdown
    if MD_IMG_SIZE[0] and MD_IMG_SIZE[1]:
        width, height = int(MD_IMG_SIZE[0]), int(MD_IMG_SIZE[1])
    else:
        from . import imgcache                 # 懒加载：避免模块级循环依赖
        width, height = _fit_size(*await imgcache.probe_size(url))
    img = md_image(url, width, height)
    if not img:
        return markdown
    parts = markdown.split("\n\n")
    idx = next((i for i, p in enumerate(parts) if _HEAD_RE.match(p)), -1)
    if idx < 0:
        parts.append(img)
    else:
        parts.insert(idx + 1, img)
    return "\n\n".join(parts)


def feed_markdown(item: dict, sub: dict) -> str:
    """动态推送的 markdown 卡片（原 pusher._markdown）。"""
    uname = str(sub.get("uname") or item.get("author") or "").strip() or f"UID {sub.get('mid')}"
    label = str(item.get("kind_label") or "动态").strip()
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").strip()
    url = str(item.get("url") or "").strip()
    when = str(item.get("pub_time") or "").strip()
    action = str(item.get("action") or "").strip()
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}

    lines: list[str] = [f"> **哔哩哔哩** · {md_text(label)}"]
    # 标题是卡片里最显眼的一行；纯文字动态就用正文第一行当标题。
    head = title or text
    if head:
        lines.append(md_title(md_text(short_line(head, 40)), url))
    # UP 主 + 动作一行说明（不加粗，别跟标题抢眼）
    author = md_text(short_line(uname, 20))
    if action and action not in head:
        author += f" {md_text(action)}"
    lines.append(author)
    foot = []
    for key, mark in (("forward", "🔁"), ("comment", "💬"), ("like", "👍")):
        v = stat.get(key)
        if v:
            foot.append(f"{mark} {v}")
    if when:
        foot.append(md_text(when))
    if foot:
        lines.append(" · ".join(foot))
    desc = text if title and text != title else ""
    if desc:
        lines.append(md_text(short_line(desc, 80)))
    if url and not MD_TITLE_LINK:
        link_text = _KIND_LINK_TEXT.get(str(item.get("kind") or ""), "查看动态")
        lines.append(f"[{link_text}]({url})")
    return "\n\n".join(lines)


def live_markdown(sub: dict, room: dict, online: bool) -> str:
    """直播开播/下播的 markdown 卡片（原 live_pusher._markdown_live）。"""
    host = str(sub.get("uname") or f"UID {sub.get('mid')}")
    action = "开播了" if online else "下播了"
    url = str(room.get("url") or sub.get("room_url") or "").strip()
    head = f"{md_text(host)} {action}"
    lines = [md_title(head, url)]
    title = str(room.get("title") or "").strip()
    if title:
        lines.append(f"> {md_text(title)}")
    area = str(room.get("area_name") or "").strip()
    if area:
        lines.append(f"分区：{md_text(area)}")
    if url and not MD_TITLE_LINK:
        lines.append(f"[进入直播间]({url})")
    return "\n\n".join(lines)
