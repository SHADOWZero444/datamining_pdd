const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}


function money(value) {
  if (!value) return "—";
  return String(value).startsWith("¥") ? value : `¥${value}`;
}


function numericValue(value) {
  const text = String(value ?? "").replace(/,/g, "");
  const match = text.match(/\d+(?:\.\d+)?/);
  if (!match) return -1;
  const number = Number(match[0]);
  return text.includes("万") ? number * 10000 : number;
}


function icon(name) {
  return `<img class="nav-ico" src="/icons/${name}" alt="">`;
}


function askModal({ title, text, confirmLabel, field = "" }) {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.hidden = false;
    modal.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <h2 id="dialog-title">${esc(title)}</h2>
      <p class="dialog-text">${esc(text)}</p>
      ${field ? `<div class="dialog-field"><label class="field-label" for="dialog-field">${esc(field)}</label><input id="dialog-field" type="text"></div>` : ""}
      <div class="dialog-actions"><button class="ghost" type="button" id="dialog-cancel">取消</button><button class="start" type="button" id="dialog-ok">${esc(confirmLabel)}</button></div>
    </div>`;
    const finish = (value) => { modal.hidden = true; modal.innerHTML = ""; resolve(value); };
    $("dialog-cancel").onclick = () => finish(null);
    $("dialog-ok").onclick = () => finish(field ? $("dialog-field").value.trim() : true);
    $("dialog-ok").focus();
  });
}


async function api(path, options) {
  const headers = new Headers(options?.headers);
  headers.set("X-PDD-App", "1");
  const response = await fetch(path, { ...options, headers });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}


function safeWebUrl(value, productOnly = false) {
  try {
    const url = new URL(String(value || ""));
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return "";
    if (productOnly && url.hostname !== "pinduoduo.com" && !url.hostname.endsWith(".pinduoduo.com")) return "";
    return url.href;
  } catch { return ""; }
}
