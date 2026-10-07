"""B 站 Web 接口客户端：扫码登录 + 凭证续期 + 动态 / 私信读取。

接口要点（2026-08 起 B 站改版，务必按此实现）：
- 扫码登录：generate 拿 qrcode_key 与二维码内容；轮询 poll 判断状态
  86101 未扫码 / 86090 已扫码待确认 / 86038 已失效 / 0 成功。
- **登录成功后 SESSDATA 不再拼在返回的 url 里**，而是通过 Set-Cookie 下发。
  取凭证顺序：① 轮询响应自身的 Set-Cookie；② 手动（不跟随重定向）请求
  data.url 这个跨域票据链接，从它的 Set-Cookie 里取。跟随 302 就看不到了。
- 申请二维码与轮询都要带设备指纹 buvid3/buvid4（x/frontend/finger/spi）。
- 动态接口需 WBI 签名（w_rid + wts），密钥 img_key/sub_key 取自 nav 的 wbi_img。
- 私信接口在 api.vc.bilibili.com，只需 Cookie(SESSDATA)，无需 WBI。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
import urllib.parse
from email.utils import parsedate_to_datetime

import httpx
from loguru import logger

from . import store

PASSPORT = "https://passport.bilibili.com"
API = "https://api.bilibili.com"
VC = "https://api.vc.bilibili.com"
# ⚠️ 直播接口有**独立的域名**：`/xlive/*` 只挂在 api.live.bilibili.com 上，
# api.bilibili.com 没有这些路由（会回一个 404 的 HTML 页面）→
# `_api_get` 解析 JSON 失败 → 前端只看到「接口返回非 JSON（HTTP 404）」。
# 2026-10-01 修：直播关注列表 / 批量直播状态全部走这个域名。
LIVE_API = "https://api.live.bilibili.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# WBI 签名用的固定置换表（B 站公开算法）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

# 需要保留的 Cookie 字段（其余如指纹类临时字段一律丢弃）
COOKIE_KEYS = (
    "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid",
    "buvid3", "buvid4", "b_nut", "b_lsid", "bili_ticket", "_uuid", "fingerprint",
)

DYNAMIC_FEATURES = (
    "itemOpusStyle", "listOnlyfans", "opusBigCover", "onlyfansVote",
    "forwardListHidden", "decorationCard", "commentsNewVersion",
    "onlyfansAssetsV2", "ugcDelete", "onlyfansQaCard",
)


class BiliError(Exception):
    """B 站接口调用失败（含接口返回非 0 code 与网络异常）。"""


# ---------------- 取值兜底 ----------------
# B 站同一字段在不同版本 / 不同动态类型下可能是 dict / list / str / null，
# 直接 .get() 或 int() 会抛异常并让整个接口 500，所以统一走这三个函数。


def _int(value, default: int = 0) -> int:
    """安全转 int，转换失败返回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict(value) -> dict:
    """只接受 dict，其余一律当空字典。"""
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    """只接受 list，其余一律当空列表。"""
    return value if isinstance(value, list) else []


