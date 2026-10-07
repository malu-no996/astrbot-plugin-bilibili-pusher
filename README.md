# astrbot-plugin-bilibili-pusher

B 站订阅推送插件（AstrBot）。由 malu_qq_bot 的 bilibili 插件移植而来。

## 功能

- **扫码登录**：B 站 Web 二维码登录，凭证（Cookie + refresh_token）落盘，支持续期与退出
- **私信 / 动态 / 关注 / 直播浏览**：页面里看会话列表、聊天记录、我关注的动态流、
  单个 UP 主的动态、正在直播的关注列表
- **动态推送订阅**：UP 主 → 平台实例 → 群，按类型（投稿/专栏/图文/…）与「图文需包含」
  关键词过滤，定时拉取新动态推送到指定群
- **直播推送订阅**：主播开播 / 下播事件推送（只在状态跳变时推，不刷屏）
- **防风控**：拉取间隔 ≥30 分钟（直播 ≥5 分钟）+ 随机浮动；**每一次** B 站请求之间
  都有随机间隔，全部串行排队
- **命令**（管理员）：`b站状态` / `b站私信` / `b站动态 [UID]`
- **群内订阅命令**：`订阅B站推送 <UID>` / `取消B站推送 [UID]`（触发词、开关、
  仅管理员可在「命令配置」页改；无需 @机器人，兼容 `/` 与 `@` 前缀）。
  订阅记录落 `{qq_id, group_id, platform, bilibili_id}`（另存 platform_id 用于推送），
  会与面板订阅一起参与定时推送

## 安装

把本目录放到 AstrBot 的 `plugins/` 下（或通过插件市场安装），并在 WebUI
「插件管理」里安装依赖（httpx、segno）。重启后在插件详情页打开各 Page。

## 页面

插件只有一个 Page（**B站面板**），9 个子页签：

- **账号**：扫码登录 / 续期 / 退出
- **私信**：会话列表 + 聊天记录
- **关注**：关注分组 / 昵称搜索 / 列表（可发起「动态推送」）
- **动态**：我关注的动态流 / 单个 UP 主动态
- **直播**：正在直播的关注列表（可发起「直播订阅」）
- **动态订阅**：订阅管理 + 自动推送设置（间隔 / 过期 / 请求节流）
- **直播订阅**：订阅管理 + 定时检查设置
- **命令**：两条群内订阅命令的触发词 / 开关 / 权限配置（内置三条管理员命令只读展示）
- **数据**：数据概览 / 缓存清理（页面缓存、图片缓存）/ 命令订阅记录管理 / data/ 文件清单

## 推送目标

订阅目标 = **平台实例 ID + 群会话 ID**（AstrBot 的 `unified_msg_origin` 格式
`平台ID:GroupMessage:群ID`）。在「消息平台」页能看到平台实例 ID；群 ID 就是该平台
群消息事件里的 session_id（如 aiocqhttp 的 QQ 群号）。

## 与原版（malu_qq_bot）的差异

- 推送统一走 AstrBot 的 `context.send_message()`（文字 + 图片），
  不再做 NoneBot OneBot 专用的 JSON 小程序卡，也不做 QQ 官方机器人 Markdown/Ark 通道
- 推送目标不再依赖「群号映射」模块（QQ 官方机器人场景请配合 AstrBot 的官方适配器自行处理）
- 凭证文件为明文 JSON 落在插件 `data/` 目录（原版有 Fernet 整文件加密）

## 数据文件

均在插件目录 `data/` 下：`bilibili_auth.json`（凭证）、`bilibili_subs.json`、
`bilibili_push.json` / `bilibili_push_state.json`、`bilibili_live_subs.json`、
`bilibili_live_push.json` / `bilibili_live_state.json`、`bilibili_cache/`（图片缓存）。
