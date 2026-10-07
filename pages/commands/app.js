/* 命令配置页：两条订阅命令的触发词 / 开关 / 仅管理员，保存即时生效。 */
const B = window.AstrBotPluginPage;

const S = {
  config: null,   // {sub:{enabled,triggers,admin_only,desc}, unsub:{...}}
  static: [],
  busy: false,
  tip: "",        // {type: 'ok'|'err', text}
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function tip(type, text) {
  S.tip = { type, text };
  render();
  if (text) setTimeout(() => { if (S.tip && S.tip.text === text) { S.tip = null; render(); } }, 4000);
}

/* ---------------- 渲染 ---------------- */

function render() {
  const box = document.getElementById("cmdCards");
  if (!S.config) {
    box.innerHTML = `<p class="muted">加载中…</p>`;
  } else {
    box.innerHTML = ["sub", "unsub"].map((k) => {
      const c = S.config[k] || {};
      return `<div class="card">
        <h3>${k === "sub" ? "订阅B站推送" : "取消B站推送"}</h3>
        <p class="desc muted">${esc(c.desc || "")}</p>
        <div class="row">
          <label><input type="checkbox" data-k="${k}" data-f="enabled" ${c.enabled ? "checked" : ""}> 启用</label>
          <label><input type="checkbox" data-k="${k}" data-f="admin_only" ${c.admin_only ? "checked" : ""}> 仅管理员可用</label>
        </div>
        <div class="triggers">
          <input type="text" data-k="${k}" data-f="triggers"
            value="${esc(c.triggers_raw ?? (c.triggers || []).join("，"))}"
            placeholder="触发词，多个用中文或英文逗号分隔，例：订阅B站推送，订阅UP">
          <p class="muted">触发词命中规则：群消息去掉 @机器人 和 / 前缀后，以触发词开头即命中；${k === "sub" ? "后面跟 UP 主 UID" : "可不带 UID（= 取消本群全部命令订阅）"}。</p>
        </div>
      </div>`;
    }).join("");
  }

  document.getElementById("staticRows").innerHTML = S.static.map((c) =>
    `<tr>
      <td>${esc(c.name)}</td>
      <td>${esc((c.aliases || []).join(" / "))}</td>
      <td>${esc(c.desc || "")}</td>
      <td>${esc(c.usage || "")}</td>
    </tr>`).join("");

  const st = document.getElementById("saveState");
  st.textContent = S.tip ? S.tip.text : "";

  const save = document.getElementById("save");
  save.disabled = S.busy || !S.config;
  save.textContent = S.busy ? "保存中…" : "保存配置";
}

/* ---------------- 数据加载 / 保存 ---------------- */

async function load() {
  try {
    const j = await B.apiGet("cmds");
    S.config = j.config || null;
    S.static = j.static || [];
  } catch (e) {
    tip("err", `加载失败：${e.message}`);
  }
  render();
}

async function save() {
  if (!S.config) return;
  S.busy = true; render();
  const body = {};
  for (const k of ["sub", "unsub"]) {
    const c = S.config[k] || {};
    body[k] = {
      enabled: !!c.enabled,
      admin_only: !!c.admin_only,
      triggers: String(c.triggers_raw ?? "")
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    };
  }
  try {
    const j = await B.apiPost("cmds/save", body);
    S.config = j.config || S.config;
    // 保存后用后端规整过的 triggers 回填输入框
    for (const k of ["sub", "unsub"]) {
      S.config[k].triggers_raw = (S.config[k].triggers || []).join("，");
    }
    tip("ok", j.message || "已保存");
  } catch (e) {
    tip("err", `保存失败：${e.message}`);
  }
  S.busy = false;
  render();
}

/* ---------------- 事件 ---------------- */

document.addEventListener("change", (e) => {
  const el = e.target;
  const k = el.dataset.k;
  const f = el.dataset.f;
  if (!k || !f || !S.config || !S.config[k]) return;
  if (f === "triggers") S.config[k].triggers_raw = el.value;
  else S.config[k][f] = el.checked;
});

document.getElementById("save").addEventListener("click", save);
document.getElementById("reload").addEventListener("click", load);

/* ---------------- 启动 ---------------- */

await B.ready();
document.title = B.getContext()?.pageTitle || "命令配置";
render();
await load();
