/* 子页签：直播订阅（原 frag/livesubs）—— 订阅列表 / 定时检查设置 */

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
