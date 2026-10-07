"""astrbot-plugin-bilibili-push —— B 站订阅推送插件（AstrBot）。

由 malu_qq_bot 的 bilibili 插件移植：
- 扫码登录（B 站 Web 二维码登录），凭证（Cookie + refresh_token）落盘
  <插件>/data/bilibili_auth.json，支持续期与退出登录
- 页面（pages/panel）：账号 / 私信 / 关注 / 动态 / 直播 / 动态订阅 / 直播订阅
- 「动态推送」：UP 主 → 平台实例 → 群，定时拉取新动态推送到指定群
- 「直播推送」：主播开播 / 下播事件推送到指定群
- 命令（管理员）：b站状态 · b站私信 · b站动态 [UID]

推送统一走 AstrBot 的 context.send_message(统一会话 origin, 消息链)，
origin = "{platform_id}:GroupMessage:{group_id}"。
"""
import asyncio
import base64
import io
import re

import httpx
from loguru import logger

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request

from . import bili
from .bili import (
    client,
    cmdsubs,
    imgcache,
    live_pusher,
    live_subs,
    pusher,
    sender,
    store,
    subs,
    webcache,
)

PLUGIN_NAME = "astrbot_plugin_bilibili_pusher"

# 页面 bridge 的 endpoint 不带插件名前缀；这里注册的路由必须带。
PREFIX = f"/{PLUGIN_NAME}"


class BilibiliPusherPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        sender.bind(context)
        self._tasks: list[asyncio.Task] = []
        self._register_apis()

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def initialize(self):
        """启动后台推送循环（动态 + 直播各一个）。"""
        self._tasks.append(asyncio.create_task(pusher.loop_forever()))
        self._tasks.append(asyncio.create_task(live_pusher.loop_forever()))
        logger.info("B 站订阅推送循环已启动（动态 + 直播）")

    async def terminate(self):
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    # ==================================================================
    # Web API 工具
    # ==================================================================

    async def _call(self, coro, error_prefix: str, status: int = 400):
        """执行 B 站接口调用，把异常统一转成 error_response。"""
        try:
            return None, await coro
        except (client.BiliError, httpx.HTTPError) as exc:
            return error_response(f"{error_prefix}：{exc}", status_code=status), None
        except Exception as exc:  # 兜底：解析 / 类型异常也要给前端看得懂的原因
            logger.exception(f"bilibili 接口异常：{error_prefix}")
            return (
                error_response(f"{error_prefix}：{type(exc).__name__}: {exc}", status_code=500),
                None,
            )

    async def _body(self) -> dict:
        try:
            data = await request.json(default={})
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _qr_png(text: str) -> bytes | None:
        try:
            import segno
        except ImportError:
            return None
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=8, border=2)
        return buf.getvalue()

    @staticmethod
    def _qr_ready() -> bool:
        try:
            import segno  # noqa: F401

            return True
        except ImportError:
            return False

    def _platforms(self) -> list[dict]:
        """AstrBot 平台实例列表（推送目标的「从哪个实例发」）。

        AstrBot 没有通用的「拉某平台的群列表」API，群 ID 由用户按目标平台群消息
        事件里的 session_id 手填（aiocqhttp = QQ 群号）。
        """
        out: list[dict] = []
        try:
            insts = self.context.platform_manager.get_insts()
        except Exception:
            insts = []
        for p in insts or []:
            # 平台适配器类型 = meta().name（如 qq_official / aiocqhttp）；
            # Platform 实例没有 .type 属性，之前 getattr(p,"type") 恒为空。
            try:
                ptype = str(p.meta().name or "")
            except Exception:
                ptype = str(getattr(p, "type", "") or "")
            out.append(
                {
                    "id": str(getattr(p, "id", "") or ""),
                    "name": str(getattr(p, "name", "") or ""),
                    "type": ptype,
                    "enable": bool(getattr(p, "enable", True)),
                }
            )
        return [p for p in out if p["id"]]

    def _register_apis(self) -> None:
        reg = self.context.register_web_api

        # ---------------- 登录 ----------------
        reg(f"{PREFIX}/state", self.api_state, ["GET"], "B 站登录状态 + 未读私信数")
        reg(f"{PREFIX}/login/start", self.api_login_start, ["POST"], "申请登录二维码")
        reg(f"{PREFIX}/login/poll", self.api_login_poll, ["GET"], "轮询扫码结果")
        reg(f"{PREFIX}/logout", self.api_logout, ["POST"], "退出登录")
        reg(f"{PREFIX}/refresh", self.api_refresh, ["POST"], "凭证续期")
        reg(f"{PREFIX}/qr.png", self.api_qr, ["GET"], "登录二维码图片")

        # ---------------- 私信 / 动态 / 关注 / 直播 ----------------
        reg(f"{PREFIX}/unread", self.api_unread, ["GET"], "未读私信数")
        reg(f"{PREFIX}/sessions", self.api_sessions, ["GET"], "私信会话列表")
        reg(f"{PREFIX}/messages", self.api_messages, ["GET"], "私信记录")
        reg(f"{PREFIX}/dynamics", self.api_dynamics, ["GET"], "指定 UID 的空间动态")
        reg(f"{PREFIX}/feed", self.api_feed, ["GET"], "登录账号关注的动态流")
        reg(f"{PREFIX}/follow_tags", self.api_follow_tags, ["GET"], "关注分组列表")
        reg(f"{PREFIX}/followings", self.api_followings, ["GET"], "关注列表")
        reg(f"{PREFIX}/user", self.api_user, ["GET"], "按 UID 查 UP 主")
        reg(f"{PREFIX}/lives", self.api_lives, ["GET"], "关注的、正在直播的 UP 主")
        reg(f"{PREFIX}/live/status", self.api_live_status, ["GET"], "单个 UP 主的直播状态")

        # ---------------- 推送目标 / 订阅 / 设置 ----------------
        reg(f"{PREFIX}/targets", self.api_targets, ["GET"], "可用的平台实例列表")
        reg(f"{PREFIX}/subs", self.api_subs, ["GET"], "全部动态订阅")
        reg(f"{PREFIX}/subs/save", self.api_subs_save, ["POST"], "新增动态订阅")
        reg(f"{PREFIX}/subs/toggle", self.api_subs_toggle, ["POST"], "启用/停用订阅")
        reg(f"{PREFIX}/subs/types", self.api_subs_types, ["POST"], "修改推送类型与图文过滤")
        reg(f"{PREFIX}/subs/delete", self.api_subs_delete, ["POST"], "删除订阅")
        reg(f"{PREFIX}/push/settings", self.api_push_settings, ["GET"], "推送设置")
        reg(f"{PREFIX}/push/settings", self.api_push_settings_save, ["POST"], "保存推送设置")
        reg(f"{PREFIX}/push/run", self.api_push_run, ["POST"], "立即跑一轮动态推送")

        reg(f"{PREFIX}/live_subs", self.api_live_subs, ["GET"], "全部直播订阅")
        reg(f"{PREFIX}/live_subs/save", self.api_live_subs_save, ["POST"], "新增直播订阅")
        reg(f"{PREFIX}/live_subs/toggle", self.api_live_subs_toggle, ["POST"], "启用/停用直播订阅")
        reg(f"{PREFIX}/live_subs/notify", self.api_live_subs_notify, ["POST"], "修改开播/下播推送")
        reg(f"{PREFIX}/live_subs/delete", self.api_live_subs_delete, ["POST"], "删除直播订阅")
        reg(f"{PREFIX}/live_push/settings", self.api_live_push_settings, ["GET"], "直播推送设置")
        reg(
            f"{PREFIX}/live_push/settings",
            self.api_live_push_settings_save,
            ["POST"],
            "保存直播推送设置",
        )
        reg(f"{PREFIX}/live_push/run", self.api_live_push_run, ["POST"], "立即检查一轮直播状态")

        # ---------------- 命令配置 / 命令订阅 / 数据管理 ----------------
        reg(f"{PREFIX}/cmds", self.api_cmds, ["GET"], "命令配置（可改的两条 + 内置只读）")
        reg(f"{PREFIX}/cmds/save", self.api_cmds_save, ["POST"], "保存命令配置")
        reg(f"{PREFIX}/cmd_subs", self.api_cmd_subs, ["GET"], "命令订阅记录列表")
        reg(f"{PREFIX}/cmd_subs/delete", self.api_cmd_subs_delete, ["POST"], "删除一条命令订阅")
        reg(f"{PREFIX}/cmd_subs/clear", self.api_cmd_subs_clear, ["POST"], "清空命令订阅记录")
        reg(f"{PREFIX}/data/overview", self.api_data_overview, ["GET"], "数据管理页概览")
        reg(f"{PREFIX}/data/cache/clear", self.api_data_cache_clear, ["POST"], "清空缓存")

        # 图片代理（兜底；页面一般直连 CDN + referrerpolicy=no-referrer）
        reg(f"{PREFIX}/img", self.api_img, ["GET"], "B 站图片代理")

    # ==================================================================
    # 登录
    # ==================================================================

    async def api_state(self):
        err, state = await self._call(client.state(), "读取 B 站状态失败")
        if err:
            return err
        state = state if isinstance(state, dict) else {}
        state["qr_ready"] = self._qr_ready()
        return json_response({"ok": True, **state})

    async def api_login_start(self):
        err, info = await self._call(client.login_start(), "申请登录二维码失败")
        if err:
            return err
        info = info if isinstance(info, dict) else {}
        key = str(info.get("qrcode_key") or "")
        if not key:
            return error_response("申请登录二维码失败：B 站未返回 qrcode_key")
        # 二维码直接以 base64 data URI 随 JSON 返回：
        # 插件 API 需要登录 token（bridge 只给 apiGet/apiPost 自动加请求头），
        # 裸 <img src="/qr.png"> 带不上鉴权头会 401，所以不能让前端单独再拉一次图片接口。
        url = str(info.get("url") or "")
        png = self._qr_png(url) if url else None
        return json_response(
            {
                "ok": True,
                "qrcode_key": key,
                "url": url,
                "png": f"data:image/png;base64,{base64.b64encode(png).decode()}" if png else "",
                "expires_in": int(info.get("expires_in") or 180),
                "qr_ready": self._qr_ready(),
            }
        )

    async def api_qr(self):
        """登录二维码图片（PNG）。缺 segno 时返回 500 + url，前端降级为链接。"""
        key = request.query.get("k", "")
        url = client.login_url(key)
        if not url:
            return error_response("二维码已失效或不存在，请重新获取", status_code=404)
        png = self._qr_png(url)
        if png is None:
            return json_response(
                {"ok": False, "message": "缺少二维码依赖：请安装 segno（pip install segno）", "url": url},
                status_code=500,
            )
        # 二进制响应走 Quart 兼容层（register_web_api 的 handler 在 Quart 请求上下文里执行）
        from quart import Response as QuartResponse

        return QuartResponse(png, content_type="image/png")

    async def api_login_poll(self):
        key = request.query.get("k", "")
        try:
            result = await client.login_poll(key)
        except (client.BiliError, httpx.HTTPError) as exc:
            return json_response({"ok": False, "status": "error", "message": str(exc)}, status_code=400)
        except Exception as exc:
            logger.exception("bilibili 扫码轮询异常")
            return json_response(
                {"ok": False, "status": "error", "message": f"轮询异常：{type(exc).__name__}: {exc}"},
                status_code=500,
            )
        # 换账号登录成功：上一账号的页面缓存全部作废
        if isinstance(result, dict) and result.get("status") == "success":
            webcache.clear()
        return json_response({"ok": True, **(result if isinstance(result, dict) else {})})

    async def api_logout(self):
        store.clear()
        webcache.clear()   # 换账号了，页面缓存必须清
        return json_response({"ok": True, "message": "已退出 B 站登录（本地凭证已清除）"})

    async def api_refresh(self):
        err, result = await self._call(client.refresh_cookie(), "凭证续期失败")
        if err:
            return err
        ok, message = result if isinstance(result, tuple) else (False, "凭证续期失败：返回异常")
        return json_response({"ok": bool(ok), "message": str(message)})

    # ==================================================================
    # 私信 / 动态 / 关注 / 直播
    # ==================================================================

    async def api_unread(self):
        err, data = await self._call(client.unread(), "读取未读私信失败")
        if err:
            return err
        return json_response({"ok": True, **(data if isinstance(data, dict) else {})})

    async def api_sessions(self):
        session_type = request.query.get("session_type", 1, type=int)
        size = request.query.get("size", 20, type=int)
        key = f"sessions:{session_type}:{size}"
        if request.query.get("force") != "1":
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, "sessions": hit})
        err, items = await self._call(
            client.sessions(session_type=session_type, size=size), "读取私信会话失败"
        )
        if err:
            # B 站请求失败时退回缓存（聊胜于无）
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, "sessions": hit})
            return err
        if isinstance(items, list):
            webcache.put(key, items)
        return json_response({"ok": True, "sessions": items if isinstance(items, list) else []})

    async def api_messages(self):
        talker_id = request.query.get("talker_id", "")
        size = request.query.get("size", 20, type=int)
        err, items = await self._call(client.messages(talker_id, size=size), "读取私信记录失败")
        if err:
            return err
        return json_response({"ok": True, "messages": items if isinstance(items, list) else []})

    async def api_dynamics(self):
        """指定 UID 的空间动态（未登录也能拉，走 WBI 签名 + 设备指纹）。

        只缓存第一页（offset 为空）；「加载更多」的 offset 请求不缓存。
        """
        uid = request.query.get("uid", "")
        offset = request.query.get("offset", "")
        key = f"dyn:{uid}"
        cacheable = not offset
        if cacheable and request.query.get("force") != "1":
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, **hit})
        err, data = await self._call(client.dynamics(uid, offset=offset), "读取动态失败")
        if err:
            return err
        data = data if isinstance(data, dict) else {}
        if cacheable and data:
            webcache.put(key, data)
        return json_response({"ok": True, **data})

    async def api_feed(self):
        """登录账号关注的动态流（需登录）。type: all / video / pgc / article。

        只缓存第一页（offset 为空）；「加载更多」的 offset 请求不缓存。
        """
        offset = request.query.get("offset", "")
        dtype = request.query.get("type", "all")
        key = f"feed:{dtype}"
        cacheable = not offset
        if cacheable and request.query.get("force") != "1":
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, **hit})
        err, data = await self._call(client.feed(offset=offset, dtype=dtype), "读取动态失败")
        if err:
            return err
        data = data if isinstance(data, dict) else {}
        if cacheable and data:
            webcache.put(key, data)
        return json_response({"ok": True, **data})

    async def api_follow_tags(self):
        key = "follow_tags"
        if request.query.get("force") != "1":
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, "tags": hit})
        err, tags = await self._call(client.follow_tags(), "读取关注分组失败")
        if err:
            return err
        if isinstance(tags, list):
            webcache.put(key, tags)
        return json_response({"ok": True, "tags": tags if isinstance(tags, list) else []})

    async def api_followings(self):
        """登录账号的关注列表（需登录）。

        tagid 为空 / -1 = 全部，其余为分组 id；kw 非空时按昵称模糊搜索（忽略分组与翻页）。
        分页与搜索结果都缓存（key 含参数）；force=1 跳过缓存强制请求 B 站。
        """
        pn = request.query.get("pn", 1, type=int)
        ps = request.query.get("ps", 50, type=int)
        tagid = request.query.get("tagid", "")
        kw = request.query.get("kw", "")
        key = f"followings:kw:{kw}" if kw else f"followings:{pn}:{ps}:{tagid}"
        if request.query.get("force") != "1":
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, **hit})
        err, data = await self._call(
            client.followings(pn=pn, ps=ps, tagid=tagid, kw=kw), "读取关注列表失败"
        )
        if err:
            hit = webcache.get(key)
            if hit is not None:
                return json_response({"ok": True, "cached": True, **hit})
            return err
        data = data if isinstance(data, dict) else {}
        if data:
            webcache.put(key, data)
        return json_response({"ok": True, **data})

    async def api_user(self):
        """按 UID 查 UP 主信息（未登录也可，用于「订阅」页的指定 UID 查找）。"""
        uid = request.query.get("uid", "")
        err, user = await self._call(client.user_card(uid), "查找 UP 主失败")
        if err:
            return err
        return json_response({"ok": True, "user": user if isinstance(user, dict) else {}})

    async def api_lives(self):
        """登录账号关注的、正在直播的 UP 主列表（需登录）。"""
        pn = request.query.get("pn", 1, type=int)
        ps = request.query.get("ps", 10, type=int)
        pages = request.query.get("pages", 1, type=int)
        err, data = await self._call(
            client.live_followings(pn=pn, ps=ps, pages=pages), "读取正在直播列表失败"
        )
        if err:
            return err
        return json_response({"ok": True, **(data if isinstance(data, dict) else {})})

    async def api_live_status(self):
        uid = request.query.get("uid", "")
        err, data = await self._call(client.live_status(uid), "读取直播状态失败")
        if err:
            return err
        return json_response({"ok": True, **(data if isinstance(data, dict) else {})})

    # ==================================================================
    # 推送目标
    # ==================================================================

    async def api_targets(self):
        """可用的平台实例列表（推送目标 = 平台 ID + 群 ID，群 ID 手填）。"""
        platforms = self._platforms()
        if not platforms:
            return json_response(
                {
                    "ok": False,
                    "message": "未检测到平台实例：请先在 AstrBot WebUI「消息平台」里配置并启用至少一个平台适配器",
                    "platforms": [],
                }
            )
        return json_response({"ok": True, "platforms": platforms})

    # ==================================================================
    # 动态订阅
    # ==================================================================

    async def api_subs(self):
        return json_response({"ok": True, "subs": subs.all()})

    async def api_subs_save(self):
        body = await self._body()
        try:
            ok, message, sub = subs.add(
                mid=body.get("mid"),
                uname=str(body.get("uname") or ""),
                face=str(body.get("face") or ""),
                platform_id=str(body.get("platform_id") or ""),
                platform_name=str(body.get("platform_name") or ""),
                group_id=str(body.get("group_id") or ""),
                group_name=str(body.get("group_name") or ""),
                types=body.get("types"),
                keyword=str(body.get("keyword") or ""),
            )
        except Exception as exc:
            logger.exception("bilibili 保存订阅异常")
            return error_response(f"保存订阅失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": ok, "message": message, "sub": sub})

    async def api_subs_toggle(self):
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        ok, message = subs.set_enabled(sub_id, bool(body.get("enabled", True)))
        return json_response({"ok": ok, "message": message})

    async def api_subs_types(self):
        """修改订阅的推送类型（顺带保存「图文内容过滤」）。

        body: {id, types:[...], keyword?}。`keyword` 键**存在**时一并更新内容过滤
        （空串 = 清除过滤；过滤**只作用于图文动态**）。
        """
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        if not body.get("types"):
            return error_response("至少选择一个消息类型")
        ok, message, sub = subs.set_types(sub_id, body.get("types"))
        if ok and "keyword" in body:
            ok2, msg2, sub2 = subs.set_keyword(sub_id, body.get("keyword"))
            message = f"{message}；{msg2}" if ok2 else message
            sub = sub2 or sub
        return json_response({"ok": ok, "message": message, "sub": sub})

    async def api_subs_delete(self):
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        ok, message = subs.remove(sub_id)
        if ok:
            pusher.forget(sub_id)
        return json_response({"ok": ok, "message": message})

    # ==================================================================
    # 动态推送设置
    # ==================================================================

    async def api_push_settings(self):
        return json_response({"ok": True, **pusher.status()})

    async def api_push_settings_save(self):
        body = await self._body()
        try:
            pusher.update(**{k: v for k, v in body.items() if k in pusher.DEFAULTS})
        except Exception as exc:
            logger.exception("bilibili 保存推送设置异常")
            return error_response(f"保存推送设置失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": True, "message": "推送设置已保存（立即生效）", **pusher.status()})

    async def api_push_run(self):
        """立即跑一轮推送（忽略总开关）。body 可带 `reset: true` 补推。"""
        body = await self._body()
        try:
            result = await pusher.run_cycle(force=True, reset=bool(body.get("reset")))
        except Exception as exc:
            logger.exception("bilibili 手动推送异常")
            return error_response(f"推送执行失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": bool(result.get("ok", True)), **result})

    # ==================================================================
    # 直播订阅
    # ==================================================================

    async def api_live_subs(self):
        return json_response({"ok": True, "subs": live_subs.all()})

    async def api_live_subs_save(self):
        body = await self._body()
        try:
            ok, message, sub = live_subs.add(
                mid=body.get("mid"),
                uname=str(body.get("uname") or ""),
                face=str(body.get("face") or ""),
                platform_id=str(body.get("platform_id") or ""),
                platform_name=str(body.get("platform_name") or ""),
                group_id=str(body.get("group_id") or ""),
                group_name=str(body.get("group_name") or ""),
                room_id=body.get("room_id"),
                notify_live=body.get("notify_live"),
                notify_offline=body.get("notify_offline"),
            )
        except Exception as exc:
            logger.exception("bilibili 保存直播订阅异常")
            return error_response(f"保存直播订阅失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": ok, "message": message, "sub": sub})

    async def api_live_subs_toggle(self):
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        ok, message = live_subs.set_enabled(sub_id, bool(body.get("enabled", True)))
        return json_response({"ok": ok, "message": message})

    async def api_live_subs_notify(self):
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        ok, message, sub = live_subs.set_notify(
            sub_id, notify_live=body.get("notify_live"), notify_offline=body.get("notify_offline")
        )
        return json_response({"ok": ok, "message": message, "sub": sub})

    async def api_live_subs_delete(self):
        body = await self._body()
        sub_id = str(body.get("id") or "")
        if not sub_id:
            return error_response("缺少订阅 id")
        ok, message = live_subs.remove(sub_id)
        if ok:
            live_pusher.forget(sub_id)
        return json_response({"ok": ok, "message": message})

    # ==================================================================
    # 直播推送设置
    # ==================================================================

    async def api_live_push_settings(self):
        return json_response({"ok": True, **live_pusher.status()})

    async def api_live_push_settings_save(self):
        body = await self._body()
        try:
            live_pusher.update(**{k: v for k, v in body.items() if k in live_pusher.DEFAULTS})
        except Exception as exc:
            logger.exception("bilibili 保存直播推送设置异常")
            return error_response(f"保存直播推送设置失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response(
            {"ok": True, "message": "直播推送设置已保存（立即生效）", **live_pusher.status()}
        )

    async def api_live_push_run(self):
        """立即跑一轮直播状态检查（忽略总开关）。body 可带 `reset: true` 重建基线。"""
        body = await self._body()
        try:
            result = await live_pusher.run_cycle(force=True, reset=bool(body.get("reset")))
        except Exception as exc:
            logger.exception("bilibili 手动直播推送异常")
            return error_response(f"直播推送执行失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": bool(result.get("ok", True)), **result})

    # ==================================================================
    # 命令配置 / 命令订阅 / 数据管理
    # ==================================================================

    async def api_cmds(self):
        """命令配置：可改的两条（sub/unsub）+ 内置只读的三条。"""
        return json_response(
            {
                "ok": True,
                "config": cmdsubs.cfg(),
                "static": cmdsubs.STATIC_COMMANDS,
            }
        )

    async def api_cmds_save(self):
        body = await self._body()
        if not isinstance(body, dict) or not (body.get("sub") or body.get("unsub")):
            return error_response("请求体里没有命令配置（需要 sub / unsub 字段）")
        try:
            config = cmdsubs.save_cfg(body)
        except Exception as exc:
            logger.exception("bilibili 保存命令配置异常")
            return error_response(f"保存命令配置失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": True, "message": "命令配置已保存（立即生效）", "config": config})

    async def api_cmd_subs(self):
        return json_response({"ok": True, "records": cmdsubs.all()})

    async def api_cmd_subs_delete(self):
        body = await self._body()
        rec_id = str(body.get("id") or "")
        if not rec_id:
            return error_response("缺少记录 id")
        ok, message = cmdsubs.remove(rec_id)
        if ok:
            pusher.forget(rec_id)   # 顺带清掉它的推送去重记录
        return json_response({"ok": ok, "message": message})

    async def api_cmd_subs_clear(self):
        try:
            n = cmdsubs.clear()
        except Exception as exc:
            logger.exception("bilibili 清空命令订阅异常")
            return error_response(f"清空失败：{type(exc).__name__}: {exc}", status_code=500)
        return json_response({"ok": True, "message": f"已清空 {n} 条命令订阅记录", "cleared": n})

    async def api_data_overview(self):
        """数据管理页概览：各类数据条数 + 缓存现状 + data/ 目录文件清单。"""
        files: list[dict] = []
        try:
            for p in sorted(paths.DATA_DIR.glob("*")):
                if p.is_file():
                    files.append({"name": p.name, "size": p.stat().st_size})
        except OSError:
            pass
        img_count, img_bytes = imgcache.stats()
        return json_response(
            {
                "ok": True,
                "counts": {
                    "subs": len(subs.all()),
                    "live_subs": len(live_subs.all()),
                    "cmd_subs": len(cmdsubs.all()),
                    "push_state": pusher.state_count(),
                },
                "cache": {
                    "page_cache": len(webcache.keys()),
                    "images": {"count": img_count, "bytes": img_bytes},
                },
                "files": files,
            }
        )

    async def api_data_cache_clear(self):
        body = await self._body()
        results: dict = {}
        if body.get("page"):
            webcache.clear()
            results["page"] = "页面缓存已清空"
        if body.get("images"):
            n, freed = imgcache.clear()
            results["images"] = f"已删除 {n} 个缓存图片，释放 {freed / 1024 / 1024:.1f} MB"
        if not results:
            return error_response("没有指定要清空的缓存（page / images）")
        return json_response({"ok": True, "message": "；".join(results.values()), "results": results})

    # ==================================================================
    # 订阅/取消订阅命令（触发词在「命令」页配置，群消息监听实现）
    # ==================================================================

    @staticmethod
    def _cmd_text(event: AstrMessageEvent) -> str:
        """规整群消息文本，让触发词不管带不带 @机器人 / 唤醒前缀都能命中。

        去掉开头的 `/`、`@某人 `（循环剥，兼容「/@xxx 订阅B站推送 123」这种叠法）。
        """
        t = str(event.message_str or "").strip()
        while True:
            if t[:1] == "/":
                t = t[1:].lstrip()
                continue
            m = re.match(r"@[\S]{1,32}\s+", t)
            if m:
                t = t[m.end():]
                continue
            break
        return t.strip()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def bili_sub_cmd_listener(self, event: AstrMessageEvent):
        """群内「订阅B站推送 / 取消B站推送」命令。

        订阅成功落一条 {qq_id, group_id, platform, platform_id, bilibili_id, ...}
        到 data/bilibili_cmd_subs.json（bili/cmdsubs.py），推送循环自动带上它。
        """
        text = self._cmd_text(event)
        if not text:
            return
        cfg = cmdsubs.cfg()
        platform_name = ""
        try:
            platform_name = str(event.platform_meta.name or "")
        except Exception:
            platform_name = ""
        group_id = str(event.get_group_id() or "")
        platform_id = str(event.get_platform_id() or "")
        qq_id = str(event.get_sender_id() or "")

        for kind in ("unsub", "sub"):
            c = cfg.get(kind) or {}
            if not c.get("enabled"):
                continue
            for trig in c.get("triggers") or []:
                trig = str(trig).strip()
                if not trig or not text.startswith(trig):
                    continue
                tail = text[len(trig):]
                # 触发词后面要么没有内容，要么是空白/纯数字/冒号，才算命中
                # （防止「订阅B站推送啦」这种恰好是前缀的消息误触发）
                if tail and not (tail[:1].isspace() or tail.isdigit() or tail[:1] in "：:"):
                    continue
                rest = tail.strip().lstrip("：:").strip()
                if c.get("admin_only") and not event.is_admin():
                    return   # 仅管理员命令，非管理员发的直接忽略（不回复不拦截）
                try:
                    if kind == "sub":
                        async for r in self._handle_sub_cmd(
                            event, rest, qq_id=qq_id, group_id=group_id,
                            platform=platform_name, platform_id=platform_id,
                        ):
                            yield r
                    else:
                        async for r in self._handle_unsub_cmd(
                            event, rest, group_id=group_id, platform_id=platform_id,
                        ):
                            yield r
                    event.stop_event()
                except Exception as exc:
                    logger.exception("bilibili 订阅命令处理异常")
                    yield event.plain_result(f"命令执行出错：{type(exc).__name__}: {exc}")
                return   # 命中一条触发词就结束

    async def _handle_sub_cmd(
        self, event: AstrMessageEvent, rest: str, *, qq_id: str, group_id: str,
        platform: str, platform_id: str,
    ):
        """「订阅B站推送 <UID>」：查 UP 主 → 落库 → 回执。"""
        uid = str(rest or "").strip()
        if not uid:
            yield event.plain_result(
                "用法：订阅B站推送 <UP主UID>（纯数字，或粘贴 space.bilibili.com 主页链接）\n"
                "例：订阅B站推送 946974"
            )
            return
        # 允许直接粘 space 主页链接
        if not uid.isdigit():
            m = re.search(r"(\d{5,})", uid)
            uid = m.group(1) if m else ""
        if not uid:
            yield event.plain_result("UID 没认出来：请发纯数字 UID，或粘贴 space.bilibili.com 主页链接")
            return
        uname = ""
        try:
            card = await client.user_card(uid)
            uid = str(card.get("mid") or uid)
            uname = str(card.get("uname") or "")
        except Exception as exc:
            logger.warning(f"订阅命令查 UP 主信息失败（UID {uid}）：{exc}")
            # 查不到资料也允许订阅（uname 留空，推送时显示 UID）
        ok, message, _rec = cmdsubs.add(
            qq_id=qq_id,
            group_id=group_id,
            platform=platform,
            platform_id=platform_id,
            bilibili_id=uid,
            uname=uname,
        )
        who = uname or f"UID {uid}"
        if ok:
            yield event.plain_result(
                f"{message}：{who}\n"
                f"之后该 UP 主的新动态会推送到本群；取消请发：取消B站推送 {uid}"
            )
        else:
            yield event.plain_result(f"订阅失败：{message}")

    async def _handle_unsub_cmd(
        self, event: AstrMessageEvent, rest: str, *, group_id: str, platform_id: str,
    ):
        """「取消B站推送 [UID]」：带 UID 取消单个，不带取消本群全部。"""
        uid = str(rest or "").strip()
        if uid and not uid.isdigit():
            m = re.search(r"(\d{5,})", uid)
            uid = m.group(1) if m else ""
        if uid:
            ok, message = cmdsubs.remove_uid(platform_id, group_id, uid)
            if ok:
                pusher.forget(f"cmd-{uid}-{platform_id}-{group_id}")
            yield event.plain_result(message)
            return
        # 不带 UID：取消本群全部命令订阅（先快照，删完逐条清推送记录）
        group_recs = [
            r for r in cmdsubs.all()
            if r.get("platform_id") == platform_id and r.get("group_id") == group_id
        ]
        ok, message, _n = cmdsubs.remove_group(platform_id, group_id)
        if ok:
            for r in group_recs:
                pusher.forget(str(r.get("id") or ""))
        yield event.plain_result(message)

    # ==================================================================
    # 图片代理
    # ==================================================================

    async def api_img(self):
        """B 站图片代理：后端带 Referer 抓取 + 本地缓存（页面一般直连 CDN 即可）。"""
        u = request.query.get("u", "")
        try:
            ok, data, ctype, err = await imgcache.load(u)
        except Exception as exc:
            logger.exception("bilibili 图片代理异常")
            return error_response(f"图片代理异常：{type(exc).__name__}: {exc}", status_code=500)
        if not ok or not data:
            status = 404 if ("HTTP" in err or "下载" in err) else 400
            return error_response(err or "图片内容为空", status_code=status)
        from quart import Response as QuartResponse

        return QuartResponse(
            data,
            content_type=ctype or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    # ==================================================================
    # QQ/平台命令（管理员）
    # ==================================================================

    @filter.command("b站状态", alias={"B站状态", "b站登录状态", "bilibili状态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def bili_status_cmd(self, event: AstrMessageEvent):
        """查看 B 站登录状态与未读私信。"""
        if not store.logged_in():
            yield event.plain_result("B 站未登录：请在 WebUI 插件详情页的「B站面板」扫码登录")
            return
        user = store.user()
        lines = [f"B 站已登录：{user.get('uname') or '（未知昵称）'}（UID {user.get('mid')}）"]
        try:
            unread = await client.unread()
            lines.append(
                f"未读私信：{unread.get('total', 0)}"
                f"（关注 {unread.get('follow', 0)} / 陌生人 {unread.get('unfollow', 0)}）"
            )
        except Exception as exc:
            lines.append(f"未读私信：读取失败（{exc}）")
        yield event.plain_result("\n".join(lines))

    @filter.command("b站私信", alias={"B站私信", "bilibili私信"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def bili_dm_cmd(self, event: AstrMessageEvent):
        """查看 B 站私信会话列表。"""
        if not store.logged_in():
            yield event.plain_result("B 站未登录：先在 WebUI 插件详情页的「B站面板」扫码登录")
            return
        try:
            items = await client.sessions(size=10)
        except Exception as exc:
            yield event.plain_result(f"读取私信失败：{exc}")
            return
        if not items:
            yield event.plain_result("暂无私信会话")
            return
        lines = ["B 站私信会话（最多 10 条）："]
        for s in items:
            who = s.get("name") or f"UID {s.get('talker_id')}"
            tail = f" · {s['unread']} 条未读" if s.get("unread") else ""
            lines.append(f"- {who}{tail}：{s.get('last_text') or '（无内容）'}")
        yield event.plain_result("\n".join(lines))

    @filter.command("b站动态", alias={"B站动态", "bilibili动态"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def bili_dyn_cmd(self, event: AstrMessageEvent, uid: str = ""):
        """查看某 UP 主的最近动态。用法：b站动态 [UID]"""
        try:
            data = await client.dynamics(uid)
        except Exception as exc:
            yield event.plain_result(f"读取动态失败：{exc}")
            return
        items = data.get("items") or []
        if not items:
            yield event.plain_result("没有拉到动态（该用户可能没有公开动态）")
            return
        lines = [
            f"B 站动态 · {items[0].get('author') or ('UID ' + uid)}（最近 {min(3, len(items))} 条）："
        ]
        for d in items[:3]:
            text = (d.get("text") or "").replace("\n", " ")
            if len(text) > 60:
                text = text[:60] + "…"
            lines.append(f"- [{d.get('pub_time') or ''}] {text}")
        yield event.plain_result("\n".join(lines))


# AstrBot 会自动实例化插件类（metadata.yaml 提供名称/版本等元信息）。
# bili 包在此模块导入时已完成全部数据文件加载（store / subs / pusher / live_pusher）。
_ = bili
