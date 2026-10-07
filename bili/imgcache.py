"""B 站图片后端代理 + 本地缓存。

页面里 B 站 CDN 的图片用 `<img referrerpolicy="no-referrer">` 直连即可（B 站 CDN
对空 Referer 放行）；本模块留作兜底：后端带 Referer 抓取 + 落盘缓存后返回图片内容。

- 只代理 bilibili 系域名（防 SSRF）
- 缓存目录 <插件>/data/bilibili_cache/，默认 7 天有效期
- 超过 5MB 的图片不落盘，直接转发
- 另提供 `probe_size()`：只发 Range 请求取前 64KB 探图片宽高（需要 Pillow，缺失时返回 0,0）
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
from pathlib import Path

import httpx
from loguru import logger

from . import paths
from .client import UA, get_client

_CACHE_DIR = paths.DATA_DIR / "bilibili_cache"
_TTL = 7 * 24 * 3600
_MAX_CACHE = 5 * 1024 * 1024
# 探尺寸时只取前 64KB（官方适配器取的也是 64KB，足够覆盖 PNG IHDR / JPEG SOF）
_PROBE_BYTES = 64 * 1024

# 允许代理的图片域名（后缀匹配，覆盖 hdslb / bilivideo / biliimg 等 CDN）
ALLOWED_SUFFIXES = (
    "hdslb.com",
    "hdslb.net",
    "bilivideo.com",
    "biliimg.com",
    "bilibili.com",
    "biligame.com",
)

# 图片相对路径（B 站偶尔只给 /bfs/xxx）要补的 CDN 前缀
_CDN_PREFIX = "https://i0.hdslb.com"
_REFERERS = ("https://www.bilibili.com/", "https://space.bilibili.com/")

_CTYPE_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def normalize(url: str) -> str:
    """补齐协议与域名。

    B 站返回的图片地址可能是这几种形式，浏览器都能处理，但后端 httpx 不行：
    - `//i0.hdslb.com/bfs/...`  协议相对
    - `/bfs/face/xxx.jpg`       只有路径
    - `http://...`              老式 http
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return _CDN_PREFIX + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _sniff(data: bytes, ctype: str) -> str:
    """CDN 有时回 `application/octet-stream`（浏览器就显示不出来），按文件头修正。"""
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype and ctype != "application/octet-stream":
        return ctype
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return ctype or "application/octet-stream"


def allowed(url: str) -> bool:
    """只放行 bilibili 系域名，避免把后台变成任意图片代理（SSRF）。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_SUFFIXES)


def _cache_path(url: str, ext: str) -> Path:
    name = hashlib.md5(url.encode("utf-8")).hexdigest() + (ext or ".img")
    return _CACHE_DIR / name


def _ext_of(url: str, ctype: str = "") -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in _CTYPE_BY_EXT:
        return suffix
    ctype = (ctype or "").split(";")[0].strip().lower()
    for ext, mime in _CTYPE_BY_EXT.items():
        if mime == ctype:
            return ".jpg" if ext in (".jpg", ".jpeg") else ext
    return ".img"


def _ctype_of(path: Path, ctype: str = "") -> str:
    if ctype:
        return ctype.split(";")[0].strip()
    return _CTYPE_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


async def load(url: str) -> tuple[bool, bytes | None, str, str]:
    """取图片：命中缓存直接读盘，否则带 Referer 抓取后落盘。

    返回 (ok, 内容, Content-Type, 错误信息)。
    """
    url = normalize(url)
    if not url:
        return False, None, "", "缺少图片地址"
    if not allowed(url):
        return False, None, "", f"不允许代理该域名：{urllib.parse.urlparse(url).hostname or url}"

    ext = _ext_of(url)
    path = _cache_path(url, ext)
    try:
        if path.exists() and (time.time() - path.stat().st_mtime) < _TTL:
            cached = path.read_bytes()
            return True, cached, _sniff(cached, _ctype_of(path)), ""
    except OSError:
        pass

    # 共享客户端全局 follow_redirects=False（登录流程要靠它读 Set-Cookie），
    # 图片这里单独打开跟随重定向，否则 CDN 的 302 会被当成失败。
    # 部分图片换 Referer 才给，所以备一个 space 域名的 Referer 重试一次。
    resp = None
    last_err = ""
    for referer in _REFERERS:
        try:
            r = await get_client().get(
                url,
                headers={"User-Agent": UA, "Referer": referer},
                timeout=httpx.Timeout(15, connect=8),
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            last_err = f"图片下载失败：{exc}"
            continue
        except Exception as exc:  # 兜底：非 httpx 异常也要给前端看得懂的原因
            last_err = f"图片下载失败：{type(exc).__name__}: {exc}"
            break
        if r.status_code == 200:
            resp = r
            break
        last_err = f"图片下载失败：HTTP {r.status_code}"

    if resp is None:
        logger.warning(f"B 站图片代理失败 {url[:120]}：{last_err}")
        return False, None, "", last_err

    data = resp.content
    ctype = _sniff(data, resp.headers.get("Content-Type", ""))
    if not data:
        return False, None, "", "图片内容为空"
    if len(data) <= _MAX_CACHE:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            pass  # 缓存失败不影响返回
    return True, data, ctype, ""


def stats() -> tuple[int, int]:
    """图片缓存现状：(文件数, 总字节数)。目录不存在返回 (0, 0)。"""
    try:
        files = [p for p in _CACHE_DIR.glob("*") if p.is_file()]
    except OSError:
        return 0, 0
    total = 0
    for p in files:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return len(files), total


def clear() -> tuple[int, int]:
    """清空图片缓存目录，返回 (删除文件数, 释放字节数)。"""
    n = freed = 0
    try:
        for p in _CACHE_DIR.glob("*"):
            if not p.is_file():
                continue
            try:
                freed += p.stat().st_size
                p.unlink()
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n, freed


async def probe_size(url: str) -> tuple[int, int]:
    """探图片的真实宽高，失败返回 `(0, 0)`。

    为什么要它：QQ 官方 markdown 的图片**必须带尺寸**（写 `![#宽px #高px](url)`），
    不带尺寸时图会被官方转存、但渲染不出来（只剩一个空行）。官方自己的适配器也正是
    「先探尺寸、再拼 markdown」，探测失败才退回 512×512。

    只发 `Range: bytes=0-65535` 取前 64KB —— PNG 的 IHDR 在第 16 字节、JPEG 的 SOF
    也很靠前，够解出尺寸了，不用把整张图拉下来（大图能省几百 KB~几 MB）。
    解析用 Pillow（项目里本来就有，米游社渲染在用）。
    """
    url = normalize(url)
    if not url or not allowed(url):
        return 0, 0
    for referer in _REFERERS:
        try:
            resp = await get_client().get(
                url,
                headers={
                    "User-Agent": UA,
                    "Referer": referer,
                    "Range": f"bytes=0-{_PROBE_BYTES - 1}",
                },
                timeout=httpx.Timeout(10, connect=6),
                follow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 —— 探不到尺寸不该影响推送
            logger.debug(f"封面尺寸探测失败 {url[:100]}：{type(exc).__name__}: {exc}")
            continue
        if resp.status_code not in (200, 206):
            logger.debug(f"封面尺寸探测失败 {url[:100]}：HTTP {resp.status_code}")
            continue
        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(resp.content)) as im:
                return int(im.width), int(im.height)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"封面尺寸解析失败 {url[:100]}：{type(exc).__name__}: {exc}")
            return 0, 0
    return 0, 0