# ---------------- HTTP 基础 ----------------

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """复用一个客户端；follow_redirects=False —— 跨域登录链接必须自己看 Set-Cookie。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=False,
            headers=_headers(),
        )
    return _client


# ---------------- 请求节流：每一次请求之间都要有间隔 ----------------
# 不是「两个订阅之间等一下」，而是**每个 B 站请求**发出前都要保证距上一次请求
# 至少 gap × U(0.5, 1.5) 秒 —— 不管有多少订阅、也不管是谁触发的（定时推送 /
# 页面手动刷新 / 翻页），全部串行排队，绝不并发轰炸。
_REQUEST_GAP = {"seconds": 0.0}      # 0 = 不限流
_gap_lock: asyncio.Lock | None = None
_gap_last = 0.0


def set_request_gap(seconds) -> None:
    """设置「每个请求之间的最小间隔（秒）」，0 = 不限流。"""
    try:
        _REQUEST_GAP["seconds"] = max(0.0, float(seconds))
    except (TypeError, ValueError):
        _REQUEST_GAP["seconds"] = 0.0


def request_gap() -> float:
    return float(_REQUEST_GAP.get("seconds") or 0.0)


async def _throttle() -> None:
    """排队：距上次请求不足间隔就先睡一会儿（间隔带随机，避免固定节奏被识别）。"""
    global _gap_last, _gap_lock
    base = request_gap()
    if base <= 0:
        return
    if _gap_lock is None:
        _gap_lock = asyncio.Lock()
    async with _gap_lock:
        wait = base * random.uniform(0.5, 1.5) - (time.monotonic() - _gap_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _gap_last = time.monotonic()


def _headers(referer: str = "https://www.bilibili.com/") -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer,
        "Origin": "https://www.bilibili.com",
    }


def _data(resp: httpx.Response) -> dict:
    """校验外层 code 并返回 data。"""
    try:
        payload = resp.json()
    except ValueError:
        raise BiliError(f"接口返回非 JSON（HTTP {resp.status_code}）")
    code = int(payload.get("code") or 0)
    if code != 0:
        raise BiliError(f"{payload.get('message') or '接口调用失败'}（code={code}）")
    return _dict(payload.get("data"))


def _vc_data(resp: httpx.Response) -> dict:
    """私信接口：code 非 0 即失败（部分接口用 msg 而不是 message）。"""
    try:
        payload = resp.json()
    except ValueError:
        raise BiliError(f"私信接口返回非 JSON（HTTP {resp.status_code}）")
    code = int(payload.get("code") or 0)
    if code != 0:
        raise BiliError(
            f"{payload.get('message') or payload.get('msg') or '私信接口调用失败'}（code={code}）"
        )
    return _dict(payload.get("data"))


# ---------------- 设备指纹 ----------------

_buvid: dict = {}


async def buvid(force: bool = False) -> dict:
    """buvid3/buvid4：未登录接口与扫码登录都需要。"""
    global _buvid
    if _buvid and not force:
        return _buvid
    resp = await get_client().get(
        API + "/x/frontend/finger/spi", headers=_headers()
    )
    data = _data(resp)
    _buvid = {
        "buvid3": str(data.get("b_3") or ""),
        "buvid4": str(data.get("b_4") or ""),
    }
    return _buvid


def _request_cookies() -> dict:
    """请求携带的 Cookie：已登录用登录凭证，否则只用设备指纹。"""
    ck = store.cookie()
    if ck.get("SESSDATA"):
        return ck
    return dict(_buvid)


# ---------------- 扫码登录 ----------------

_LOGIN_SESSIONS: dict[str, dict] = {}


def _clean_login_sessions() -> None:
    """清理 200 秒前的登录会话（二维码有效期 180 秒）。"""
    now = time.time()
    for key in [k for k, v in _LOGIN_SESSIONS.items() if now - v.get("ts", 0) > 200]:
        _LOGIN_SESSIONS.pop(key, None)


def login_url(key: str) -> str:
    """取某个 qrcode_key 对应的二维码内容（供生成二维码图片）。"""
    sess = _LOGIN_SESSIONS.get(key)
    return str(sess.get("url") or "") if sess else ""


def _parse_cookies(cookie_headers: list[str]) -> dict:
    """从 Set-Cookie 头里提取需要的字段（只取第一段 key=value）。"""
    out: dict = {}
    for raw in cookie_headers or []:
        first = raw.split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        name, value = name.strip(), value.strip().strip('"')
        if name in COOKIE_KEYS and value and value != "deleted":
            out[name] = value
    return out


def _cookie_expires(cookie_headers: list[str], name: str = "SESSDATA") -> int:
    """解析某个 Cookie 的 Expires（秒级时间戳，取不到返回 0）。"""
    for raw in cookie_headers or []:
        first = raw.split(";", 1)[0].strip()
        if not first.lower().startswith(name.lower() + "="):
            continue
        for part in raw.split(";")[1:]:
            key, _, value = part.partition("=")
            if key.strip().lower() == "expires":
                try:
                    return int(parsedate_to_datetime(value.strip()).timestamp())
                except (TypeError, ValueError, OverflowError):
                    return 0
    return 0


async def login_start(go_url: str = "https://www.bilibili.com/") -> dict:
    """申请登录二维码。返回 {qrcode_key, url, expires_in}。"""
    fingerprint = await buvid()
    resp = await get_client().get(
        PASSPORT + "/x/passport-login/web/qrcode/generate",
        params={"source": "main-fe-header", "go_url": go_url},
        cookies=fingerprint,
        headers=_headers(PASSPORT + "/"),
    )
    data = _data(resp)
    key = str(data.get("qrcode_key") or "")
    url = str(data.get("url") or "")
    if not key or not url:
        raise BiliError("申请登录二维码失败：返回缺少 qrcode_key / url")
    _clean_login_sessions()
    _LOGIN_SESSIONS[key] = {
        "url": url,
        "buvid": fingerprint,
        "ts": time.time(),
    }
    return {"qrcode_key": key, "url": url, "expires_in": 180}


async def login_poll(key: str) -> dict:
    """轮询扫码状态。成功时落盘凭证并返回 status=success。"""
    sess = _LOGIN_SESSIONS.get(key)
    if sess is None:
        raise BiliError("登录会话不存在或已过期，请重新获取二维码")

    client = get_client()
    resp = await client.get(
        PASSPORT + "/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": key, "source": "main-fe-header"},
        cookies=sess.get("buvid") or {},
        headers=_headers(PASSPORT + "/"),
    )
    try:
        payload = resp.json()
    except ValueError:
        raise BiliError(f"轮询接口返回非 JSON（HTTP {resp.status_code}）")
    if int(payload.get("code") or 0) != 0:
        raise BiliError(f"轮询失败：{payload.get('message')}（code={payload.get('code')}）")

    data = payload.get("data") or {}
    status_code = int(data.get("code") or 0)
    if status_code == 86101:
        return {"status": "waiting", "message": "等待扫码…"}
    if status_code == 86090:
        return {"status": "scanned", "message": "已扫码，请在手机上确认"}
    if status_code == 86038:
        _LOGIN_SESSIONS.pop(key, None)
        return {"status": "expired", "message": "二维码已失效，请重新获取"}
    if status_code != 0:
        return {"status": "error", "message": f"未知扫码状态 {status_code}"}

    # 登录成功：凭证走 Set-Cookie，先取轮询响应自身的
    own_cookies = resp.headers.get_list("Set-Cookie")
    cookie = _parse_cookies(own_cookies)
    expires_at = _cookie_expires(own_cookies)

    # 取不到再手动跟一次跨域票据链接（不能自动跟随重定向，否则看不到 Set-Cookie）
    if not cookie.get("SESSDATA") and data.get("url"):
        cross = await client.get(
            str(data["url"]),
            cookies=sess.get("buvid") or {},
            headers=_headers(PASSPORT + "/"),
        )
        cross_cookies = cross.headers.get_list("Set-Cookie")
        cookie.update(_parse_cookies(cross_cookies))
        expires_at = expires_at or _cookie_expires(cross_cookies)

    if not cookie.get("SESSDATA"):
        return {
            "status": "error",
            "message": "扫码已确认，但没拿到 Cookie（B 站未下发）：请重新扫码，或重启机器人后重试",
        }

    user = await fetch_user(cookie)
    refresh_token = str(data.get("refresh_token") or "")
    store.save_auth(cookie, refresh_token, user, expires_at)
    _LOGIN_SESSIONS.pop(key, None)
    return {
        "status": "success",
        "message": f"已登录：{user.get('uname') or user.get('mid')}",
        "user": user,
        "has_refresh_token": bool(refresh_token),
    }


async def fetch_user(cookie: dict | None = None) -> dict:
    """用 Cookie 取登录用户信息（同时刷新 WBI 密钥缓存）。未登录会抛错。"""
    ck = cookie or store.cookie()
    if not ck.get("SESSDATA"):
        raise BiliError("未登录 B 站")
    resp = await get_client().get(
        API + "/x/web-interface/nav", cookies=ck, headers=_headers()
    )
    data = _data(resp)
    if not data.get("isLogin"):
        raise BiliError("凭证已失效：请重新扫码登录")
    wbi = data.get("wbi_img") or {}
    img, sub = _key_of(wbi.get("img_url") or ""), _key_of(wbi.get("sub_url") or "")
    if img and sub:
        _wbi_cache.update(img=img, sub=sub, ts=time.time())
    return {
        "mid": _int(data.get("mid")),
        "uname": str(data.get("uname") or ""),
        "face": str(data.get("face") or ""),
    }


async def refresh_cookie() -> tuple[bool, str]:
    """尝试用 refresh_token 续期 Cookie；失败则需要重新扫码。"""
    token = store.refresh_token()
    if not token:
        return False, "没有 refresh_token：请重新扫码登录"
    ck = store.cookie()
    if not ck.get("SESSDATA"):
        return False, "当前未登录：请重新扫码登录"
    client = get_client()
    # 1) 取 refresh_csrf
    resp = await client.get(
        PASSPORT + "/x/passport-login/web/confirm/refresh",
        params={"refresh_token": token},
        cookies=ck,
        headers=_headers(PASSPORT + "/"),
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    data = payload.get("data") or {}
    refresh_csrf = str(data.get("refresh_csrf") or "")
    if not refresh_csrf:
        refresh_csrf = _parse_cookies(resp.headers.get_list("Set-Cookie")).get(
            "refresh_csrf", ""
        )
    if not refresh_csrf:
        return False, "续期失败：取不到 refresh_csrf，请重新扫码登录"

    # 2) 换新 Cookie（同样走 Set-Cookie）
    resp2 = await client.post(
        PASSPORT + "/x/passport-login/web/cookie/refresh",
        data={
            "csrf": ck.get("bili_jct", ""),
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": token,
        },
        cookies=ck,
        headers=_headers(PASSPORT + "/"),
    )
    try:
        payload2 = resp2.json()
    except ValueError:
        return False, "续期失败：接口返回非 JSON"
    if int(payload2.get("code") or 0) != 0:
        data2 = payload2.get("data") or {}
        if data2.get("refresh_token"):
            store.save_auth(store.cookie(), str(data2["refresh_token"]))
        return False, f"续期失败：{payload2.get('message')}（需重新扫码）"

    new_cookies = resp2.headers.get_list("Set-Cookie")
    cookie = dict(ck)
    cookie.update(_parse_cookies(new_cookies))
    if not cookie.get("SESSDATA"):
        return False, "续期接口成功但未下发新 Cookie：请重新扫码"
    new_token = str((payload2.get("data") or {}).get("refresh_token") or token)
    user = await fetch_user(cookie)
    store.save_auth(
        cookie, new_token, user, _cookie_expires(new_cookies) or store.expires_at()
    )
    return True, f"凭证已续期：{user.get('uname') or user.get('mid')}"


# ---------------- WBI 签名 ----------------

_wbi_cache: dict = {"img": "", "sub": "", "ts": 0.0}


def _key_of(url: str) -> str:
    """从 wbi_img 的图片 URL 里取密钥（文件名去掉扩展名）。"""
    return url.rsplit("/", 1)[-1].split(".", 1)[0]


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """给参数加上 wts 与 w_rid。"""
    mixin = _mixin_key(img_key + sub_key)
    cleaned = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in params.items()
        if v not in (None, "")
    }
    cleaned["wts"] = round(time.time())
    cleaned = dict(sorted(cleaned.items()))
    query = urllib.parse.urlencode(cleaned, quote_via=urllib.parse.quote)
    cleaned["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return cleaned


async def wbi_keys(force: bool = False) -> tuple[str, str]:
    """取 WBI 密钥（缓存 30 分钟；密钥每日轮换）。"""
    now = time.time()
    if (
        not force
        and _wbi_cache.get("img")
        and _wbi_cache.get("sub")
        and now - float(_wbi_cache.get("ts") or 0) < 1800
    ):
        return str(_wbi_cache["img"]), str(_wbi_cache["sub"])
    if not _buvid:
        await buvid()
    await _throttle()
    resp = await get_client().get(
        API + "/x/web-interface/nav",
        cookies=_request_cookies(),
        headers=_headers(),
    )
    data = _data(resp)
    wbi = data.get("wbi_img") or {}
    _wbi_cache.update(
        img=_key_of(wbi.get("img_url") or ""),
        sub=_key_of(wbi.get("sub_url") or ""),
        ts=now,
    )
    if not _wbi_cache.get("img") or not _wbi_cache.get("sub"):
        raise BiliError("获取 WBI 密钥失败：nav 未返回 wbi_img")
    return str(_wbi_cache["img"]), str(_wbi_cache["sub"])


async def _signed_get(url: str, params: dict, referer: str) -> dict:
    """带 WBI 签名的 GET；签名失效（-403/-352）时刷新密钥重试一次。"""
    img, sub = await wbi_keys()
    last_message = "接口调用失败"
    for attempt in (0, 1):
        if attempt:
            img, sub = await wbi_keys(force=True)
        signed = enc_wbi(dict(params), img, sub)
        await _throttle()   # 每个请求都要排队，重试也算一次请求
        resp = await get_client().get(
            url, params=signed, cookies=_request_cookies(), headers=_headers(referer)
        )
        try:
            payload = resp.json()
        except ValueError:
            raise BiliError(f"接口返回非 JSON（HTTP {resp.status_code}）")
        code = int(payload.get("code") or 0)
        if code == 0:
            return _dict(payload.get("data"))
        last_message = f"{payload.get('message') or '接口调用失败'}（code={code}）"
        if code in (-403, -352, -401) and attempt == 0:
            continue
        break
    raise BiliError(last_message)


async def _api_get(url: str, params: dict, referer: str):
    """普通带 Cookie 的 GET（不需要 WBI 签名的接口，如关注列表 / 关注分组）。

    注意：这里的 data 可能是 dict（带 list/total）也可能是 list（分组内成员接口），
    所以原样返回，由调用方自己归一化。
    """
    await _throttle()
    resp = await get_client().get(
        url, params=params, cookies=_request_cookies(), headers=_headers(referer)
    )
    try:
        payload = resp.json()
    except ValueError:
        raise BiliError(f"接口返回非 JSON（HTTP {resp.status_code}）")
    code = int(payload.get("code") or 0)
    if code != 0:
        raise BiliError(f"{payload.get('message') or '接口调用失败'}（code={code}）")
    return payload.get("data")


# ---------------- 动态 ----------------
# 动态类型：B 站返回的是 MAJOR_TYPE_*，归一化成短 key，订阅可按类型过滤（只推想看的）
DYN_KINDS = ("archive", "article", "pgc", "opus", "draw", "live", "common")
KIND_LABELS = {
    "archive": "投稿",
    "article": "专栏",
    "pgc": "影视",
    "opus": "图文",
    "draw": "相册",
    "live": "直播",
    "common": "文字",
}
DEFAULT_DYN_KINDS = ["archive"]     # 默认只推投稿


def normalize_kinds(value) -> list[str]:
    """把任意输入（字符串 / 列表 / None）夹成合法类型列表，空则回落默认（投稿）。"""
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for k in _list(value):
        k = str(k).strip().lower()
        if k in DYN_KINDS and k not in out:
            out.append(k)
    return out or list(DEFAULT_DYN_KINDS)


# 订阅「内容过滤」：正文里必须包含某个关键词才推送（用户只要含某串的动态）。
# 一条订阅可以有多个关键词，命中**任意一个**即推送；分隔符见 KEYWORD_SEPS。
# 留空 = 不过滤（默认，老订阅行为不变）。
KEYWORD_SEPS = "\r\n,，、;；|｜"
KEYWORD_MAX = 500      # 存盘时夹取的最大长度（纯防呆）


def split_keywords(value) -> list[str]:
    """把用户填的「内容需包含」文本切成关键词列表。

    - 输入可以是字符串（按分隔符切）或列表；
    - 去首尾空白、去重、丢掉空项；
    - **空 → 返回空列表 = 不过滤**（默认，保持老订阅行为不变）。
    """
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    else:
        raw = re.split("[" + KEYWORD_SEPS + "]+", str(value or ""))
    out: list[str] = []
    for k in raw:
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def clean_keyword(value) -> str:
    """存盘用的原文：只做 strip 与限长（保留用户自己写的换行/逗号排版，方便回显）。"""
    return str(value or "").strip()[:KEYWORD_MAX]


def _kind_of(major_type: str, major: dict) -> str:
    """MAJOR_TYPE_* → 短 key。OPUS 里如果 jump_url 指向视频，按投稿算。"""
    t = str(major_type or "").upper()
    table = {
        "MAJOR_TYPE_ARCHIVE": "archive",
        "MAJOR_TYPE_ARTICLE": "article",
        "MAJOR_TYPE_PGC": "pgc",
        "MAJOR_TYPE_DRAW": "draw",
        "MAJOR_TYPE_LIVE_RCMD": "live",
        "MAJOR_TYPE_COMMON": "common",
        "MAJOR_TYPE_NONE": "common",
    }
    if t == "MAJOR_TYPE_OPUS":
        opus = _dict(major.get("opus"))
        jump = str(opus.get("jump_url") or "")
        if "/video/" in jump or opus.get("ugc") or opus.get("archive"):
            return "archive"
        return "opus"
    return table.get(t, "common")


def _search_text(*parts) -> str:
    """把「标题 + 各处正文」拼成一段给**内容过滤**用的文本（去空、去重）。

    为什么要单独拼一份：图文（OPUS）的正文有两个落点 —— 老的 `desc.text` 与
    新的 `major.opus.summary.text`，而展示用的 `text` 只在 desc 为空时才回落取
    summary。订阅的「图文需包含」是用来**过滤图文动态**的（只对 kind=opus 生效），
    两处正文都得算进来，否则「正文在 summary」的那些图文会被整条判成「不含关键词」而漏推。
    """
    out: list[str] = []
    for p in parts:
        s = str(p or "").strip()
        if s and s not in out:
            out.append(s)
    return "\n".join(out)


def _dyn_brief(item: dict) -> dict:
    """把一条动态归一化成前端好展示的结构。"""
    modules = _dict(item.get("modules"))
    author = _dict(modules.get("module_author"))
    dynamic = _dict(modules.get("module_dynamic"))
    basic = _dict(item.get("basic"))

    text = str(_dict(dynamic.get("desc")).get("text") or "").strip()
    major = _dict(dynamic.get("major"))
    kind = str(major.get("type") or "")
    kind_key = _kind_of(kind, major)
    title = ""
    images: list[str] = []
    # 图文的标题与正文（正文在 desc 为空时才顶上来当展示文本，但过滤时两处都要用）
    opus_m = _dict(major.get("opus"))
    opus_title = str(opus_m.get("title") or "").strip()
    opus_summary = str(_dict(opus_m.get("summary")).get("text") or "").strip()
    if not text:
        text = opus_summary
    jump = ""
    if kind == "MAJOR_TYPE_ARCHIVE":
        archive = _dict(major.get("archive"))
        title = str(archive.get("title") or "")
        images = [str(archive.get("cover") or "")]
        bvid = str(archive.get("bvid") or "")
        jump = f"https://www.bilibili.com/video/{bvid}" if bvid else str(archive.get("jump_url") or "")
    elif kind == "MAJOR_TYPE_ARTICLE":
        article = _dict(major.get("article"))
        title = str(article.get("title") or "")
        images = [str(c) for c in _list(article.get("covers"))]
        jump = str(article.get("jump_url") or "")
    elif kind == "MAJOR_TYPE_PGC":
        pgc = _dict(major.get("pgc"))
        title = str(pgc.get("title") or "")
        images = [str(pgc.get("cover") or "")]
        jump = str(pgc.get("jump_url") or "")
    elif kind == "MAJOR_TYPE_COMMON":
        common = _dict(major.get("common"))
        title = str(common.get("title") or common.get("desc") or "")
        images = [str(common.get("cover") or "")]
        jump = str(common.get("jump_url") or "")
    elif kind == "MAJOR_TYPE_LIVE_RCMD":
        live = _dict(
            _dict(_dict(major.get("live_rcmd")).get("content")).get("live_play_info")
        )
        title = "[直播]"
        images = [str(live.get("cover") or "")]
        room = _int(live.get("room_id") or live.get("roomid"))
        jump = f"https://live.bilibili.com/{room}" if room else str(live.get("jump_url") or "")
    elif kind == "MAJOR_TYPE_DRAW":
        images = [
            str(_dict(i).get("src") or "")
            for i in _list(_dict(major.get("draw")).get("items"))
        ]
    elif kind == "MAJOR_TYPE_OPUS":
        images = [
            str(_dict(p).get("url") or "")
            for p in _list(opus_m.get("pics"))
        ]
        jump = str(opus_m.get("jump_url") or "")
    images = [i for i in images if i][:9]

    # 互动数（module_stat：转发 / 评论 / 点赞），给卡片底栏用
    stat = _dict(modules.get("module_stat"))

    dyn_id = str(basic.get("comment_id_str") or item.get("id_str") or "")
    opus_url = f"https://www.bilibili.com/opus/{dyn_id}" if dyn_id else ""
    return {
        "id": dyn_id or str(author.get("pub_ts") or ""),
        "url": jump or opus_url,          # 优先类型专属链接（视频/专栏/直播），否则 opus 页
        "jump": jump,
        "author": str(author.get("name") or ""),
        "mid": _int(author.get("mid")),
        "face": str(author.get("face") or ""),
        "action": str(author.get("pub_action") or ""),
        "pub_time": str(author.get("pub_time") or ""),
        "ts": _int(author.get("pub_ts")),
        "title": title,
        "text": text or title,
        # 内容过滤专用：标题 + 展示正文 + 图文的标题/正文（两处正文都算，别漏）
        "search_text": _search_text(title, text, opus_title, opus_summary),
        "kind": kind_key,                      # 归一化类型（archive/article/pgc/...）
        "kind_label": KIND_LABELS.get(kind_key, kind_key),
        "images": images,
        "stat": {
            "forward": _int(stat.get("forward")),
            "comment": _int(stat.get("comment")),
            "like": _int(stat.get("like")),
        },
    }


def _dyn_items(data: dict) -> list[dict]:
    """把接口返回的 items 归一化；单条解析失败只跳过，不影响整批。"""
    items: list[dict] = []
    for it in _list(_dict(data).get("items")):
        if not isinstance(it, dict):
            continue
        try:
            brief = _dyn_brief(it)
        except Exception as exc:  # 单条动态结构异常不应该让整个请求 500
            logger.warning(f"B 站动态解析失败，已跳过：{type(exc).__name__}: {exc}")
            continue
        # items 里偶尔混有 null / 空壳条目，解析出来既没作者也没内容，直接跳过
        if not (brief.get("author") or brief.get("text") or brief.get("images")):
            continue
        items.append(brief)
    return items


async def dynamics(uid, offset: str = "") -> dict:
    """拉取某用户的动态（默认本人）。返回 {items, offset, has_more}。"""
    target = str(uid or "").strip() or str(store.user().get("mid") or "")
    if not target:
        raise BiliError("缺少目标 UID：请先登录或指定 UID")
    params: dict = {
        "host_mid": target,
        "timezone_offset": "-480",
        "platform": "web",
        "features": ",".join(DYNAMIC_FEATURES),
        "web_location": "333.1387",
    }
    if offset:
        params["offset"] = offset
    data = await _signed_get(
        API + "/x/polymer/web-dynamic/v1/feed/space",
        params,
        f"https://space.bilibili.com/{target}/dynamic",
    )
    data = _dict(data)
    return {
        "items": _dyn_items(data),
        "offset": str(data.get("offset") or ""),
        "has_more": bool(data.get("has_more")),
    }


async def feed(offset: str = "", dtype: str = "all") -> dict:
    """登录账号关注的动态流（需 SESSDATA）；dtype: all / video / pgc / article。"""
    if not store.logged_in():
        raise BiliError("查看「我关注的」动态需要先登录 B 站")
    params: dict = {
        "type": dtype or "all",
        "timezone_offset": "-480",
        "platform": "web",
        "features": ",".join(DYNAMIC_FEATURES),
        "web_location": "333.1365",
    }
    if offset:
        params["offset"] = offset
    data = _dict(
        await _signed_get(
            API + "/x/polymer/web-dynamic/v1/feed/all",
            params,
            "https://www.bilibili.com/",
        )
    )
    return {
        "items": _dyn_items(data),
        "offset": str(data.get("offset") or ""),
        "has_more": bool(data.get("has_more")),
    }


# ---------------- 关注列表 / 关注分组 ----------------

# 「全部关注」用这个伪 tagid，走 /x/relation/followings（带 total 可翻页）；
# 其余真实分组走 /x/relation/tag（只返回成员，数量为分组自带的 count）。
ALL_TAG_ID = -1


def _relation_brief(u: dict) -> dict:
    sign = str(u.get("sign") or "").replace("\r", " ").replace("\n", " ").strip()
    return {
        "mid": _int(u.get("mid")),
        "uname": str(u.get("uname") or ""),
        "face": str(u.get("face") or ""),
        "sign": sign,
        "official": str(_dict(u.get("official_verify")).get("desc") or ""),
        "vip": bool(_dict(u.get("vip")).get("vipStatus") or 0),
        "mtime": _int(u.get("mtime")),
        # 该用户所属的分组 id（默认分组为 null）
        "tagids": [int(t) for t in _list(u.get("tag")) if str(t).lstrip("-").isdigit()],
        "special": bool(u.get("special") or 0),
    }


async def follow_tags() -> list[dict]:
    """关注分组列表：GET /x/relation/tags（需登录）。

    返回 [{tagid, name, count}]，并在最前面补一个「全部关注」伪分组。
    """
    if not store.logged_in():
        raise BiliError("读取关注分组需要先登录 B 站")
    raw = await _api_get(
        API + "/x/relation/tags",
        {},
        "https://space.bilibili.com/1/fans/follow",
    )
    raw = raw if isinstance(raw, list) else _list(_dict(raw).get("tags"))
    tags: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        tags.append(
            {
                "tagid": _int(t.get("tagid"), ALL_TAG_ID),
                "name": str(t.get("name") or "").strip() or "未命名分组",
                "count": _int(t.get("count")),
            }
        )
    total = sum(t["count"] for t in tags)
    return [{"tagid": ALL_TAG_ID, "name": "全部关注", "count": total}] + tags


# 搜索模式最多扫多少页（每页 50）：B 站接口不支持按名字搜关注，只能本地过滤
FOLLOW_SEARCH_MAX_PAGES = 10


async def followings(
    vmid=None, pn: int = 1, ps: int = 50, order_type: str = "", tagid=None, kw: str = ""
) -> dict:
    """登录账号的关注列表（需 SESSDATA）。

    tagid 为空或 -1 → 全部关注（/x/relation/followings，可翻页）
    tagid 为真实分组 → 该分组成员（/x/relation/tag?mid=&tagid=）
    kw 非空 → 搜索模式：扫前 N 页（约 500 个）按昵称模糊匹配，忽略 tagid 与翻页
    """
    if not store.logged_in():
        raise BiliError("查看关注列表需要先登录 B 站")
    mid = str(vmid or store.user().get("mid") or "")
    if not mid:
        raise BiliError("缺少目标 UID：请先登录或指定 vmid")
    ps = max(1, min(int(ps or 50), 50))
    pn = max(1, int(pn or 1))
    referer = f"https://space.bilibili.com/{mid}/fans/follow"
    keyword = str(kw or "").strip().lower()

    if keyword:
        # 搜索模式：顺序扫页直到拿满全部关注（或到达上限），再按昵称过滤
        rows_all: list = []
        total_all = 0
        for page in range(1, FOLLOW_SEARCH_MAX_PAGES + 1):
            raw = await _api_get(
                API + "/x/relation/followings",
                {"vmid": mid, "pn": page, "ps": 50, "order_type": order_type or ""},
                referer,
            )
            data = _dict(raw)
            rows = _list(data.get("list"))
            total_all = _int(data.get("total")) or total_all
            rows_all.extend(rows)
            if not rows or page * 50 >= (total_all or 0):
                break
        items = [_relation_brief(u) for u in rows_all if isinstance(u, dict)]
        matched = [u for u in items if keyword in str(u.get("uname") or "").lower()]
        return {
            "items": matched[:ps],
            "total": len(matched),
            "pn": 1,
            "ps": ps,
            "tagid": ALL_TAG_ID,
            "search": True,
            "scanned": len(items),
            "total_all": total_all,
        }

    tag_id = ALL_TAG_ID if tagid in (None, "", "all") else _int(tagid, ALL_TAG_ID)
    if tag_id == ALL_TAG_ID:
        raw = await _api_get(
            API + "/x/relation/followings",
            {"vmid": mid, "pn": pn, "ps": ps, "order_type": order_type or ""},
            referer,
        )
        data = _dict(raw)
        rows = _list(data.get("list"))
        total = _int(data.get("total")) or len(rows)
    else:
        raw = await _api_get(
            API + "/x/relation/tag",
            {"mid": mid, "tagid": tag_id, "pn": pn, "ps": ps},
            referer,
        )
        rows = raw if isinstance(raw, list) else _list(_dict(raw).get("list"))
        # 分组接口不返回总数，只能按页估算（下一页按钮始终可点）
        total = (pn - 1) * ps + len(rows)
        if len(rows) >= ps:
            total += 1

    items = [_relation_brief(u) for u in rows if isinstance(u, dict)]
    return {"items": items, "total": total, "pn": pn, "ps": ps, "tagid": tag_id}


async def user_card(uid) -> dict:
    """按 UID 取 UP 主信息（x/web-interface/card，未登录也可）。

    UID 允许直接粘贴 space 主页链接，自动提取里面的数字。
    """
    target = str(uid or "").strip()
    if not target.isdigit():
        m = re.search(r"(\d{5,})", target)
        target = m.group(1) if m else ""
    if not target:
        raise BiliError("请输入 UP 主 UID（纯数字，或粘贴 space.bilibili.com 主页链接）")
    raw = await _api_get(
        API + "/x/web-interface/card",
        {"mid": target},
        f"https://space.bilibili.com/{target}/",
    )
    raw = _dict(raw)
    card = _dict(raw.get("card"))
    mid = _int(card.get("mid")) or _int(target)
    if not mid:
        raise BiliError("没有找到该用户，请确认 UID 是否正确")
    return {
        "mid": mid,
        "uname": str(card.get("name") or ""),
        "face": str(card.get("face") or ""),
        "sign": str(card.get("sign") or "").strip(),
        "official": str(_dict(card.get("official_verify")).get("desc") or ""),
        "vip": bool(_dict(card.get("vip")).get("vipStatus") or 0),
        "followers": _int(raw.get("follower")),
    }


# ---------------- 直播 ----------------
# 直播状态：0=未开播 1=正在直播 2=轮播（视为不在播）
LIVE_STATUS_LIVE = 1
LIVE_STATUS_LABELS = {0: "未开播", 1: "直播中", 2: "轮播中"}


def live_status_label(status) -> str:
    return LIVE_STATUS_LABELS.get(_int(status), "未知")


def _extract_uid(value) -> str:
    """从「纯数字 / space 链接 / live 链接」里取出 UID 或房间号（纯数字）。"""
    target = str(value or "").strip()
    if target.isdigit():
        return target
    m = re.search(r"(\d{4,})", target)
    return m.group(1) if m else ""


def live_cover(url: str) -> str:
    """直播封面补全协议（接口里常见 `//i0.hdslb.com/...` 或 `http://`）。"""
    url = str(url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _live_room_brief(room: dict, user: dict | None = None, mid=None) -> dict:
    """把直播接口返回的 room 归一化。

    不同接口字段名不一样：
    - `/xlive/web-ucenter/v1/live/Status`：`liveStatus` / `title` / `cover` / `uid`
    - `/room/v1/Room/get_info`：`live_status` / `title` / `keyframe` / `uid` / `online`
    - `/xlive/web-ucenter/user/following`：`live_status` / `roomid` / `room_cover` /
      `area_name_v2` / `text_small`（直播中时≈在线人数，形如 "10.9万"）
    """
    room = _dict(room)
    user = _dict(user)
    status = _int(room.get("liveStatus", room.get("live_status", 0)))
    room_id = _int(room.get("room_id") or room.get("roomid") or room.get("roomId"))
    uid = (
        _int(room.get("uid"))
        or _int(mid)
        or _int(user.get("uid"))
        or _int(user.get("mid"))
    )
    cover = live_cover(
        room.get("cover") or room.get("keyframe") or room.get("room_cover") or room.get("user_cover")
    )
    face = live_cover(room.get("face") or user.get("face"))
    return {
        "mid": uid,
        "room_id": room_id,
        "url": f"https://live.bilibili.com/{room_id}" if room_id else "",
        "title": str(room.get("title") or "").strip(),
        "cover": cover,
        "face": face,
        "uname": str(room.get("uname") or user.get("uname") or "").strip(),
        "status": status,
        "live": status == LIVE_STATUS_LIVE,
        "status_label": live_status_label(status),
        "online": _int(room.get("online")),
        # text_small 在直播中时是「在线人数」的展示串（含「万」等单位），非数字 → 原样透传
        "online_text": str(room.get("text_small") or "").strip(),
        "area_name": str(room.get("area_name") or room.get("area_name_v2") or "").strip(),
        "live_time": _int(room.get("live_time")),
        "live_start_time": _int(room.get("live_start_time")),
    }


# 一次最多连续扫多少页关注列表（每页 ≤10 个关注 = 最多 100 个）。
# 关注几百人的账号，第 1 页里常常一个在播的都没有，只拉 1 页会让人以为「拉取失败」。
MAX_LIVE_PAGES = 10


async def live_followings(pn: int = 1, ps: int = 10, pages: int = 1) -> dict:
    """登录账号关注的、**正在直播**的 UP 主列表。

    接口：`GET https://api.live.bilibili.com/xlive/web-ucenter/user/following`（需登录）。

    ⚠️ 域名必须是 **api.live.bilibili.com**：`/xlive/*` 只挂在直播域名下，
    api.bilibili.com 上没有这些路由（回 404 的 HTML）—— 2026-10-01 修的就是这个
    （原来打到 api.bilibili.com，前端只看到「接口返回非 JSON（HTTP 404）」）。

    ⚠️ 2026-09 起旧的 `/xlive/web-ucenter/v1/xfetter/GetWebList` 对已登录会话返回 404
    （接口下线），改用 B 站 API 文档记录的现行端点 `web-ucenter/user/following`。
    返回里未开播的 UP 主也会出现（`live_status == 0`），所以按 `live_status == 1`
    过滤一遍，避免把「轮播」当成直播。

    pages：从 pn 起**连续扫描**多少页（1~`MAX_LIVE_PAGES`）。该接口 `page_size`
    官方有效值是 1-10（超过被夹到 10），所以只看一个关注（10 个）远远不够 ——
    聚合结果按 uid 去重，并回 `scanned` / `has_more` / `next_pn` 供前端「继续加载」。
    """
    if not store.logged_in():
        raise BiliError("查看「我关注的正在直播」需要先登录 B 站")
    pn = max(1, _int(pn, 1))
    ps = max(1, min(_int(ps, 10), 10))  # 官方上限 10
    pages = max(1, min(_int(pages, 1), MAX_LIVE_PAGES))

    items: list[dict] = []
    seen: set[int] = set()
    scanned = 0
    total = 0
    live_count = 0
    total_page = 0
    next_pn = pn

    for i in range(pages):
        page_no = pn + i
        raw = await _api_get(
            LIVE_API + "/xlive/web-ucenter/user/following",
            {"page": page_no, "page_size": ps, "ignoreRecord": 1, "hit_ab": "true"},
            "https://live.bilibili.com/",
        )
        data = _dict(raw)
        rows = _list(data.get("list"))
        # 每页都会带回全量计数，取到就用（拿不到时保留上一次的值）
        total = _int(data.get("count")) or _int(data.get("total")) or total
        live_count = _int(data.get("live_count")) or live_count
        total_page = _int(data.get("totalPage")) or total_page
        scanned += len(rows)
        for u in rows:
            if not isinstance(u, dict):
                continue
            brief = _live_room_brief(u)
            mid = _int(brief.get("mid"))
            if not brief["live"] or (mid and mid in seen):
                continue
            if mid:
                seen.add(mid)
            items.append(brief)
        next_pn = page_no + 1
        if not rows or (total_page and page_no >= total_page):
            next_pn = page_no       # 已经没有下一页了，别让前端继续点
            break

    return {
        "items": items,
        "total": total,
        "live_count": live_count,
        "total_page": total_page,
        "scanned": scanned,
        "has_more": bool(total_page and next_pn <= total_page),
        "next_pn": next_pn,
        "pn": pn,
        "ps": ps,
        "pages": pages,
    }


async def _api_get_qs(url: str, pairs: list[tuple], referer: str):
    """带重复参数名的 GET（如 `?uid=1&uid=2&uid=3`，httpx 用 list[tuple] 表达）。"""
    await _throttle()
    resp = await get_client().get(
        url, params=pairs, cookies=_request_cookies(), headers=_headers(referer)
    )
    try:
        payload = resp.json()
    except ValueError:
        raise BiliError(f"接口返回非 JSON（HTTP {resp.status_code}）")
    code = int(payload.get("code") or 0)
    if code != 0:
        raise BiliError(f"{payload.get('message') or '接口调用失败'}（code={code}）")
    return payload.get("data")


async def live_statuses(uids) -> dict:
    """批量查多个 UP 主的直播状态。

    接口：`/xlive/web-ucenter/v1/live/Status?uid=1&uid=2...`，返回 `{uid: {...}}`。
    不需要 WBI，未登录也可（返回字段较少：无昵称昵图，需要时用关注列表补）。
    一次最多 50 个，超了分批；节流在 `_throttle()` 里，串行不会并发。
    """
    ids: list[str] = []
    for u in _list(uids):
        s = str(u or "").strip()
        if s.isdigit() and s not in ids:
            ids.append(s)
    if not ids:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        raw = await _api_get_qs(
            LIVE_API + "/xlive/web-ucenter/v1/live/Status",
            [("uid", u) for u in chunk],
            "https://live.bilibili.com/",
        )
        data = _dict(raw)
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            mid = str(val.get("uid") or key)
            out[mid] = _live_room_brief(val, mid=mid)
    return out


async def live_status(uid) -> dict:
    """单个 UP 主的直播状态。

    批量接口只返回「已开播或被关注」的用户；查不到就当作**未开播**（不抛错，
    否则「查找主播」页对没在直播的主播会一直报错）。
    """
    target = _extract_uid(uid)
    if not target:
        raise BiliError("缺少 UP 主 UID")
    data = await live_statuses([target])
    brief = data.get(target)
    if brief is not None:
        brief["mid"] = brief.get("mid") or _int(target)
        return brief
    return _live_room_brief({"uid": _int(target), "liveStatus": 0}, mid=target)


# ---------------- 私信 ----------------


def _msg_text(raw, msg_type=1) -> str:
    """私信 content 是 JSON 字符串（{"content":"..."}），取出纯文本。"""
    if raw is None:
        return ""
    obj: object
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError:
            return raw
    else:
        obj = raw
    text = ""
    if isinstance(obj, dict):
        text = str(obj.get("content") or obj.get("text") or obj.get("title") or "")
    else:
        text = str(obj)
    if not text:
        text = {
            1: "[消息]", 2: "[图片]", 3: "[语音]", 4: "[视频]",
            5: "[视频卡片]", 7: "[表情]", 10: "[卡片]", 11: "[转发]",
        }.get(_int(msg_type), f"[类型{msg_type}]")
    return text


def _session_brief(session: dict) -> dict:
    last = _dict(session.get("last_msg"))
    account = _dict(session.get("account_info"))
    return {
        "talker_id": str(session.get("talker_id") or ""),
        "session_type": _int(session.get("session_type"), 1),
        "unread": _int(session.get("unread_count")),
        "name": str(account.get("name") or account.get("uname") or ""),
        "face": str(account.get("face") or ""),
        "last_text": _msg_text(last.get("content"), last.get("msg_type")),
        "ts": _int(last.get("timestamp")),
    }


async def _vc_get(path: str, params: dict) -> dict:
    if not store.logged_in():
        raise BiliError("未登录 B 站：请先在配置页「B站」扫码登录")
    await _throttle()
    resp = await get_client().get(
        VC + path,
        params=params,
        cookies=store.cookie(),
        headers=_headers("https://message.bilibili.com/"),
    )
    return _vc_data(resp)


async def unread() -> dict:
    """未读私信数。"""
    data = await _vc_get(
        "/session_svr/v1/session_svr/single_unread",
        {"build": 0, "mobi_app": "web", "unread_type": 0},
    )
    follow = _int(data.get("follow_unread"))
    unfollow = _int(data.get("unfollow_unread"))
    return {
        "follow": follow,
        "unfollow": unfollow,
        "total": follow + unfollow,
    }


async def sessions(session_type: int = 1, size: int = 20) -> list[dict]:
    """私信会话列表（session_type=1 用户与系统）。"""
    data = await _vc_get(
        "/session_svr/v1/session_svr/get_sessions",
        {
            "session_type": session_type,
            "group_fold": 1,
            "unfollow_fold": 0,
            "sort_rule": 2,
            "build": 0,
            "mobi_app": "web",
            "size": size,
        },
    )
    return [
        _session_brief(s) for s in _list(data.get("session_list")) if isinstance(s, dict)
    ]


async def messages(talker_id, size: int = 20, session_type: int = 1) -> list[dict]:
    """与某人的私信记录（返回按时间正序）。"""
    if str(talker_id or "").strip() == "":
        raise BiliError("缺少 talker_id")
    data = await _vc_get(
        "/svr_sync/v1/svr_sync/fetch_session_msgs",
        {
            "session_type": session_type,
            "sender_device_id": 1,
            "talker_id": str(talker_id),
            "size": size,
            "build": 0,
            "mobi_app": "web",
        },
    )
    my_mid = int(store.user().get("mid") or 0)
    out = []
    for m in _list(data.get("messages")):
        if not isinstance(m, dict):
            continue
        uid = _int(m.get("sender_uid"))
        out.append(
            {
                "sender_uid": uid,
                "self": bool(my_mid and uid == my_mid),
                "ts": _int(m.get("timestamp")),
                "msg_type": _int(m.get("msg_type")),
                "text": _msg_text(m.get("content"), m.get("msg_type")),
            }
        )
    out.reverse()  # 接口是倒序，展示按时间正序
    return out


# ---------------- 状态 ----------------


async def state() -> dict:
    """配置页用：登录状态 + 未读数（未读失败不阻断，单独给 error）。"""
    logged = store.logged_in()
    out = {
        "logged": logged,
        "user": store.user(),
        "login_at": store.login_at(),
        "expires_at": store.expires_at(),
        "has_refresh_token": bool(store.refresh_token()),
        "unread": {},
    }
    if logged:
        try:
            out["unread"] = await unread()
        except BiliError as exc:
            out["unread_error"] = str(exc)
        except httpx.HTTPError as exc:
            out["unread_error"] = f"网络异常：{exc}"
    return out
