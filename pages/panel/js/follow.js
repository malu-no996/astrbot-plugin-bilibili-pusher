/* 子页签：关注（原 frag/follow）—— 关注列表 / 分组 / 本地搜索
 * 列表/分组的缓存已挪到后端（data/page_cache.json）：进页签自动读后端缓存，
 * 点「刷新列表/刷新分组」才带 force=1 重新请求 B 站。前端不用 localStorage
 * （AstrBot Page 在受限 iframe 里 localStorage 可能被浏览器禁掉）。 */

function followClearCache() {
  S.follow.fromCache = false;
}

async function followTags(force) {
  if (!S.state.logged) { S.follow.error = '读取关注分组需要先登录'; render(); return; }
  const j = await GET('follow_tags', force ? { force: 1 } : {});
  if (j.ok) {
    S.follow.tags = j.tags || [];
    if (!S.follow.tags.some(t => t.tagid === S.follow.tagid)) S.follow.tagid = -1;
  } else if (force) S.follow.error = j.message || '读取关注分组失败';
  render();
}
function followTag(tagid) {
  if (S.follow.tagid === tagid && !S.follow.searching) return;
  S.follow.tagid = tagid; S.follow.pn = 1; S.follow.kw = ''; S.follow.searching = false;
  loadFollowings(1);
}
function followSearch() {
  // 搜索 = 直接在缓存里的关注列表上过滤，不发任何请求
  const kw = (S.follow.kw || '').trim();
  if (!kw) { S.follow.searching = false; S.follow.error = ''; render(); return; }
  if (!S.follow.list.length) { S.follow.error = '先点「刷新列表」拉一次关注列表（有缓存后搜索就是本地过滤，不请求 B 站）'; render(); return; }
  S.follow.searching = true; S.follow.error = '';
  render();
}
async function loadFollowings(pn, force) {
  if (!S.state.logged) { S.follow.error = '查看关注列表需要先登录'; render(); return; }
  S.follow.loading = true; S.follow.error = ''; render();
  const params = { pn: pn || 1, ps: 50 };
  if (S.follow.tagid !== -1) params.tagid = S.follow.tagid;
  if (force) params.force = 1;
  const j = await GET('followings', params);
  if (j.ok) {
    S.follow.list = j.items || []; S.follow.total = j.total || 0; S.follow.pn = j.pn || 1;
    S.follow.fromCache = !!j.cached;
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
  </div>
  <p class="muted" style="margin-top:2px">搜索 = 直接在缓存列表里按昵称过滤，<b>不请求 B 站</b>；列表以「刷新列表」拉到的为准。</p>`;
  html += `<div class="flex" style="margin-bottom:8px">
    ${f.total ? `<span class="muted">共 ${f.total} 个 · 第 ${f.pn} 页</span>` : (S.state.logged ? '' : '<span class="muted">需先在「账号」页签扫码登录</span>')}
    ${f.searching ? `<span class="muted">（搜索「${esc(f.kw.trim())}」：当前列表中匹配 ${(f.list || []).filter(u => (u.uname || '').toLowerCase().includes(f.kw.trim().toLowerCase())).length} 个）</span>` : ''}
    ${f.fromCache ? '<span class="badge" style="background:var(--warn-bg);color:var(--warn-text)">缓存数据 · 点「刷新列表」更新</span>' : ''}
  </div>`;
  if (f.tags.length) {
    html += `<div class="flex" style="margin-bottom:12px">`;
    for (const t of f.tags) {
      html += `<span class="tab ${f.tagid === t.tagid ? 'active' : ''}" data-act="followTag" data-arg="${t.tagid}" style="padding:5px 12px;border:1px solid var(--line);border-radius:8px">${esc(t.name)} ${t.count}</span>`;
    }
    html += `</div>`;
  }
  if (f.error) html += `<p class="err">${esc(f.error)}</p>`;
  if (!f.searching && !f.list.length && !f.loading && !f.error && S.state.logged) {
    html += `<p class="muted">尚未加载关注列表。点上方 <b>「刷新列表」</b> 拉取，拉过一次就会缓存在后端，之后进页面秒开、搜索也在这份缓存上过滤。</p>`;
  }
  const items = f.searching
    ? f.list.filter(u => (u.uname || '').toLowerCase().includes(f.kw.trim().toLowerCase()))
    : f.list;
  if (f.searching && !items.length && !f.error) {
    html += `<p class="muted">当前列表里没有昵称包含「${esc(f.kw.trim())}」的关注（只搜当前页缓存，可翻页后再搜）。</p>`;
  }
  if (items.length) {
    html += `<div class="grid grid-follow">`;
    for (const u of items) {
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
