/* 子页签：动态（原 frag/feed）—— 关注动态流 / 指定 UP 主空间动态 */

function dynReset() { S.dyn.list = []; S.dyn.offset = ''; S.dyn.error = ''; }

async function loadDynamics(offset, force) {
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
  if (force) params.force = 1;
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
  loadDynamics('', true);   // 手动点进来的：直接拉最新的
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
