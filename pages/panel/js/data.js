/* 子页签：数据（概览 / 缓存清理 / 命令订阅记录 / 数据文件清单） */

async function loadData() {
  const D = S.data;
  D.loading = true; render();
  const [o, r, b] = await Promise.all([GET('data/overview'), GET('cmd_subs'), GET('binds')]);
  if (o.ok) D.overview = o; else notify(o.message || '读取数据概览失败', 'err');
  if (r.ok) D.records = r.records || []; else notify(r.message || '读取命令订阅记录失败', 'err');
  if (b.ok) D.binds = b.binds || []; else notify(b.message || '读取绑定群失败', 'err');
  D.loading = false;
  render();
}

async function dataDelRec(id) {
  if (!confirmBox('确定删除这条命令订阅记录？删除后该群不再收到这条命令订阅的推送。')) return;
  const j = await POST('cmd_subs/delete', { id });
  notify(j.message || (j.ok ? '已删除' : '删除失败'), j.ok ? 'ok' : 'err');
  await loadData();
}

async function dataClearRecs() {
  const D = S.data;
  if (!D.records.length) return;
  if (!confirmBox(`确定清空全部 ${D.records.length} 条命令订阅记录？这些群将不再收到命令订阅的推送。`)) return;
  const j = await POST('cmd_subs/clear', {});
  notify(j.message || '已清空', 'ok');
  await loadData();
}

async function dataClearCache(kind) {
  const j = await POST('data/cache/clear', kind === 'page' ? { page: true } : { images: true });
  notify(j.message || '已清空', j.ok ? 'ok' : 'err');
  await loadData();
}

async function bindsDel(id) {
  if (!confirmBox('确定删除这条绑定记录？')) return;
  const j = await POST('binds/delete', { id });
  notify(j.message || (j.ok ? '已删除' : '删除失败'), j.ok ? 'ok' : 'err');
  await loadData();
}

async function bindsClear() {
  const D = S.data;
  if (!D.binds.length) return;
  if (!confirmBox(`确定清空全部 ${D.binds.length} 条绑定记录？`)) return;
  const j = await POST('binds/clear', {});
  notify(j.message || '已清空', j.ok ? 'ok' : 'err');
  await loadData();
}

/* 编辑绑定记录：群名 / 处理者名字（QQ 官方平台拿不到群名和机器人名，在这里自定义） */
async function bindsEdit(id) {
  const b = S.data.binds.find(x => String(x.id) === String(id));
  if (!b) return;
  const gname = promptBox('群名（群的名称，可自定义）', b.group_name || '');
  if (gname === null) return;
  const hname = promptBox('处理者名字（处理这条绑定的机器人名字，可自定义）', b.handler_name || '');
  if (hname === null) return;
  const j = await POST('binds/update', { id: b.id, group_name: gname.trim(), handler_name: hname.trim() });
  notify(j.message || (j.ok ? '已保存' : '保存失败'), j.ok ? 'ok' : 'err');
  if (j.ok) await loadData();
}

