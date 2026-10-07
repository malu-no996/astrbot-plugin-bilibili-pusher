/* 子页签：动态订阅（原 frag/subs）—— 订阅列表 / 自动推送设置 */

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
