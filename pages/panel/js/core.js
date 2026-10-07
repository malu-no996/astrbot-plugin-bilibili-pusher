/* B 站面板 - 公共部分（bridge / 工具 / 全局状态）
 * 子页签各自一个文件：js/account.js、js/dm.js、js/follow.js、js/feed.js、
 * js/lives.js、js/subs.js、js/livesubs.js、js/modals.js、js/cmds.js、js/data.js，
 * 入口与事件分发在 js/main.js。经典脚本按序加载，顶层 const/函数全局可见。
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

/* ---------------- 全局状态（对应原 frag 的 init） ---------------- */

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
  // 命令配置 / 数据管理（页签）
  cmds: { config: null, static: [], loading: false },
  data: { overview: null, records: [], loading: false },
};

/* 按「a.b.c」路径读/写状态（事件委托的绑定用） */
function stateGet(path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), S);
}
function stateSet(path, value) {
  const keys = path.split('.');
  let o = S;
  for (let i = 0; i < keys.length - 1; i++) o = o[keys[i]];
  o[keys[keys.length - 1]] = value;
}

/* ---------------- 通用展示辅助（命令 / 数据页共用） ---------------- */

const PLATFORM_LABELS = { aiocqhttp: 'OneBot（aiocqhttp）', qq_official: 'QQ 官方机器人' };
const platformLabel = (p) => PLATFORM_LABELS[p] || p || '-';
function fmtSize(n) {
  if (n == null) return '-';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(2) + ' MB';
}
