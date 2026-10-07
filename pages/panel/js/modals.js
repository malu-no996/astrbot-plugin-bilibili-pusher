/* 弹窗（动态推送 / 推送条件 / 直播订阅 / 推送事件）
 * 供动态订阅、直播订阅、关注、直播各页签调用。 */

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
    `<label class="check"><input type="checkbox" data-check="${list === 'push' ? 'push.types' : 'types.list'}" value="${k.v}" ${(list === 'push' ? S.push.types : S.types.list).includes(k.v) ? 'checked' : ''}> ${k.t}</label>`).join('');
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
