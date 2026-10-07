/* 子页签：私信（原 frag/dm）—— 会话列表 / 消息记录 */

async function loadSessions(force) {
  S.sessions.loading = true; S.sessions.error = '';
  render();
  const j = await GET('sessions', force ? { force: 1 } : {});
  if (j.ok) S.sessions.list = j.sessions || [];
  else S.sessions.error = j.message || '读取私信会话失败';
  S.sessions.loading = false;
  render();
}

async function openSession(s) {
  S.msgs.talker = s.talker_id; S.msgs.name = s.name;
  S.msgs.loading = true; S.msgs.error = '';
  render();
  const j = await GET('messages', { talker_id: s.talker_id });
  if (j.ok) S.msgs.list = j.messages || [];
  else { S.msgs.list = []; S.msgs.error = j.message || '读取消息失败'; }
  S.msgs.loading = false;
  render();
}

function renderDm() {
  const se = S.sessions;
  let html = `<div class="flex">
    <button class="ghost" data-act="loadSessions" ${!S.state.logged || se.loading ? 'disabled' : ''}>${se.loading ? '加载中…' : '刷新会话'}</button>
    <span class="muted">${S.state.logged
      ? `未读：关注 ${(S.state.unread && S.state.unread.follow) || 0} / 陌生人 ${(S.state.unread && S.state.unread.unfollow) || 0}`
      : '需先登录'}</span>
  </div>`;
  if (se.error) html += `<p class="err">${esc(se.error)}</p>`;
  if (se.list.length) {
    html += `<table style="margin-top:10px"><thead><tr><th>会话</th><th>最后一条</th><th style="width:70px">未读</th><th style="width:120px">时间</th></tr></thead><tbody>`;
    for (const s of se.list) {
      html += `<tr class="clickable" data-act="openSession" data-arg="${esc(s.talker_id)}">
        <td><div class="flex" style="gap:8px">${s.face ? img(s.face, 'face face-28') : ''}
          <div><b>${esc(s.name || 'UID ' + s.talker_id)}</b><div class="muted">${esc(s.talker_id)}</div></div></div></td>
        <td class="muted">${esc(short(s.last_text, 26))}</td>
        <td>${s.unread ? `<span class="badge online">${esc(s.unread)}</span>` : ''}</td>
        <td class="muted">${fmt(s.ts)}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  const m = S.msgs;
  if (m.talker) {
    html += `<div style="margin-top:14px"><h2 style="margin-bottom:8px">与 ${esc(m.name || m.talker)} 的消息 <span class="hint">最近 ${m.list.length} 条</span></h2>`;
    if (m.error) html += `<p class="err">${esc(m.error)}</p>`;
    if (m.loading) html += `<p class="muted">加载中…</p>`;
    html += `<div class="dm-list">`;
    for (const msg of m.list) {
      html += `<div style="margin-bottom:10px"><div class="muted">${msg.self ? '我' : esc(m.name || msg.sender_uid)} · ${fmt(msg.ts)}</div>
        <div class="dm-bubble ${msg.self ? 'me' : 'other'}">${esc(msg.text)}</div></div>`;
    }
    html += `</div></div>`;
  }
  return html;
}