function renderData() {
  const D = S.data;
  const o = D.overview || {};
  const c = o.counts || {};
  const cache = o.cache || {};
  const imgs = cache.images || {};
  let html = `<div class="flex" style="margin-bottom:12px">
    <span class="badge online">面板订阅 ${c.subs ?? '-'}</span>
    <span class="badge online">直播订阅 ${c.live_subs ?? '-'}</span>
    <span class="badge online">命令订阅 ${c.cmd_subs ?? '-'}</span>
    <span class="badge online">绑定群 ${c.binds ?? '-'}</span>
    <span class="badge online">推送记录 ${c.push_state ?? '-'}</span>
    <span style="flex:1"></span>
    <button class="ghost" data-act="dataReload" ${D.loading ? 'disabled' : ''}>刷新</button>
  </div>`;
  html += `<div class="card"><h2>缓存</h2>
    <div class="row"><label>页面缓存</label><span>${cache.page_cache ?? 0} 条</span>
      <button class="ghost" data-act="dataClearPage" ${D.loading ? 'disabled' : ''}>清空</button></div>
    <div class="row"><label>图片缓存</label><span>${imgs.count ?? 0} 个（${fmtSize(imgs.bytes || 0)}）</span>
      <button class="ghost" data-act="dataClearImages" ${D.loading ? 'disabled' : ''}>清空</button></div>
  </div>`;
  html += `<div class="card"><h2>命令订阅记录${D.records.length ? `（共 ${D.records.length} 条）` : ''}</h2>
    <p class="muted" style="margin-top:0">群内发「<b>订阅B站推送 UP主UID</b>」产生的订阅（{qq_id, group_id, platform, bilibili_id}），这些群也参与定时推送（全类型动态）。面板手动加的订阅在「动态订阅」页管理。</p>
    <div class="actions" style="margin:0 0 10px"><button class="red" data-act="dataClearRecs" ${D.loading || !D.records.length ? 'disabled' : ''}>清空全部记录</button></div>`;
  if (!D.records.length) {
    html += `<p class="muted">暂无记录</p>`;
  } else {
    html += `<table><thead><tr><th>UP 主</th><th>群 ID</th><th>平台</th><th>平台实例</th><th>订阅者</th><th>订阅时间</th><th></th></tr></thead><tbody>`;
    for (const r of D.records) {
      html += `<tr>
        <td>${esc(r.uname || '-')}<div class="muted">UID ${esc(r.bilibili_id)}</div></td>
        <td>${esc(r.group_id || '-')}</td>
        <td>${esc(platformLabel(r.platform))}</td>
        <td>${esc(r.platform_id || '-')}</td>
        <td>${esc(r.qq_id || '-')}</td>
        <td>${fmt(r.created_at)}</td>
        <td><button class="red" data-act="dataDel" data-arg="${esc(r.id)}">删除</button></td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `</div>`;
  html += `<div class="card"><h2>绑定的群（绑定B站推送服务）</h2>
    <p class="muted" style="margin-top:0">群内发「<b>绑定B站推送 [自定义群名]</b>」登记的群（仅群主/群管理员/AstrBot 管理员可用），持久化保存群 ID + 群名 + <b>处理者</b>（处理这条命令的机器人 ID 和名字）。QQ 官方平台拿不到群名/机器人名，可在命令里带群名或在这里点「编辑」自定义。</p>
    <div class="actions" style="margin:0 0 10px"><button class="red" data-act="bindsClear" ${D.loading || !D.binds.length ? 'disabled' : ''}>清空全部绑定</button></div>`;
  if (!D.binds.length) {
    html += `<p class="muted">暂无绑定群</p>`;
  } else {
    html += `<table><thead><tr><th>群名</th><th>群 ID</th><th>平台</th><th>处理者 ID</th><th>处理者名字</th><th>绑定人</th><th>绑定时间</th><th></th></tr></thead><tbody>`;
    for (const r of D.binds) {
      html += `<tr>
        <td>${esc(r.group_name || '（未取到群名）')}</td>
        <td>${esc(r.group_id || '-')}</td>
        <td>${esc(platformLabel(r.platform))}</td>
        <td>${esc(r.handler_id || '-')}</td>
        <td>${esc(r.handler_name || '-')}</td>
        <td>${esc(r.bound_by || '-')}</td>
        <td>${fmt(r.bound_at)}</td>
        <td><button class="ghost" data-act="bindsEdit" data-arg="${esc(r.id)}">编辑</button>
          <button class="red" data-act="bindsDel" data-arg="${esc(r.id)}">删除</button></td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }
  html += `</div>`;
  html += `<div class="card"><h2>数据文件（data/ 目录）</h2>
    <table><thead><tr><th>文件</th><th>大小</th></tr></thead><tbody>
    ${(o.files || []).map(f => `<tr><td>${esc(f.name)}</td><td>${fmtSize(f.size)}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">空</td></tr>'}
    </tbody></table></div>`;
  return html;
}
