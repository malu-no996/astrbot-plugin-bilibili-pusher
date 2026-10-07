/* 子页签：直播（原 frag/lives）—— 正在直播的关注列表 */

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
