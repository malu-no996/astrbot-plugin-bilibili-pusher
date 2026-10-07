/* 子页签：账号（原 frag/account）—— 扫码登录 / 状态 / 退出 / 续期 */

async function refreshState() {
  S.busy = true;
  const j = await GET('state');
  if (j.ok) S.state = Object.assign({ logged: false, user: {}, login_at: 0, expires_at: 0, unread: {}, has_refresh_token: false, qr_ready: true }, j);
  else notify(j.message || '读取 B 站状态失败', 'err');
  S.busy = false;
  render();
}

async function loginStart() {
  const L = S.login;
  L.busy = true; L.tip = '正在获取二维码…'; L.png = ''; L.url = '';
  render();
  const j = await POST('login/start');
  if (!j.ok) { L.busy = false; L.tip = j.message || '获取二维码失败'; notify(j.message || '获取二维码失败', 'err'); render(); return; }
  L.key = j.qrcode_key;
  L.url = j.url;
  L.png = j.png || '';
  L.tip = '请用哔哩哔哩 App 扫码（' + (j.expires_in || 180) + ' 秒内有效）';
  L.busy = false;
  render();
  clearInterval(L.timer);
  L.timer = setInterval(async () => {
    const p = await GET('login/poll', { k: L.key });
    if (p.status === 'success') {
      clearInterval(L.timer);
      L.png = ''; L.url = ''; L.tip = '';
      notify(p.message || 'B 站登录成功', 'ok');
      await refreshState();
      loadSessions();   // 登录后顺手把私信会话拉出来
    } else if (p.status === 'scanned') { L.tip = '已扫码，请在手机上确认'; render(); }
    else if (p.status === 'waiting') { L.tip = '等待扫码…'; render(); }
    else if (!p.status) { /* 轮询中断（网络抖动等）不影响页面，下个周期继续 */ }
    else { clearInterval(L.timer); L.tip = p.message || '二维码已失效，请重新获取'; notify(L.tip, 'err'); render(); }
  }, 2000);
}

async function logout() {
  if (!confirmBox('确定退出 B 站登录？本地保存的 Cookie 会被清除。')) return;
  const j = await POST('logout');
  notify(j.message, j.ok ? 'ok' : 'err');
  if (j.ok) {
    S.state = Object.assign({}, S.state, { logged: false, user: {}, unread: {}, login_at: 0, expires_at: 0 });
    S.sessions.list = []; S.msgs = { loading: false, talker: '', name: '', list: [], error: '' };
    followClearCache(); S.follow.list = []; S.follow.tags = []; S.follow.tagid = -1;
    dynReset(); S.finder.user = null; S.livefinder.user = null;
    render();
  }
}

async function refreshToken() {
  const j = await POST('refresh');
  notify(j.message, j.ok ? 'ok' : 'err');
  refreshState();
}

function renderAccount() {
  const st = S.state;
  if (st.logged) {
    return `<div class="flex" style="gap:12px">
      ${st.user.face ? img(st.user.face, 'face face-46') : ''}
      <div>
        <b>${esc(st.user.uname || '（未知昵称）')}</b> <span class="muted">UID ${esc(st.user.mid)}</span>
        <div class="muted">登录于 ${fmt(st.login_at)}${st.expires_at ? ' · 有效期至 ' + fmt(st.expires_at) : ''}${st.unread && st.unread.total ? ' · 未读私信 ' + st.unread.total : ''}</div>
        ${st.unread_error ? `<div class="err">${esc(st.unread_error)}</div>` : ''}
      </div>
      <div class="flex" style="margin-left:auto">
        <button class="ghost" data-act="refreshState" ${S.busy ? 'disabled' : ''}>刷新</button>
        <button class="ghost" data-act="refreshToken" ${st.has_refresh_token ? '' : 'disabled'}>续期</button>
        <button class="red" data-act="logout">退出登录</button>
      </div>
    </div>`;
  }
  const L = S.login;
  return `<p class="muted">未登录。点「扫码登录」后用<b>哔哩哔哩 App</b> 扫码确认（3 分钟内有效）。</p>
    <div class="flex">
      <button data-act="loginStart" ${L.busy ? 'disabled' : ''}>${L.busy ? '获取中…' : '扫码登录'}</button>
      ${st.qr_ready === false ? '<span class="muted">未安装二维码依赖 segno（未安装时以链接形式登录）</span>' : ''}
    </div>
    ${L.url ? `<div style="margin-top:12px">
      ${L.png ? `<img src="${esc(L.png)}" width="180" height="180" alt="登录二维码" class="qr-img">` : ''}
      <div class="muted" style="margin-top:6px">${esc(L.tip)}</div>
      <div class="muted">二维码显示不出来？<a href="${esc(L.url)}" target="_blank">点此用浏览器打开登录链接</a></div>
    </div>` : ''}`;
}
