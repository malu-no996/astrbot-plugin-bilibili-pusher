/* B 站面板（astrbot-plugin-bilibili-pusher/pages/panel/app.js）
 * 由 malu_qq_bot bilibili 插件的 11 个 Vue frag 合并移植：原生 JS + AstrBot bridge。
 * 子页签：账号 / 私信 / 关注 / 动态 / 直播 / 动态订阅 / 直播订阅
 * 弹窗：动态推送 / 推送条件 / 直播订阅 / 推送事件
 *
 * 图片直连 B 站 CDN：referrerpolicy="no-referrer"（B 站 CDN 对空 Referer 放行）。
 */
const B = window.AstrBotPluginPage;

/* ---------------- 工具 ---------------- */

const KINDS = [
  { v: 'archive', t: '投稿' }, { v: 'article', t: '专栏' }, { v: 'pgc', t: '影视' },
  { v: 'opus', t: '图文' }, { v: 'draw', t: '相册' }, { v: 'live', t: '直播' },
  { v: 'common', t: '文字' },
];
const kindText = (v) => { const k = KINDS.find(x => x.v === v); return k ? k.t : (v || ''); };

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function fmt(ts) {
  ts = Number(ts || 0);
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function short(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n) + '…' : s;
}
/* B 站 CDN 直连：no-referrer 绕过 Referer 防盗链 */
function img(url, cls, alt) {
  if (!url) return '';
  return `<img src="${esc(url)}" referrerpolicy="no-referrer" class="${cls || ''}" alt="${esc(alt || '')}" loading="lazy">`;
}
function openImg(url) { if (url) window.open(url, '_blank'); }

async function GET(path, params) {
  try { return await B.apiGet(path, params || {}); }
  catch (e) { return { ok: false, message: e.message }; }
}
async function POST(path, body) {
  try { return await B.apiPost(path, body || {}); }
  catch (e) { return { ok: false, message: e.message }; }
}

function notify(msg, type) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}
function confirmBox(msg) { return window.confirm(msg); }

/* ---------------- 状态（对应原 frag 的 init） ---------------- */

const S = {
  tab: 'account',
  state: { logged: false, user: {}, login_at: 0, expires_at: 0, unread: {}, has_refresh_token: false, qr_ready: true },
  busy: false,
  login: { busy: false, tip: '', key: '', url: '', timer: null },
  sessions: { loading: false, list: [], error: '' },
  msgs: { loading: false, talker: '', name: '', list: [], error: '' },
  follow: { loading: false, list: [], total: 0, pn: 1, error: '', tags: [], tagid: -1, kw: '', searching: false, scanned: 0, total_all: 0, fromCache: false },
  dyn: { loading: false, source: 'follow', type: 'all', uid: '', uname: '', offset: '', list: [], error: '' },
  lives: { loading: false, list: [], total_live: 0, error: '', scanned: 0, has_more: false, next_pn: 1 },
  subs: { loading: false, list: [], error: '' },
  livesubs: { loading: false, list: [], error: '' },
  finder: { uid: '', loading: false, user: null, error: '' },
  livefinder: { uid: '', loading: false, user: null, error: '' },
  pusher: { enabled: false, interval_minutes: 120, jitter_minutes: 15, max_age_hours: 24, request_gap_seconds: 2, mode: 'auto', push_images: true, last_run: 0, next_run: 0, last_checked: 0, last_pushed: 0, last_error: '', running: false, saving: false, loading: false, message: '', details: [] },
  livepusher: { enabled: false, interval_minutes: 10, jitter_minutes: 3, request_gap_seconds: 2, mode: 'auto', push_images: true, last_run: 0, next_run: 0, last_checked: 0, last_pushed: 0, last_error: '', running: false, saving: false, loading: false, message: '', details: [] },
  // 弹窗
  push: { show: false, mid: 0, uname: '', face: '', platforms: [], platform_id: '', group_id: '', group_name: '', types: ['archive'], keyword: '', loading: false, saving: false, error: '' },
  types: { show: false, id: '', uname: '', list: [], keyword: '', saving: false, error: '' },
  livepush: { show: false, mid: 0, uname: '', face: '', room_id: 0, platforms: [], platform_id: '', group_id: '', group_name: '', notify_live: true, notify_offline: true, loading: false, saving: false, error: '' },
  livenotify: { show: false, id: '', uname: '', notify_live: true, notify_offline: true, saving: false, error: '' },
};

/* ---------------- 账号（frag/account） ---------------- */

async function refreshState() {
  S.busy = true;
  const j = await GET('state');
  if (j.ok) S.state = Object.assign({ logged: false, user: {}, login_at: 0, expires_at: 0, unread: {}, has_refresh_token: false, qr_ready: true }, j);
  else notify(j.message || '读取 B 站状态失败', 'err');
  S.busy = false;
  render();
}

async function loginStart() {
  const L = S.login;
  L.busy = true; L.tip = '正在获取二维码…'; L.png = ''; L.url = '';
  render();
  const j = await POST('login/start');
  if (!j.ok) { L.busy = false; L.tip = j.message || '获取二维码失败'; notify(j.message || '获取二维码失败', 'err'); render(); return; }
  L.key = j.qrcode_key;
  L.url = j.url;
  L.png = j.png || '';
  L.tip = '请用哔哩哔哩 App 扫码（' + (j.expires_in || 180) + ' 秒内有效）';
  L.busy = false;
  render();
  clearInterval(L.timer);
  L.timer = setInterval(async () => {
    const p = await GET('login/poll', { k: L.key });
    if (p.status === 'success') {
      clearInterval(L.timer);
      L.png = ''; L.url = ''; L.tip = '';
      notify(p.message || 'B 站登录成功', 'ok');
      await refreshState();
      loadSessions();   // 登录后顺手把私信会话拉出来
    } else if (p.status === 'scanned') { L.tip = '已扫码，请在手机上确认'; render(); }
    else if (p.status === 'waiting') { L.tip = '等待扫码…'; render(); }
    else if (!p.status) { /* 轮询中断（网络抖动等）不影响页面，下个周期继续 */ }
    else { clearInterval(L.timer); L.tip = p.message || '二维码已失效，请重新获取'; notify(L.tip, 'err'); render(); }
  }, 2000);
}

async function logout() {
  if (!confirmBox('确定退出 B 站登录？本地保存的 Cookie 会被清除。')) return;
  const j = await POST('logout');
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) {
    S.state = Object.assign({}, S.state, { logged: false, user: {}, unread: {}, login_at: 0, expires_at: 0 });
    S.sessions.list = []; S.msgs = { loading: false, talker: '', name: '', list: [], error: '' };
    followClearCache(); S.follow.list = []; S.follow.tags = []; S.follow.tagid = -1;
    dynReset(); S.finder.user = null; S.livefinder.user = null;
    render();
  }
}

async function refreshToken() {
  const j = await POST('refresh');
  notify(j.message, j.ok ? 'ok' : 'err');
  refreshState();
}

function renderAccount() {
  const st = S.state;
  if (st.logged) {
    return `<div class="flex" style="gap:12px">
      ${st.user.face ? img(st.user.face, 'face face-46') : ''}
      <div>
        <b>${esc(st.user.uname || '（未知昵称）')}</b> <span class="muted">UID ${esc(st.user.mid)}</span>
        <div class="muted">登录于 ${fmt(st.login_at)}${st.expires_at ? ' · 有效期至 ' + fmt(st.expires_at) : ''}${st.unread && st.unread.total ? ' · 未读私信 ' + st.unread.total : ''}</div>
        ${st.unread_error ? `<div class="err">${esc(st.unread_error)}</div>` : ''}
      </div>
      <div class="flex" style="margin-left:auto">
        <button class="ghost" data-act="refreshState" ${S.busy ? 'disabled' : ''}>刷新</button>
        <button class="ghost" data-act="refreshToken" ${st.has_refresh_token ? '' : 'disabled'}>续期</button>
        <button class="red" data-act="logout">退出登录</button>
      </div>
    </div>`;
  }
  const L = S.login;
  return `<p class="muted">未登录。点「扫码登录」后用<b>哔哩哔哩 App</b> 扫码确认（3 分钟内有效）。</p>
    <div class="flex">
      <button data-act="loginStart" ${L.busy ? 'disabled' : ''}>${L.busy ? '获取中…' : '扫码登录'}</button>
      ${st.qr_ready === false ? '<span class="muted">未安装二维码依赖 segno（未安装时以链接形式登录）</span>' : ''}
    </div>
    ${L.url ? `<div style="margin-top:12px">
      ${L.png ? `<img src="${esc(L.png)}" width="180" height="180" alt="登录二维码" class="qr-img">` : ''}
      <div class="muted" style="margin-top:6px">${esc(L.tip)}</div>
      <div class="muted">二维码显示不出来？<a href="${esc(L.url)}" target="_blank">点此用浏览器打开登录链接</a></div>
    </div>` : ''}`;
}

/* ---------------- 私信（frag/dm） ---------------- */

async function loadSessions() {
  S.sessions.loading = true; S.sessions.error = '';
  render();
  const j = await GET('sessions');
  if (j.ok) S.sessions.list = j.sessions || [];
  else S.sessions.error = j.message || '读取私信会话失败';
  S.sessions.loading = false;
  render();
}

