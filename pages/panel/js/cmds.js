/* 子页签：命令（群内订阅命令的触发词 / 开关 / 权限配置） */

async function loadCmds() {
  const C = S.cmds;
  C.loading = true; render();
  const j = await GET('cmds');
  if (j.ok) {
    C.config = j.config || null;
    C.static = j.static || [];
    if (C.config) {
      for (const k of ['sub', 'unsub']) {
        if (C.config[k]) C.config[k].triggers_raw = (C.config[k].triggers || []).join('，');
      }
    }
  } else notify(j.message || '读取命令配置失败', 'err');
  C.loading = false;
  render();
}

async function saveCmds() {
  const C = S.cmds;
  if (!C.config) return;
  C.loading = true; render();
  const body = {};
  for (const k of ['sub', 'unsub']) {
    const c = C.config[k] || {};
    body[k] = {
      enabled: !!c.enabled,
      admin_only: !!c.admin_only,
      triggers: String(c.triggers_raw || '').split(/[,，\n]/).map(s => s.trim()).filter(Boolean),
    };
  }
  const j = await POST('cmds/save', body);
  if (j.ok) {
    C.config = j.config || C.config;
    for (const k of ['sub', 'unsub']) {
      if (C.config[k]) C.config[k].triggers_raw = (C.config[k].triggers || []).join('，');
    }
    notify(j.message || '命令配置已保存', 'ok');
  } else notify(j.message || '保存命令配置失败', 'err');
  C.loading = false;
  render();
}

function renderCmds() {
  const C = S.cmds;
  let html = '';
  if (!C.config) {
    html += `<div class="card"><p class="muted">${C.loading ? '加载中…' : '未加载，点「重新加载」'}</p></div>`;
  } else {
    html += `<p class="muted hint" style="margin:0 0 10px">两条订阅命令改完点「保存配置」立即生效（不用重启）。群消息去掉 @机器人 和 / 前缀后，以触发词开头即命中；触发词后面跟 UP 主 UID（纯数字或 space 主页链接）。</p>`;
    for (const [k, name] of [['sub', '订阅B站推送'], ['unsub', '取消B站推送']]) {
      const c = C.config[k] || {};
      html += `<div class="card"><h2>${name}</h2>
        <p class="muted" style="margin-top:0">${esc(c.desc || '')}</p>
        <div class="row">
          <label><input type="checkbox" data-model="cmds.config.${k}.enabled" ${c.enabled ? 'checked' : ''}> 启用</label>
          <label><input type="checkbox" data-model="cmds.config.${k}.admin_only" ${c.admin_only ? 'checked' : ''}> 仅管理员可用</label>
        </div>
        <div class="row"><label>触发词</label>
          <input type="text" style="flex:1;min-width:220px" data-model="cmds.config.${k}.triggers_raw"
            value="${esc(c.triggers_raw ?? (c.triggers || []).join('，'))}"
            placeholder="多个用逗号分隔，例：${k === 'sub' ? '订阅B站推送，订阅UP' : '取消B站推送，退订UP'}"></div>
      </div>`;
    }
    html += `<div class="actions" style="margin-bottom:14px">
      <button data-act="cmdsSave" ${C.loading ? 'disabled' : ''}>保存配置</button>
      <button class="ghost" data-act="cmdsReload" ${C.loading ? 'disabled' : ''}>重新加载</button>
    </div>`;
  }
  html += `<div class="card"><h2>内置命令（固定，仅管理员）</h2>
    <p class="muted" style="margin-top:0">这三条由 AstrBot 命令系统静态注册，触发词/权限改不了，只作展示。</p>
    <table><thead><tr><th>命令</th><th>别名</th><th>说明</th><th>用法</th></tr></thead><tbody>
    ${C.static.map(c => `<tr><td>${esc(c.name)}</td><td>${esc((c.aliases || []).join(' / '))}</td><td>${esc(c.desc || '')}</td><td>${esc(c.usage || '')}</td></tr>`).join('')}
    </tbody></table></div>`;
  return html;
}
