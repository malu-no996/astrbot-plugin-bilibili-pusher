/* B 站面板 - 入口：页签 / 渲染 / 事件分发 / 启动
 * 各子页签逻辑见同目录：core.js（公共）、account.js、dm.js、follow.js、feed.js、
 * lives.js、subs.js、livesubs.js、modals.js（弹窗）、cmds.js、data.js。
 * 经典脚本按 index.html 里的顺序加载，顶层 const/函数全局共享。
 */

const TABS = [
  ['account', '账号'], ['dm', '私信'], ['follow', '关注'], ['feed', '动态'],
  ['lives', '直播'], ['subs', '动态订阅'], ['livesubs', '直播订阅'],
  ['cmds', '命令'], ['data', '数据'],
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
  const map = { account: renderAccount, dm: renderDm, follow: renderFollow, feed: renderFeed, lives: renderLives, subs: renderSubs, livesubs: renderLivesubs, cmds: renderCmds, data: renderData };
  view.innerHTML = (map[S.tab] || renderAccount)();
  renderModal();
}

/* 进入子页签时的拉取（对应原 biliSwitch 的 onEnter） */
function switchTab(id) {
  S.tab = id;
  if (id === 'dm' && S.state.logged && !S.sessions.list.length) loadSessions();   // 先读后端缓存
  if (id === 'subs') { loadSubs(); pushCfgLoad(); }
  if (id === 'livesubs') { loadLiveSubs(); livePushCfgLoad(); }
  if (id === 'feed' && S.dyn.source === 'follow' && S.state.logged && !S.dyn.list.length && !S.dyn.loading && !S.dyn.error) loadDynamics('');   // 有缓存秒显，没缓存拉一次
  if (id === 'follow' && !S.state.logged) { followClearCache(); S.follow.list = []; S.follow.total = 0; }
  else if (id === 'follow' && !S.follow.list.length && !S.follow.loading) loadFollowings();   // 后端缓存
  if (id === 'cmds' && !S.cmds.config && !S.cmds.loading) loadCmds();
  if (id === 'data' && !S.data.overview && !S.data.loading) loadData();
  render();
}

/* 动作分发（事件委托） */
const ACTIONS = {
  switchTab: (id) => switchTab(id),
  refreshState, loginStart, logout, refreshToken,
  loadSessions: () => loadSessions(true),   // 「刷新会话」按钮 = 强制拉新
  openSession: (talkerId) => { const s = S.sessions.list.find(x => String(x.talker_id) === String(talkerId)); if (s) openSession(s); },
  followSearch, followTags: () => followTags(true),
  followTag: (id) => followTag(Number(id)),
  followings1: () => loadFollowings(1, true),   // 「刷新列表」按钮 = 强制拉新
  followPrev: () => loadFollowings(S.follow.pn - 1),
  followNext: () => loadFollowings(S.follow.pn + 1),
  followDyn: (mid) => { const u = S.follow.list.find(x => String(x.mid) === String(mid)) || (S.finder.user && String(S.finder.user.mid) === String(mid) ? S.finder.user : null); followDyn(u || { mid, uname: '' }); },
  dynLoad: () => loadDynamics('', true),   // 「拉取动态」按钮 = 强制拉新
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
  cmdsSave: () => saveCmds(),
  cmdsReload: () => loadCmds(),
  dataReload: () => loadData(),
  dataDel: (id) => dataDelRec(id),
  dataClearRecs,
  dataClearPage: () => dataClearCache('page'),
  dataClearImages: () => dataClearCache('images'),
  bindsDel: (id) => bindsDel(id),
  bindsClear,
  bindsEdit: (id) => bindsEdit(id),
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
    // 订阅类型多选（data-check = 完整状态路径，如 push.types / types.list）
    const arr = stateGet(el.dataset.check);
    if (!Array.isArray(arr)) return;
    const v = el.value;
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
    // 弹窗的平台实例下拉（data-modal-pick = 状态名）：选中后自动回填该处理者绑定的群 ID
    if (el.dataset.modalPick && typeof pushPlatformPicked === 'function') pushPlatformPicked(el.dataset.modalPick);
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

(async () => {
  const ctx = await B.ready();
  document.title = (ctx.pageTitle || 'B 站面板');
  render();
  await refreshState();
})();