async function openSession(s) {
  S.msgs.talker = s.talker_id; S.msgs.name = s.name;
  S.msgs.loading = true; S.msgs.error = '';
  render();
  const j = await GET('messages', { talker_id: s.talker_id });
  if (j.ok) S.msgs.list = j.messages || [];
  else { S.msgs.list = []; S.msgs.error = j.message || '读取消息失败'; }
  S.msgs.loading = false;
  render();
}

function renderDm() {
  const se = S.sessions;
  let html = `<div class="flex">
    <button class="ghost" data-act="loadSessions" ${!S.state.logged || se.loading ? 'disabled' : ''}>${se.loading ? '加载中…' : '刷新会话'}</button>
    <span class="muted">${S.state.logged
      ? `未读：关注 ${(S.state.unread && S.state.unread.follow) || 0} / 陌生人 ${(S.state.unread && S.state.unread.unfollow) || 0}`
      : '需先登录'}</span>
  </div>`;
  if (se.error) html += `<p class="err">${esc(se.error)}</p>`;
  if (se.list.length) {
    html += `<table style="margin-top:10px"><thead><tr><th>会话</th><th>最后一条</th><th style="width:70px">未读</th><th style="width:120px">时间</th></tr></thead><tbody>`;
    for (const s of se.list) {
      html += `<tr class="clickable" data-act="openSession" data-arg="${esc(s.talker_id)}">
        <td><div class="flex" style="gap:8px">${s.face ? img(s.face, 'face face-28') : ''}
          <div><b>${esc(s.name || 'UID ' + s.talker_id)}</b><div class="muted">${esc(s.talker_id)}</div></div></div></td>
        <td class="muted">${esc(short(s.last_text, 26))}</td>
        <td>${s.unread ? `<span class="badge online">${esc(s.unread)}</span>` : ''}</td>
        <td class="muted">${fmt(s.ts)}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  const m = S.msgs;
  if (m.talker) {
    html += `<div style="margin-top:14px"><h2 style="margin-bottom:8px">与 ${esc(m.name || m.talker)} 的消息 <span class="hint">最近 ${m.list.length} 条</span></h2>`;
    if (m.error) html += `<p class="err">${esc(m.error)}</p>`;
    if (m.loading) html += `<p class="muted">加载中…</p>`;
    html += `<div class="dm-list">`;
    for (const msg of m.list) {
      html += `<div style="margin-bottom:10px"><div class="muted">${msg.self ? '我' : esc(m.name || msg.sender_uid)} · ${fmt(msg.ts)}</div>
        <div class="dm-bubble ${msg.self ? 'me' : 'other'}">${esc(msg.text)}</div></div>`;
    }
    html += `</div></div>`;
  }
  return html;
}

/* ---------------- 关注（frag/follow） ---------------- */

const FOLLOW_CACHE_KEY = 'astrbot_bili_follow_cache_v1';

function followSaveCache() {
  try {
    localStorage.setItem(FOLLOW_CACHE_KEY, JSON.stringify({
      at: Math.floor(Date.now() / 1000), tagid: S.follow.tagid, pn: S.follow.pn,
      total: S.follow.total, tags: S.follow.tags, list: S.follow.list,
    }));
  } catch (e) { /* ignore */ }
}
function followLoadCache() {
  try {
    const raw = localStorage.getItem(FOLLOW_CACHE_KEY);
    if (!raw) return false;
    const c = JSON.parse(raw);
    if (!c || !Array.isArray(c.list) || !c.list.length) return false;
    S.follow.list = c.list; S.follow.total = c.total || 0; S.follow.pn = c.pn || 1;
    S.follow.tags = Array.isArray(c.tags) ? c.tags : [];
    S.follow.tagid = typeof c.tagid === 'number' ? c.tagid : -1;
    S.follow.fromCache = true;
    return true;
  } catch (e) { return false; }
}
function followClearCache() {
  try { localStorage.removeItem(FOLLOW_CACHE_KEY); } catch (e) { /* ignore */ }
  S.follow.fromCache = false;
}

async function followTags() {
  if (!S.state.logged) { S.follow.error = '读取关注分组需要先登录'; render(); return; }
  const j = await GET('follow_tags');
  if (j.ok) {
    S.follow.tags = j.tags || [];
    if (!S.follow.tags.some(t => t.tagid === S.follow.tagid)) S.follow.tagid = -1;
    followSaveCache();
    render();
  }
}
function followTag(tagid) {
  if (S.follow.tagid === tagid && !S.follow.searching) return;
  S.follow.tagid = tagid; S.follow.pn = 1; S.follow.kw = ''; S.follow.searching = false;
  loadFollowings(1);
}
async function followSearch() {
  const kw = (S.follow.kw || '').trim();
  if (!kw) { S.follow.searching = false; loadFollowings(1); return; }
  if (!S.state.logged) { S.follow.error = '搜索关注需要先登录'; render(); return; }
  S.follow.loading = true; S.follow.error = ''; render();
  const j = await GET('followings', { kw, ps: 50 });
  if (j.ok) {
    S.follow.list = j.items || []; S.follow.total = j.total || 0; S.follow.pn = 1;
    S.follow.searching = true; S.follow.scanned = j.scanned || 0; S.follow.total_all = j.total_all || 0;
    S.follow.fromCache = false;
  } else S.follow.error = j.message || '搜索关注失败';
  S.follow.loading = false; render();
}
async function loadFollowings(pn) {
  if (!S.state.logged) { S.follow.error = '查看关注列表需要先登录'; render(); return; }
  S.follow.loading = true; S.follow.error = ''; render();
  const params = { pn: pn || 1, ps: 50 };
  if (S.follow.tagid !== -1) params.tagid = S.follow.tagid;
  const j = await GET('followings', params);
  if (j.ok) {
    S.follow.list = j.items || []; S.follow.total = j.total || 0; S.follow.pn = j.pn || 1;
    S.follow.fromCache = false;
    followSaveCache();
  } else S.follow.error = j.message || '读取关注列表失败';
  S.follow.loading = false; render();
}
function followDyn(u) { dynSpace(u.mid, u.uname); }

function renderFollow() {
  const f = S.follow;
  let html = `<div class="row" style="margin-bottom:8px">
    <label class="muted">搜索关注</label>
    <input type="text" data-model="follow.kw" value="${esc(f.kw)}" placeholder="按昵称模糊搜索（回车确认），清空恢复分页" style="flex:1;max-width:340px">
    <button class="ghost" data-act="followSearch" ${!S.state.logged || f.loading ? 'disabled' : ''}>${f.loading ? '加载中…' : '搜索'}</button>
    <button class="ghost" data-act="followTags" ${!S.state.logged || f.loading ? 'disabled' : ''}>刷新分组</button>
    <button class="ghost" data-act="followings1" ${!S.state.logged || f.loading ? 'disabled' : ''}>刷新列表</button>
  </div>`;
  html += `<div class="flex" style="margin-bottom:8px">
    ${f.total ? `<span class="muted">共 ${f.total} 个 · 第 ${f.pn} 页</span>` : (S.state.logged ? '' : '<span class="muted">需先在「账号」页签扫码登录</span>')}
    ${f.searching ? `<span class="muted">（搜索模式：已扫描 ${f.scanned} 个${f.scanned < f.total_all ? ' / 共 ' + f.total_all + ' 个关注' : ''}）</span>` : ''}
    ${f.fromCache ? '<span class="badge" style="background:var(--warn-bg);color:var(--warn-text)">本地缓存 · 点「刷新列表」更新</span>' : ''}
  </div>`;
  if (f.tags.length) {
    html += `<div class="flex" style="margin-bottom:12px">`;
    for (const t of f.tags) {
      html += `<span class="tab ${f.tagid === t.tagid ? 'active' : ''}" data-act="followTag" data-arg="${t.tagid}" style="padding:5px 12px;border:1px solid var(--line);border-radius:8px">${esc(t.name)} ${t.count}</span>`;
    }
    html += `</div>`;
  }
  if (f.error) html += `<p class="err">${esc(f.error)}</p>`;
  if (!f.list.length && !f.loading && !f.error && S.state.logged) {
    html += `<p class="muted">尚未加载关注列表（不再自动拉取）。点上方 <b>「刷新列表」</b> 拉取，拉过一次后会缓存在本地。</p>`;
  }
  if (f.list.length) {
    html += `<div class="grid grid-follow">`;
    for (const u of f.list) {
      html += `<div class="ucard">
        <div class="flex" style="gap:10px;align-items:flex-start">
          ${img(u.face, 'face face-44')}
          <div style="min-width:0;flex:1">
            <div class="name-row"><b title="${esc(u.uname)}">${esc(u.uname)}</b>
              ${u.official ? `<span class="badge">${esc(u.official)}</span>` : ''}
              ${u.special ? '<span class="badge online">特别关注</span>' : ''}</div>
            <div class="muted">UID ${esc(u.mid)}</div>
            <div class="muted">${esc(short(u.sign, 40)) || '（无签名）'}</div>
          </div>
        </div>
        <div class="foot">
          <button class="ghost" data-act="followDyn" data-arg="${u.mid}">看 TA 的动态</button>
          <button data-act="pushOpen" data-arg="${u.mid}">动态推送</button>
        </div></div>`;
    }
    html += `</div>`;
    if (!f.searching) {
      html += `<div class="flex pager">
        <button class="ghost" data-act="followPrev" ${f.pn <= 1 || f.loading ? 'disabled' : ''}>上一页</button>
        <button class="ghost" data-act="followNext" ${f.loading ? 'disabled' : ''}>下一页</button></div>`;
    }
  }
  return html;
}

/* ---------------- 动态（frag/feed） ---------------- */

function dynReset() { S.dyn.list = []; S.dyn.offset = ''; S.dyn.error = ''; }

async function loadDynamics(offset) {
  const d = S.dyn;
  const follow = d.source === 'follow';
  if (follow && !S.state.logged) { d.error = '查看「我关注的」动态需要先扫码登录'; render(); return; }
  const params = {};
  if (offset) params.offset = offset;
  if (follow) { params.type = d.type || 'all'; }
  else {
    const uid = (d.uid || '').trim();
    if (!uid) { d.error = '缺少 UP 主 UID'; render(); return; }
    params.uid = uid;
  }
  d.loading = true; d.error = ''; render();
  const j = await GET(follow ? 'feed' : 'dynamics', params);
  if (j.ok) {
    d.list = offset ? d.list.concat(j.items || []) : (j.items || []);
    d.offset = j.offset || '';
  } else d.error = j.message || '读取动态失败';
  d.loading = false; render();
}
function dynSpace(mid, uname) {
  dynReset();
  S.dyn.source = 'space'; S.dyn.uid = String(mid || ''); S.dyn.uname = uname || '';
  S.tab = 'feed';
  loadDynamics('');
}
function dynBack() {
  dynReset();
  S.dyn.source = 'follow'; S.dyn.uname = '';
  loadDynamics('');
}

function dynCard(d) {
  let imgs = '';
  if (d.images && d.images.length === 1) {
    imgs = `<img src="${esc(d.images[0])}" referrerpolicy="no-referrer" alt="" class="feed-cover" data-act="openImg" data-arg="${esc(d.images[0])}">`;
  } else if (d.images && d.images.length > 1) {
    imgs = `<div class="feed-imgs">` + d.images.map(im =>
      `<img src="${esc(im)}" referrerpolicy="no-referrer" alt="" class="feed-thumb" data-act="openImg" data-arg="${esc(im)}">`).join('') + `</div>`;
  }
  return `<div class="ucard">
    <div class="flex" style="gap:8px">
      ${img(d.face, 'face face-38')}
      <div style="min-width:0;flex:1">
        <b style="font-size:14px">${esc(d.author)}</b>
        <div class="muted">${esc(d.pub_time || fmt(d.ts))}${d.action ? ' · ' + esc(d.action) : ''}</div>
      </div>
      ${d.url ? `<a href="${esc(d.url)}" target="_blank" class="muted" style="font-size:12px">原动态</a>` : ''}
    </div>
    ${imgs}
    ${d.title ? `<div style="font-weight:bold;font-size:14px">${esc(d.title)}</div>` : ''}
    ${d.text ? `<div class="dyn-text" style="font-size:13px">${esc(d.text)}</div>` : ''}
    <div class="dyn-foot"><span>转发 ${(d.stat && d.stat.forward) || 0}</span><span>评论 ${(d.stat && d.stat.comment) || 0}</span><span>点赞 ${(d.stat && d.stat.like) || 0}</span></div>
  </div>`;
}

function renderFeed() {
  const d = S.dyn;
  let html = `<div class="flex" style="margin-bottom:6px">`;
  if (d.source === 'space') {
    html += `<span class="badge">正在查看 ${esc(d.uname || 'UID ' + d.uid)} 的动态</span>
      <button class="ghost" data-act="dynBack" ${d.loading ? 'disabled' : ''}>返回我关注的</button>`;
  } else {
    html += `<label class="muted">动态类型</label>
      <select data-model="dyn.type" class="sel">
        <option value="all"${d.type === 'all' ? ' selected' : ''}>全部</option>
        <option value="video"${d.type === 'video' ? ' selected' : ''}>投稿</option>
        <option value="pgc"${d.type === 'pgc' ? ' selected' : ''}>影视</option>
        <option value="article"${d.type === 'article' ? ' selected' : ''}>专栏</option>
      </select>
      <button data-act="dynLoad" ${d.loading ? 'disabled' : ''}>${d.loading ? '拉取中…' : '拉取动态'}</button>
      ${!S.state.logged ? '<span class="muted">查看「我关注的」需先在「账号」页签扫码登录；或到「动态订阅」页用 UID 查找 UP 主后点「看 TA 的动态」</span>' : ''}`;
  }
  html += `</div>`;
  if (d.error) html += `<p class="err">${esc(d.error)}</p>`;
  if (!d.list.length && !d.loading && !d.error) html += `<p class="muted">点「拉取动态」加载</p>`;
  if (d.list.length) html += `<div class="grid grid-feed">` + d.list.map(dynCard).join('') + `</div>`;
  if (d.offset) html += `<div class="actions"><button class="ghost" data-act="dynMore" ${d.loading ? 'disabled' : ''}>加载更多</button></div>`;
  return html;
}

/* ---------------- 直播（frag/lives） ---------------- */

async function loadLives(more) {
  if (!S.state.logged) { S.lives.error = '查看正在直播的 UP 主需要先登录'; render(); return; }
  const L = S.lives;
  const pn = more ? (L.next_pn || 1) : 1;
  L.loading = true; L.error = ''; render();
  const j = await GET('lives', { pn, ps: 10, pages: 3 });
  if (j.ok) {
    const items = j.items || [];
    L.list = more ? L.list.concat(items) : items;
    L.total_live = j.live_count || 0;
    L.scanned = (more ? L.scanned : 0) + (j.scanned || 0);
    L.has_more = !!j.has_more;
    L.next_pn = j.next_pn || (pn + 3);
  } else L.error = j.message || '读取正在直播列表失败';
  L.loading = false; render();
}

function renderLives() {
  const L = S.lives;
  let html = `<div class="flex" style="margin-bottom:12px">
    <button data-act="livesLoad" ${!S.state.logged || L.loading ? 'disabled' : ''}>${L.loading ? '拉取中…' : '刷新直播列表'}</button>
    ${L.has_more ? `<button class="ghost" data-act="livesMore" ${L.loading ? 'disabled' : ''}>${L.loading ? '拉取中…' : '继续加载更多'}</button>` : ''}
    <span class="muted">共 ${L.list.length} 个正在直播${L.total_live ? `（接口报告 ${L.total_live} 个）` : ''}</span>
    ${L.scanned && L.has_more ? `<span class="muted">已扫 ${L.scanned} 个关注</span>` : ''}
    ${!S.state.logged ? '<span class="muted">需先在「账号」页签扫码登录</span>' : ''}
  </div>`;
  if (L.error) html += `<p class="err">${esc(L.error)}</p>`;
  if (!L.list.length && !L.loading && !L.error) {
    html += `<p class="muted">点「刷新直播列表」加载（只显示你关注的、当前正在直播的 UP 主）${L.scanned ? `；已扫过 ${L.scanned} 个关注但没人在播` : ''}</p>`;
  }
  if (L.list.length) {
    html += `<div class="grid grid-live">`;
    for (const l of L.list) {
      html += `<div class="ucard">
        <div class="flex" style="gap:10px">
          ${img(l.face, 'face face-38')}
          <div style="flex:1;min-width:0">
            <div class="name-row"><b title="${esc(l.uname || 'UID ' + l.mid)}">${esc(l.uname || 'UID ' + l.mid)}</b><span class="badge online">直播中</span></div>
            <div class="muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(l.area_name || '')}${l.online_text ? ' · 人气 ' + esc(l.online_text) : (l.online ? ' · 人气 ' + l.online : '')}</div>
          </div>
          ${l.url ? `<a href="${esc(l.url)}" target="_blank" class="muted" style="font-size:12px;white-space:nowrap">直播间 ↗</a>` : ''}
        </div>
        ${l.cover ? `<img src="${esc(l.cover)}" referrerpolicy="no-referrer" alt="" class="feed-cover" data-act="openImg" data-arg="${esc(l.cover)}">` : ''}
        ${l.title ? `<div class="dyn-text" style="font-size:13px;-webkit-line-clamp:2" title="${esc(l.title)}">${esc(l.title)}</div>` : ''}
        <div class="foot"><button class="ghost" data-act="livePushOpen" data-arg="${l.mid}">直播订阅</button></div>
      </div>`;
    }
    html += `</div>`;
  }
  return html;
}

/* ---------------- 动态订阅（frag/subs + 弹窗） ---------------- */

async function findUser() {
  const f = S.finder;
  const uid = (f.uid || '').trim();
  if (!uid) { f.error = '请输入 UP 主 UID'; render(); return; }
  f.loading = true; f.error = ''; render();
  const j = await GET('user', { uid });
  if (j.ok) { f.user = j.user || null; if (!j.user) f.error = '没有找到该用户'; }
  else { f.user = null; f.error = j.message || '查找失败'; }
  f.loading = false; render();
}

async function loadSubs() {
  S.subs.loading = true; S.subs.error = '';
  const j = await GET('subs');
  if (j.ok) S.subs.list = j.subs || [];
  else S.subs.error = j.message || '读取动态订阅列表失败';
  S.subs.loading = false;
  render();
}
async function subToggle(s) {
  const j = await POST('subs/toggle', { id: s.id, enabled: !s.enabled });
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) loadSubs();
}
async function subDelete(s) {
  if (!confirmBox(`确定删除动态订阅「${s.uname || s.mid}」→ 群 ${s.group_name || s.group_id}？`)) return;
  const j = await POST('subs/delete', { id: s.id });
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) loadSubs();
}

function pusherApply(j) {
  for (const k of ['enabled', 'interval_minutes', 'jitter_minutes', 'max_age_hours', 'request_gap_seconds', 'mode', 'push_images', 'last_run', 'next_run', 'last_checked', 'last_pushed', 'last_error', 'running']) {
    if (j[k] !== undefined && j[k] !== null) S.pusher[k] = j[k];
  }
}
async function pushCfgLoad() {
  S.pusher.loading = true; S.pusher.message = ''; render();
  const j = await GET('push/settings');
  if (j.ok) pusherApply(j);
  else S.pusher.message = j.message || '读取推送设置失败';
  S.pusher.loading = false; render();
}
async function pushCfgSave() {
  const p = S.pusher;
  p.saving = true; p.message = ''; render();
  const j = await POST('push/settings', {
    enabled: !!p.enabled,
    interval_minutes: Number(p.interval_minutes) || 120,
    jitter_minutes: Number(p.jitter_minutes) || 0,
    max_age_hours: Number(p.max_age_hours) || 0,
    request_gap_seconds: Number(p.request_gap_seconds) || 0,
    mode: p.mode || 'auto',
    push_images: !!p.push_images,
  });
  if (j.ok) { pusherApply(j); notify(j.message || '推送设置已保存（立即生效）', 'ok'); }
  else notify(j.message || '保存推送设置失败', 'err');
  p.saving = false; render();
}
async function pushRunNow(reset) {
  if (!S.subs.list.some(s => s.enabled)) { notify('没有启用的订阅，先把订阅启用后再试', 'err'); return; }
  const tip = reset
    ? '重置推送记录：清空各订阅的「已推送」标记，把未过期（≤ 过期阈值）的动态重新推一遍（单个订阅一轮最多 5 条）。确定继续？'
    : '立即对所有启用的订阅跑一轮？注意：首次运行的订阅只建立基线，不会推送历史动态。';
  if (!confirmBox(tip)) return;
  S.pusher.running = true; S.pusher.message = '正在逐个拉取…'; S.pusher.details = []; render();
  const j = await POST('push/run', { reset: !!reset });
  const msg = j.message || (j.ok ? '执行完成' : '执行失败');
  const details = j.details || [];
  await pushCfgLoad();
  S.pusher.details = details; S.pusher.message = msg;
  notify(msg, j.ok ? 'ok' : 'err');
  S.pusher.running = false; render();
}
function pushCountdown() {
  const left = Number(S.pusher.next_run || 0) - Math.floor(Date.now() / 1000);
  if (!S.pusher.enabled || left <= 0) return '';
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60);
  return h ? `约 ${h} 小时 ${m} 分钟后` : `约 ${m} 分钟后`;
}

function renderSubs() {
  let html = '';
  // 指定 UID 查找
  const f = S.finder;
  html += `<div class="card"><div class="row" style="margin-bottom:0">
    <label class="muted">指定 UID</label>
    <input type="text" data-model="finder.uid" value="${esc(f.uid)}" placeholder="UP 主 UID（纯数字，或直接粘贴 space.bilibili.com 主页链接）" style="flex:1;max-width:400px">
    <button data-act="findUser" ${f.loading || !f.uid.trim() ? 'disabled' : ''}>${f.loading ? '查找中…' : '查找'}</button>
  </div>`;
  if (f.error) html += `<p class="err" style="margin:8px 0 0">${esc(f.error)}</p>`;
  if (f.user) {
    html += `<div class="ucard" style="max-width:420px;margin-top:10px">
      <div class="flex" style="gap:10px;align-items:flex-start">
        ${img(f.user.face, 'face face-44')}
        <div style="min-width:0;flex:1">
          <div class="name-row"><b>${esc(f.user.uname)}</b>${f.user.official ? `<span class="badge">${esc(f.user.official)}</span>` : ''}</div>
          <div class="muted">UID ${esc(f.user.mid)}${f.user.followers ? ' · 粉丝 ' + f.user.followers : ''}</div>
          <div class="muted">${esc(short(f.user.sign, 40)) || '（无签名）'}</div>
        </div>
      </div>
      <div class="foot">
        <button class="ghost" data-act="followDyn" data-arg="${f.user.mid}">看 TA 的动态</button>
        <button data-act="pushOpen" data-arg="${f.user.mid}">动态推送</button>
      </div></div>`;
  }
  html += `</div>`;

  // 订阅列表
  const sb = S.subs;
  html += `<div class="flex" style="margin-bottom:8px">
    <button class="ghost" data-act="loadSubs" ${sb.loading ? 'disabled' : ''}>${sb.loading ? '加载中…' : '刷新动态订阅列表'}</button>
    <span class="muted">点「动态推送」把 UP 主的新动态订阅到指定群</span></div>`;
  if (sb.error) html += `<p class="err">${esc(sb.error)}</p>`;
  if (!sb.list.length && !sb.loading && !sb.error) html += `<p class="muted">还没有动态订阅</p>`;
  if (sb.list.length) {
    html += `<table><thead><tr><th style="width:26%">UP 主</th><th>推送目标</th><th style="width:150px">推送条件</th><th style="width:80px">状态</th><th style="width:150px">操作</th></tr></thead><tbody>`;
    for (const s of sb.list) {
      html += `<tr>
        <td><div class="flex" style="gap:8px">${img(s.face, 'face face-30')}
          <div style="min-width:0"><b>${esc(s.uname || 'UID ' + s.mid)}</b><div class="muted">UID ${esc(s.mid)}</div></div></div></td>
        <td>${esc(s.platform_name || s.platform_id)}<div class="muted">群 ${esc(s.group_name || s.group_id)}</div><div class="muted">${esc(s.group_id)}</div></td>
        <td>${(s.types || []).map(t => `<span class="badge" style="margin:0 4px 4px 0">${kindText(t)}</span>`).join('') || '<span class="muted">投稿</span>'}
          ${s.keyword ? `<div class="muted" style="white-space:pre-line" title="${esc(s.keyword)}">图文只推含：${esc(short(s.keyword, 40))}</div>` : ''}</td>
        <td><span class="badge ${s.enabled ? 'online' : 'offline'}">${s.enabled ? '已启用' : '已停用'}</span></td>
        <td><button class="ghost" style="padding:4px 8px" data-act="typesOpen" data-arg="${esc(s.id)}">条件</button>
          <button class="ghost" style="padding:4px 8px" data-act="subToggle" data-arg="${esc(s.id)}">${s.enabled ? '停用' : '启用'}</button>
          <button class="red" style="padding:4px 8px" data-act="subDelete" data-arg="${esc(s.id)}">删除</button></td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  // 自动推送设置
  const p = S.pusher;
  html += `<div class="card" style="margin-top:18px"><h2>自动推送设置 <span class="hint">定时逐个拉取动态订阅里 UP 主的新动态，推到指定群</span></h2>
    <div class="row"><label class="muted">启用自动推送</label>
      <label class="check"><input type="checkbox" data-model="pusher.enabled" ${p.enabled ? 'checked' : ''}> ${p.enabled ? '已启用' : '已停用（不会自动拉取）'}</label></div>
    <div class="row"><label class="muted">拉取间隔（分钟）</label>
      <input type="number" min="30" max="1440" data-model="pusher.interval_minutes" value="${esc(p.interval_minutes)}" class="num">
      <span class="muted">不得小于 30 分钟，最大 1440（24 小时），默认 120（2 小时）</span></div>
    <div class="row"><label class="muted">随机浮动（± 分钟）</label>
      <input type="number" min="0" max="120" data-model="pusher.jitter_minutes" value="${esc(p.jitter_minutes)}" class="num">
      <span class="muted">实际等待 = 间隔 ± 随机浮动，不会每次都在同一秒触发</span></div>
    <div class="row"><label class="muted">过期不推送（小时）</label>
      <input type="number" min="0" max="720" data-model="pusher.max_age_hours" value="${esc(p.max_age_hours)}" class="num">
      <span class="muted">发布超过该时长的动态直接丢弃（0 = 不限），默认 24 小时</span></div>
    <div class="row"><label class="muted">每个请求间隔（秒）</label>
      <input type="number" min="0" max="60" step="0.5" data-model="pusher.request_gap_seconds" value="${esc(p.request_gap_seconds)}" class="num">
      <span class="muted"><b>每一次</b> B 站请求之间都要排队等待「基准 × 0.5~1.5 倍」，默认 2 秒左右（0 = 不限流）</span></div>
    <div class="row"><label class="muted">推送形态</label>
      <select data-model="pusher.mode" class="sel">
        <option value="auto"${p.mode === 'auto' ? ' selected' : ''}>文字 + 封面图</option>
        <option value="text"${p.mode === 'text' ? ' selected' : ''}>纯文字</option>
      </select>
      <span class="muted">封面图以 CDN 链接形式随消息发出，由接收端自行加载</span></div>
    <div class="row"><label class="muted">附带封面图</label>
      <label class="check"><input type="checkbox" data-model="pusher.push_images" ${p.push_images ? 'checked' : ''}> 动态有图时随文字一起发（一条消息，文字在上图在下）</label></div>
    <div class="warn"><b>⚠️ 推送目标说明</b>
      <p style="margin:6px 0 0">推送目标 = <b>平台实例 ID + 群 ID</b>（统一会话 origin：<code>平台ID:GroupMessage:群ID</code>）。
      平台实例 ID 见 WebUI「消息平台」页；群 ID 是该平台群消息事件里的 session_id（如 aiocqhttp 的 QQ 群号）。</p></div>
    <div class="row"><label class="muted">运行状态</label>
      <span class="muted">上次运行 ${fmt(p.last_run) || '—'} · 下次运行 ${fmt(p.next_run) || '—'}${pushCountdown() ? '（' + pushCountdown() + '）' : ''} · 上次检查 ${p.last_checked} 个订阅 / 推送 ${p.last_pushed} 条</span></div>
    ${p.last_error ? `<p class="err" style="margin-top:0">最近错误：${esc(p.last_error)}</p>` : ''}
    <div class="row" style="margin-bottom:0">
      <button data-act="pushCfgSave" ${p.saving ? 'disabled' : ''}>${p.saving ? '保存中…' : '保存设置'}</button>
      <button class="ghost" data-act="pushCfgLoad" ${p.loading ? 'disabled' : ''}>${p.loading ? '读取中…' : '重新读取'}</button>
      <button class="ghost" data-act="pushRun" ${p.running ? 'disabled' : ''}>${p.running ? '执行中…' : '立即执行一轮'}</button>
      <button class="ghost" data-act="pushRunReset" ${p.running ? 'disabled' : ''}>重置基线并重推</button>
      ${p.message ? `<span class="muted">${esc(p.message)}</span>` : ''}
    </div>`;
  if (p.details.length) {
    html += `<table style="margin-top:10px"><thead><tr><th>订阅</th><th>推送群</th><th style="width:70px">拉到</th><th style="width:70px">已推</th><th>说明</th></tr></thead><tbody>`;
    for (const d of p.details) {
      html += `<tr><td>${esc(d.uname)}</td><td>${esc(d.group)}</td><td>${d.items}</td><td>${d.pushed}</td><td>
        ${d.error ? `<span class="err">${esc(d.error)}</span>`
        : d.baselined ? '<span class="muted">首次运行，仅建立基线（未推送历史动态）</span>'
        : `<span class="muted">推送 ${d.pushed} 条${d.skipped ? `；跳过 ${d.skipped} 条${d.skipped_kind ? `（类型不匹配 ${d.skipped_kind}）` : ''}${d.skipped_keyword ? `（图文不含关键词 ${d.skipped_keyword}）` : ''}` : ''}${(!d.pushed && !d.skipped) ? '；无新动态（或已超过「过期不推送」阈值）' : ''}</span>`}
      </td></tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `<p class="muted" style="margin:8px 0 0">说明：<b>新订阅第一次运行只建立基线</b>，不会把历史动态全推一遍；已推过的动态按 id 去重。
    <b>「图文需包含」只筛「图文」类型的动态</b>（点订阅行「条件」按钮改），投稿 / 专栏 / 影视 / 相册 / 直播 / 文字不受影响。
    请求间隔按<b>每个请求</b>生效（页面手动刷新、翻页、定时推送都会排队，永不并发）。</p></div>`;
  return html;
}

/* ---------------- 直播订阅（frag/livesubs + 弹窗） ---------------- */

async function liveFindUser() {
  const f = S.livefinder;
  const uid = (f.uid || '').trim();
  if (!uid) { f.error = '请输入主播 UID'; render(); return; }
  f.loading = true; f.error = ''; render();
  const j = await GET('live/status', { uid });
  if (j.ok) { f.user = j; if (!j.mid) f.error = '没有找到该主播'; }
  else { f.user = null; f.error = j.message || '查找失败'; }
  f.loading = false; render();
}

async function loadLiveSubs() {
  S.livesubs.loading = true; S.livesubs.error = '';
  const j = await GET('live_subs');
  if (j.ok) S.livesubs.list = j.subs || [];
  else S.livesubs.error = j.message || '读取直播订阅列表失败';
  S.livesubs.loading = false;
  render();
}
async function liveSubToggle(s) {
  const j = await POST('live_subs/toggle', { id: s.id, enabled: !s.enabled });
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) loadLiveSubs();
}
async function liveSubDelete(s) {
  if (!confirmBox(`确定删除直播订阅「${s.uname || s.mid}」→ 群 ${s.group_name || s.group_id}？`)) return;
  const j = await POST('live_subs/delete', { id: s.id });
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) loadLiveSubs();
}

function livePusherApply(j) {
  for (const k of ['enabled', 'interval_minutes', 'jitter_minutes', 'request_gap_seconds', 'mode', 'push_images', 'last_run', 'next_run', 'last_checked', 'last_pushed', 'last_error', 'running']) {
    if (j[k] !== undefined && j[k] !== null) S.livepusher[k] = j[k];
  }
}
async function livePushCfgLoad() {
  S.livepusher.loading = true; S.livepusher.message = ''; render();
  const j = await GET('live_push/settings');
  if (j.ok) livePusherApply(j);
  else S.livepusher.message = j.message || '读取直播推送设置失败';
  S.livepusher.loading = false; render();
}
async function livePushCfgSave() {
  const p = S.livepusher;
  p.saving = true; p.message = ''; render();
  const j = await POST('live_push/settings', {
    enabled: !!p.enabled,
    interval_minutes: Number(p.interval_minutes) || 10,
    jitter_minutes: Number(p.jitter_minutes) || 0,
    request_gap_seconds: Number(p.request_gap_seconds) || 0,
    mode: p.mode || 'auto',
    push_images: !!p.push_images,
  });
  if (j.ok) { livePusherApply(j); notify(j.message || '直播推送设置已保存（立即生效）', 'ok'); }
  else notify(j.message || '保存直播推送设置失败', 'err');
  p.saving = false; render();
}
async function livePushRunNow(reset) {
  if (!S.livesubs.list.some(s => s.enabled)) { notify('没有启用的直播订阅，先把订阅启用后再试', 'err'); return; }
  const tip = reset
    ? '重置状态记录：清空各订阅的直播状态，本轮只重建基线（不会补推历史开播事件）。确定继续？'
    : '立即对所有启用的直播订阅检查一轮？首次运行的订阅只建立基线，不会推送。';
  if (!confirmBox(tip)) return;
  S.livepusher.running = true; S.livepusher.message = '正在检查…'; S.livepusher.details = []; render();
  const j = await POST('live_push/run', { reset: !!reset });
  const msg = j.message || (j.ok ? '执行完成' : '执行失败');
  const details = j.details || [];
  await livePushCfgLoad();
  S.livepusher.details = details; S.livepusher.message = msg;
  notify(msg, j.ok ? 'ok' : 'err');
  S.livepusher.running = false; render();
}

function renderLivesubs() {
  let html = '';
  // 指定 UID 订阅直播
  const f = S.livefinder;
  html += `<div class="card"><h2>指定 UID 订阅直播 <span class="hint">输入主播 UID 或 space 主页链接</span></h2>
    <div class="flex">
      <input type="text" data-model="livefinder.uid" value="${esc(f.uid)}" placeholder="主播 UID（纯数字，或粘贴 space.bilibili.com 主页链接）" style="flex:1;max-width:400px">
      <button data-act="liveFindUser" ${f.loading || !f.uid.trim() ? 'disabled' : ''}>${f.loading ? '查找中…' : '查找'}</button>
    </div>`;
  if (f.error) html += `<p class="err" style="margin:8px 0 0">${esc(f.error)}</p>`;
  if (f.user && f.user.mid) {
    html += `<div class="ucard" style="max-width:460px;margin-top:10px">
      <div class="flex" style="gap:10px">
        ${img(f.user.face, 'face face-44')}
        <div style="flex:1;min-width:0">
          <div class="name-row"><b>${esc(f.user.uname)}</b><span class="badge ${f.user.live ? 'online' : ''}">${esc(f.user.status_label)}</span></div>
          <div class="muted">UID ${esc(f.user.mid)}${f.user.room_id ? ` · 房间 ${f.user.room_id}` : ''}</div>
          <div class="muted">${esc(f.user.title || '（当前无直播标题）')}</div>
        </div>
      </div>
      <div class="foot">
        ${f.user.url ? `<a href="${esc(f.user.url)}" target="_blank" class="muted" style="font-size:12px">打开直播间</a>` : ''}
        <button data-act="livePushOpen" data-arg="${f.user.mid}">直播订阅</button>
      </div></div>`;
  }
  html += `</div>`;

  // 直播订阅列表
  const sb = S.livesubs;
  html += `<div class="flex" style="margin-bottom:12px">
    <button class="ghost" data-act="loadLiveSubs" ${sb.loading ? 'disabled' : ''}>${sb.loading ? '加载中…' : '刷新直播订阅列表'}</button>
    <span class="muted">点「直播订阅」把主播的开播 / 下播事件订阅到指定群</span></div>`;
  if (sb.error) html += `<p class="err">${esc(sb.error)}</p>`;
  if (!sb.list.length && !sb.loading && !sb.error) html += `<p class="muted">还没有直播订阅</p>`;
  if (sb.list.length) {
    html += `<table><thead><tr><th>主播</th><th>推送目标</th><th>事件</th><th>状态</th><th style="width:200px">操作</th></tr></thead><tbody>`;
    for (const s of sb.list) {
      html += `<tr>
        <td><div class="flex" style="gap:8px">${img(s.face, 'face face-30')}
          <div><b>${esc(s.uname || 'UID ' + s.mid)}</b>
          ${s.room_id ? `<div class="muted"><a href="${esc(s.room_url)}" target="_blank">房间 ${esc(s.room_id)}</a></div>` : ''}</div></div></td>
        <td><div>${esc(s.group_name || s.group_id)}</div><div class="muted">通过 ${esc(s.platform_name || s.platform_id)}</div><div class="muted">${esc(s.group_id)}</div></td>
        <td>${s.notify_live ? '<span class="badge" style="margin:0 4px 4px 0">开播</span>' : ''}${s.notify_offline ? '<span class="badge" style="margin:0 4px 4px 0">下播</span>' : ''}</td>
        <td>${s.enabled ? '<span style="color:var(--ok)">已启用</span>' : '<span style="color:var(--err)">已停用</span>'}</td>
        <td><button class="ghost" style="padding:4px 8px" data-act="liveNotifyOpen" data-arg="${esc(s.id)}">事件</button>
          <button class="ghost" style="padding:4px 8px" data-act="liveSubToggle" data-arg="${esc(s.id)}">${s.enabled ? '停用' : '启用'}</button>
          <button class="red" style="padding:4px 8px" data-act="liveSubDelete" data-arg="${esc(s.id)}">删除</button></td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  // 直播推送设置
  const p = S.livepusher;
  html += `<div class="card" style="margin-top:16px"><h2>直播推送设置 <span class="hint">定时检查订阅主播是否在直播，状态变化时推到指定群</span></h2>
    <div class="row"><label class="muted">启用定时检查</label>
      <label class="check"><input type="checkbox" data-model="livepusher.enabled" ${p.enabled ? 'checked' : ''}> ${p.enabled ? '已启用' : '已停用（不会自动检查）'}</label></div>
    <div class="row"><label class="muted">检查间隔（分钟）</label>
      <input type="number" min="5" max="1440" data-model="livepusher.interval_minutes" value="${esc(p.interval_minutes)}" class="num">
      <span class="muted">不得小于 5 分钟，默认 10 分钟</span></div>
    <div class="row"><label class="muted">随机浮动（± 分钟）</label>
      <input type="number" min="0" max="60" data-model="livepusher.jitter_minutes" value="${esc(p.jitter_minutes)}" class="num">
      <span class="muted">每次实际等待在基准上下随机浮动，避免固定节奏被风控</span></div>
    <div class="row"><label class="muted">请求间隔（秒）</label>
      <input type="number" min="0" max="60" step="0.5" data-model="livepusher.request_gap_seconds" value="${esc(p.request_gap_seconds)}" class="num">
      <span class="muted">每个 B 站请求之间的最小间隔（与动态推送共用，改任一处都生效）</span></div>
    <div class="row"><label class="muted">推送形态</label>
      <select data-model="livepusher.mode" class="sel">
        <option value="auto"${p.mode === 'auto' ? ' selected' : ''}>文字 + 封面图</option>
        <option value="text"${p.mode === 'text' ? ' selected' : ''}>纯文字</option>
      </select></div>
    <div class="row"><label class="muted">推送封面图</label>
      <label class="check"><input type="checkbox" data-model="livepusher.push_images" ${p.push_images ? 'checked' : ''}> 有封面时随文字一起发</label></div>
    <div class="row"><span class="muted">上次运行 ${fmt(p.last_run) || '—'} · 下次运行 ${fmt(p.next_run) || '—'} · 上次检查 ${p.last_checked} 个 / 推送 ${p.last_pushed} 条</span></div>
    ${p.last_error ? `<p class="err" style="margin-top:0">最近错误：${esc(p.last_error)}</p>` : ''}
    <div class="row" style="margin-bottom:0">
      <button data-act="livePushCfgSave" ${p.saving ? 'disabled' : ''}>${p.saving ? '保存中…' : '保存设置'}</button>
      <button class="ghost" data-act="livePushCfgLoad" ${p.loading ? 'disabled' : ''}>${p.loading ? '读取中…' : '重新读取'}</button>
      <button class="ghost" data-act="livePushRun" ${p.running ? 'disabled' : ''}>${p.running ? '执行中…' : '立即检查一轮'}</button>
      ${p.message ? `<span class="muted">${esc(p.message)}</span>` : ''}
    </div>`;
  if (p.details.length) {
    html += `<table style="margin-top:10px"><thead><tr><th>主播</th><th>推送群</th><th style="width:80px">当前</th><th style="width:80px">事件</th><th>说明</th></tr></thead><tbody>`;
    for (const d of p.details) {
      html += `<tr><td>${esc(d.uname)}</td><td>${esc(d.group)}</td>
        <td>${d.live ? '<span style="color:var(--err)">直播中</span>' : '<span class="muted">未开播</span>'}</td>
        <td>${d.event ? `<span class="badge">${esc(d.event)}</span>` : '<span class="muted">—</span>'}</td>
        <td>${d.error ? `<span class="err">${esc(d.error)}</span>` : d.baselined ? '<span class="muted">首次运行，仅建立基线</span>' : d.pushed ? `<span class="muted">已推送 ${esc(d.title || '')}</span>` : '<span class="muted">状态无变化</span>'}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `<p class="muted" style="margin:8px 0 0">说明：<b>只在状态变化时推送</b>（未开播→直播中 推「开播」，直播中→未开播 推「下播」），不会重复刷屏；
    <b>新订阅第一次检查只建立基线</b>，不会补推「当前正在直播」的历史事件。</p></div>`;
  return html;
}

/* ---------------- 弹窗 ---------------- */

async function pushModalOpen(u) {
  // u 可以是对象（关注列表卡片）或 mid（按 id 从 finder/列表里找）
  let user = u;
  if (typeof u === 'string' || typeof u === 'number') {
    user = (S.finder.user && String(S.finder.user.mid) === String(u)) ? S.finder.user
      : S.follow.list.find(x => String(x.mid) === String(u)) || { mid: u, uname: '', face: '' };
  }
  const P = S.push;
  P.mid = user.mid; P.uname = user.uname || ''; P.face = user.face || '';
  P.platform_id = ''; P.group_id = ''; P.group_name = '';
  P.types = ['archive']; P.keyword = '';
  P.error = ''; P.saving = false; P.show = true; P.loading = true;
  P.platforms = [];
  render();
  const j = await GET('targets');
  if (j.ok) {
    P.platforms = j.platforms || [];
    if (P.platforms.length === 1) P.platform_id = P.platforms[0].id;
  } else P.error = j.message || '拉取平台实例失败';
  P.loading = false;
  render();
}

async function pushModalSave() {
  const P = S.push;
  if (!P.types.length) { P.error = '至少选择一个消息类型'; render(); return; }
  if (!P.platform_id) { P.error = '请选择推送用的平台实例'; render(); return; }
  if (!String(P.group_id || '').trim()) { P.error = '请填写群 ID（该平台群消息事件里的 session_id）'; render(); return; }
  P.saving = true; P.error = ''; render();
  const plat = P.platforms.find(x => x.id === P.platform_id) || {};
  const j = await POST('subs/save', {
    mid: P.mid, uname: P.uname, face: P.face,
    platform_id: P.platform_id, platform_name: plat.name || plat.type || P.platform_id,
    group_id: String(P.group_id).trim(),
    group_name: P.group_name || '',
    types: P.types.slice(),
    keyword: (P.keyword || '').trim(),
  });
  if (j.ok) { notify(j.message, 'ok'); P.show = false; loadSubs(); }
  else P.error = j.message || '保存订阅失败';
  P.saving = false; render();
}

function typesModalOpen(s) {
  const T = S.types;
  T.id = s.id;
  T.uname = s.uname || 'UID ' + s.mid;
  T.list = (s.types && s.types.length) ? s.types.slice() : ['archive'];
  T.keyword = s.keyword || '';
  T.error = ''; T.saving = false; T.show = true;
  render();
}
async function typesModalSave() {
  const T = S.types;
  if (!T.list.length) { T.error = '至少选择一个消息类型'; render(); return; }
  T.saving = true; T.error = ''; render();
  const j = await POST('subs/types', { id: T.id, types: T.list.slice(), keyword: (T.keyword || '').trim() });
  if (j.ok) { notify(j.message, 'ok'); T.show = false; loadSubs(); }
  else T.error = j.message || '保存失败';
  T.saving = false; render();
}

async function livePushModalOpen(u) {
  let user = u;
  if (typeof u === 'string' || typeof u === 'number') {
    user = (S.livefinder.user && String(S.livefinder.user.mid) === String(u)) ? S.livefinder.user
      : S.lives.list.find(x => String(x.mid) === String(u)) || { mid: u, uname: '', face: '' };
  }
  const P = S.livepush;
  P.mid = user.mid; P.uname = user.uname || ''; P.face = user.face || ''; P.room_id = user.room_id || 0;
  P.platform_id = ''; P.group_id = ''; P.group_name = '';
  P.notify_live = true; P.notify_offline = true;
  P.error = ''; P.saving = false; P.show = true; P.loading = true;
  P.platforms = [];
  render();
  const j = await GET('targets');
  if (j.ok) {
    P.platforms = j.platforms || [];
    if (P.platforms.length === 1) P.platform_id = P.platforms[0].id;
  } else P.error = j.message || '拉取平台实例失败';
  P.loading = false;
  render();
}

async function livePushModalSave() {
  const P = S.livepush;
  if (!P.notify_live && !P.notify_offline) { P.error = '至少选择一个事件'; render(); return; }
  if (!P.platform_id) { P.error = '请选择推送用的平台实例'; render(); return; }
  if (!String(P.group_id || '').trim()) { P.error = '请填写群 ID（该平台群消息事件里的 session_id）'; render(); return; }
  P.saving = true; P.error = ''; render();
  const plat = P.platforms.find(x => x.id === P.platform_id) || {};
  const j = await POST('live_subs/save', {
    mid: P.mid, uname: P.uname, face: P.face, room_id: P.room_id,
    platform_id: P.platform_id, platform_name: plat.name || plat.type || P.platform_id,
    group_id: String(P.group_id).trim(),
    group_name: P.group_name || '',
    notify_live: !!P.notify_live, notify_offline: !!P.notify_offline,
  });
  if (j.ok) { notify(j.message, 'ok'); P.show = false; loadLiveSubs(); }
  else P.error = j.message || '保存直播订阅失败';
  P.saving = false; render();
}

function liveNotifyOpen(s) {
  const N = S.livenotify;
  N.id = s.id;
  N.uname = s.uname || 'UID ' + s.mid;
  N.notify_live = !!s.notify_live;
  N.notify_offline = !!s.notify_offline;
  N.error = ''; N.saving = false; N.show = true;
  render();
}
async function liveNotifySave() {
  const N = S.livenotify;
  N.saving = true; N.error = ''; render();
  const j = await POST('live_subs/notify', {
    id: N.id, notify_live: !!N.notify_live, notify_offline: !!N.notify_offline,
  });
  if (j.ok) { notify(j.message, 'ok'); N.show = false; loadLiveSubs(); }
  else N.error = j.message || '保存失败';
  N.saving = false; render();
}

function findSubById(id) { return S.subs.list.find(s => s.id === id); }
function findLiveSubById(id) { return S.livesubs.list.find(s => s.id === id); }

function renderModal() {
  const box = document.getElementById('modal');
  const kindsChecks = (list) => KINDS.map(k =>
    `<label class="check"><input type="checkbox" data-check="${list}" value="${k.v}" ${S[list === 'push' ? 'push' : 'types'].list.includes(k.v) ? 'checked' : ''}> ${k.t}</label>`).join('');
  const platformOptions = (list, sel) => {
    const P = S[list];
    return P.platforms.map(p => `<option value="${esc(p.id)}"${p.id === sel ? ' selected' : ''}>${esc(p.name || p.id)}（${esc(p.type || p.id)}）</option>`).join('');
  };

  let html = '';
  if (S.push.show) {
    const P = S.push;
    html = `<div class="modal" data-modal="push"><div class="modal-box">
      <h2>动态推送 <span class="hint">${esc(P.uname || 'UID ' + P.mid)}</span></h2>
      <div class="row"><label>平台实例</label>
        <select data-modal-model="push.platform_id" class="sel" ${P.loading ? 'disabled' : ''}>
          <option value="">— 请选择 —</option>${platformOptions('push', P.platform_id)}
        </select>
        <span class="muted">AstrBot「消息平台」里配置的实例 ID</span></div>
      <div class="row"><label>群 ID</label>
        <input type="text" data-modal-model="push.group_id" value="${esc(P.group_id)}" placeholder="该平台群消息事件里的 session_id（如 QQ 群号）" class="kw-input">
        <input type="text" data-modal-model="push.group_name" value="${esc(P.group_name)}" placeholder="群备注名（可选）" style="max-width:140px"></div>
      <p class="muted" style="margin-top:0">群 ID 填写方式：在目标群触发一次消息，在 AstrBot 日志/会话列表里能看到该群的 session_id（aiocqhttp 平台就是 QQ 群号）。</p>
      <div class="row"><label>订阅消息类型</label><div class="flex">${kindsChecks('push')}</div></div>
      ${!P.types.length ? '<p class="err" style="margin-top:0">至少选择一个消息类型（默认：投稿）</p>' : ''}
      <div class="row"><label>图文需包含</label>
        <input type="text" data-modal-model="push.keyword" value="${esc(P.keyword)}" placeholder="留空 = 图文不过滤（默认，什么都推）" class="kw-input"></div>
      <p class="muted" style="margin-top:0"><b>只筛「图文」类型的动态</b>：正文不含这些字的图文不推，其他类型照常推送。多个关键词用 <code>,</code> 分隔，命中任意一个即推送。保存后还能在订阅列表里点「条件」再改。</p>
      ${P.loading ? '<p class="muted">正在拉取平台实例列表…</p>' : ''}
      ${P.error ? `<p class="err">${esc(P.error)}</p>` : '<p class="muted">同一个 UP 主可以订阅到多个群；重复订阅同一组合会被自动去重。</p>'}
      <div class="actions" style="margin-top:14px">
        <button class="ghost" data-act="modalClose">取消</button>
        <button data-act="pushModalSave" ${P.saving ? 'disabled' : ''}>${P.saving ? '保存中…' : '保存订阅'}</button>
      </div></div></div>`;
  } else if (S.types.show) {
    const T = S.types;
    html = `<div class="modal" data-modal="types"><div class="modal-box" style="max-width:480px">
      <h2>订阅推送条件 <span class="hint">${esc(T.uname)}</span></h2>
      <div class="row"><label>要推送的类型</label><div class="flex">${kindsChecks('types')}</div></div>
      <p class="muted" style="margin-top:0">只推勾选类型的动态；识别不出类型的动态仍会推送（避免漏推）。</p>
      ${!T.list.length ? '<p class="err" style="margin-top:0">至少选择一个消息类型</p>' : ''}
      <div class="row"><label>图文需包含</label>
        <input type="text" data-modal-model="types.keyword" value="${esc(T.keyword)}" placeholder="留空 = 图文不过滤（默认，什么都推）" class="kw-input"></div>
      <p class="muted" style="margin-top:0"><b>只筛「图文」类型的动态</b>：正文不含这些字的<b>图文不推</b>；上面勾的投稿 / 专栏 / 影视 / 相册 / 直播 / 文字<b>一概不受影响</b>。
        匹配范围 = 标题 + 正文（忽略大小写）；多个关键词用 <code>,</code> 分隔，命中任意一个即推；被跳过的图文不补推。<b>留空 = 不过滤</b>。</p>
      ${T.error ? `<p class="err">${esc(T.error)}</p>` : ''}
      <div class="actions" style="margin-top:14px">
        <button class="ghost" data-act="modalClose">取消</button>
        <button data-act="typesModalSave" ${T.saving ? 'disabled' : ''}>${T.saving ? '保存中…' : '保存'}</button>
      </div></div></div>`;
  } else if (S.livepush.show) {
    const P = S.livepush;
    html = `<div class="modal" data-modal="livepush"><div class="modal-box">
      <h2>直播订阅 <span class="hint">${esc(P.uname || 'UID ' + P.mid)}</span></h2>
      <div class="row"><label>平台实例</label>
        <select data-modal-model="livepush.platform_id" class="sel" ${P.loading ? 'disabled' : ''}>
          <option value="">— 请选择 —</option>${platformOptions('livepush', P.platform_id)}
        </select></div>
      <div class="row"><label>群 ID</label>
        <input type="text" data-modal-model="livepush.group_id" value="${esc(P.group_id)}" placeholder="该平台群消息事件里的 session_id（如 QQ 群号）" class="kw-input">
        <input type="text" data-modal-model="livepush.group_name" value="${esc(P.group_name)}" placeholder="群备注名（可选）" style="max-width:140px"></div>
      <div class="row"><label>推送事件</label>
        <label class="check"><input type="checkbox" data-modal-check="livepush.notify_live" ${P.notify_live ? 'checked' : ''}> 开播</label>
        <label class="check"><input type="checkbox" data-modal-check="livepush.notify_offline" ${P.notify_offline ? 'checked' : ''}> 下播</label></div>
      ${(!P.notify_live && !P.notify_offline) ? '<p class="err" style="margin-top:0">至少选择一个事件（默认：开播 + 下播）</p>' : ''}
      ${P.loading ? '<p class="muted">正在拉取平台实例列表…</p>' : ''}
      ${P.error ? `<p class="err">${esc(P.error)}</p>` : '<p class="muted">同一个主播可以订阅到多个群；重复订阅同一组合会被自动去重。</p>'}
      <div class="actions" style="margin-top:14px">
        <button class="ghost" data-act="modalClose">取消</button>
        <button data-act="livePushModalSave" ${P.saving ? 'disabled' : ''}>${P.saving ? '保存中…' : '保存订阅'}</button>
      </div></div></div>`;
  } else if (S.livenotify.show) {
    const N = S.livenotify;
    html = `<div class="modal" data-modal="livenotify"><div class="modal-box" style="max-width:460px">
      <h2>推送事件 <span class="hint">${esc(N.uname)}</span></h2>
      <div class="row"><label>要推送的事件</label>
        <label class="check"><input type="checkbox" data-modal-check="livenotify.notify_live" ${N.notify_live ? 'checked' : ''}> 开播</label>
        <label class="check"><input type="checkbox" data-modal-check="livenotify.notify_offline" ${N.notify_offline ? 'checked' : ''}> 下播</label></div>
      <p class="muted" style="margin-top:0">只在状态变化时推送；两个事件都关掉会自动恢复为「开播 + 下播」。</p>
      ${N.error ? `<p class="err">${esc(N.error)}</p>` : ''}
      <div class="actions" style="margin-top:14px">
        <button class="ghost" data-act="modalClose">取消</button>
        <button data-act="liveNotifySave" ${N.saving ? 'disabled' : ''}>${N.saving ? '保存中…' : '保存'}</button>
      </div></div></div>`;
  }
  box.innerHTML = html;
  // 点击遮罩关闭
  box.querySelectorAll('.modal').forEach(m => {
    m.addEventListener('click', (e) => { if (e.target === m) { setModalHidden(); render(); } });
  });
}

function setModalHidden() {
  S.push.show = false; S.types.show = false; S.livepush.show = false; S.livenotify.show = false;
}

/* ---------------- 渲染 & 事件 ---------------- */

const TABS = [
  ['account', '账号'], ['dm', '私信'], ['follow', '关注'], ['feed', '动态'],
  ['lives', '直播'], ['subs', '动态订阅'], ['livesubs', '直播订阅'],
];

function render() {
  // 页签
  const tabs = document.getElementById('tabs');
  const unread = (S.state.unread && S.state.unread.total) || 0;
  const counts = { dm: unread, lives: S.lives.list.length, subs: S.subs.list.length, livesubs: S.livesubs.list.length };
  tabs.innerHTML = TABS.map(([id, label]) => {
    const c = counts[id] ? ` <span class="badge online" style="margin-left:4px">${counts[id]}</span>` : '';
    return `<span class="tab ${S.tab === id ? 'active' : ''}" data-act="switchTab" data-arg="${id}">${label}${c}</span>`;
  }).join('');
  // 登录徽标
  const badge = document.getElementById('login-badge');
  badge.textContent = S.state.logged ? `已登录：${S.state.user.uname || 'UID ' + S.state.user.mid}` : '未登录';
  badge.className = 'badge ' + (S.state.logged ? 'online' : 'offline');
  // 内容
  const view = document.getElementById('view');
  const map = { account: renderAccount, dm: renderDm, follow: renderFollow, feed: renderFeed, lives: renderLives, subs: renderSubs, livesubs: renderLivesubs };
  view.innerHTML = (map[S.tab] || renderAccount)();
  renderModal();
}

/* 进入子页签时的拉取（对应原 biliSwitch 的 onEnter） */
function switchTab(id) {
  S.tab = id;
  if (id === 'dm' && S.state.logged && !S.sessions.list.length) loadSessions();
  if (id === 'subs') { loadSubs(); pushCfgLoad(); }
  if (id === 'livesubs') { loadLiveSubs(); livePushCfgLoad(); }
  if (id === 'follow' && !S.state.logged) { followClearCache(); S.follow.list = []; S.follow.total = 0; }
  else if (id === 'follow' && !S.follow.list.length && !S.follow.loading) followLoadCache();
  render();
}

/* 按「a.b.c」路径读状态 */
function stateGet(path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), S);
}
function stateSet(path, value) {
  const keys = path.split('.');
  let o = S;
  for (let i = 0; i < keys.length - 1; i++) o = o[keys[i]];
  o[keys[keys.length - 1]] = value;
}

/* 动作分发（事件委托） */
const ACTIONS = {
  switchTab: (id) => switchTab(id),
  refreshState, loginStart, logout, refreshToken,
  loadSessions: () => loadSessions(),
  openSession: (talkerId) => { const s = S.sessions.list.find(x => String(x.talker_id) === String(talkerId)); if (s) openSession(s); },
  followSearch, followTags: () => followTags(),
  followTag: (id) => followTag(Number(id)),
  followings1: () => loadFollowings(1),
  followPrev: () => loadFollowings(S.follow.pn - 1),
  followNext: () => loadFollowings(S.follow.pn + 1),
  followDyn: (mid) => { const u = S.follow.list.find(x => String(x.mid) === String(mid)) || (S.finder.user && String(S.finder.user.mid) === String(mid) ? S.finder.user : null); followDyn(u || { mid, uname: '' }); },
  dynLoad: () => loadDynamics(''),
  dynMore: () => loadDynamics(S.dyn.offset),
  dynBack,
  openImg: (u) => openImg(u),
  livesLoad: () => loadLives(false),
  livesMore: () => loadLives(true),
  findUser, loadSubs, subToggle: (id) => { const s = findSubById(id); if (s) subToggle(s); },
  subDelete: (id) => { const s = findSubById(id); if (s) subDelete(s); },
  typesOpen: (id) => { const s = findSubById(id); if (s) typesModalOpen(s); },
  typesModalSave,
  pushOpen: (mid) => pushModalOpen(mid),
  pushModalSave,
  pushCfgSave, pushCfgLoad, pushRun: () => pushRunNow(false), pushRunReset: () => pushRunNow(true),
  liveFindUser, loadLiveSubs,
  liveSubToggle: (id) => { const s = findLiveSubById(id); if (s) liveSubToggle(s); },
  liveSubDelete: (id) => { const s = findLiveSubById(id); if (s) liveSubDelete(s); },
  liveNotifyOpen: (id) => { const s = findLiveSubById(id); if (s) liveNotifyOpen(s); },
  liveNotifySave,
  livePushOpen: (mid) => livePushModalOpen(mid),
  livePushModalSave,
  livePushCfgSave, livePushCfgLoad, livePushRun: () => livePushRunNow(false),
  modalClose: () => { setModalHidden(); render(); },
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const fn = ACTIONS[el.dataset.act];
  if (fn) { e.preventDefault(); fn(el.dataset.arg); }
});

/* 输入框双向绑定（input 事件委托） */
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el.dataset.model) {
    let v = el.value;
    if (el.type === 'checkbox') v = el.checked;
    stateSet(el.dataset.model, v);
    return;
  }
  if (el.dataset.modalModel) {
    let v = el.value;
    if (el.type === 'checkbox') v = el.checked;
    stateSet(el.dataset.modalModel, v);
    return;
  }
  if (el.dataset.modalCheck) {
    stateSet(el.dataset.modalCheck, el.checked);
    return;
  }
});
document.addEventListener('change', (e) => {
  const el = e.target;
  if (el.dataset.check) {
    // 订阅类型多选（data-check 指向 push / types 的 list）
    const listName = el.dataset.check;
    const v = el.value;
    const arr = stateGet(listName + '.list');
    const idx = arr.indexOf(v);
    if (el.checked && idx < 0) arr.push(v);
    if (!el.checked && idx >= 0) arr.splice(idx, 1);
    render();
    return;
  }
  if (el.dataset.modalCheck) {
    stateSet(el.dataset.modalCheck, el.checked);
    render();
    return;
  }
  if (el.tagName === 'SELECT' && el.dataset.model) {
    stateSet(el.dataset.model, el.value);
    return;
  }
  if (el.tagName === 'SELECT' && el.dataset.modalModel) {
    stateSet(el.dataset.modalModel, el.value);
    return;
  }
  if (el.type === 'checkbox' && (el.dataset.model || el.dataset.modalModel)) {
    // 勾选框改变后重渲染（刷新「已启用/已停用」等文案）
    if (el.dataset.model) stateSet(el.dataset.model, el.checked);
    else stateSet(el.dataset.modalModel, el.checked);
    render();
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const el = e.target;
  if (el.tagName !== 'INPUT') return;
  // 回车触发所在行的主要动作
  const row = el.closest('.row, .flex, .card');
  if (!row) return;
  const act = el.dataset.model === 'follow.kw' ? 'followSearch'
    : el.dataset.model === 'finder.uid' ? 'findUser'
    : el.dataset.model === 'livefinder.uid' ? 'liveFindUser' : null;
  if (act && ACTIONS[act]) ACTIONS[act]();
});

/* ---------------- 启动 ---------------- */

const ctx = await B.ready();
document.title = (ctx.pageTitle || 'B 站面板');
render();
await refreshState();
