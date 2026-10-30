const SETTINGS_DEFAULTS = Object.freeze({ theme: "day", startup: "home" });
const SETTINGS_KEY = "pdd-preferences-v1";
const SETTINGS_CHOICES = { theme: ["day", "ink", "warm", "system"], startup: ["home", "tools"] };
// 这三项已从设置页去掉。列表和搜索表单仍读取它们，所以固定成原来的默认值，不再跟本机保存的旧偏好走。
const FIXED_PREFERENCES = Object.freeze({ pageSize: 100, images: true, limit: "" });
let settingsMessage = "更改自动保存到本机";
let settingsError = false;

function normalizeSettings(raw) {
  const clean = { ...SETTINGS_DEFAULTS };
  if (!raw || typeof raw !== "object") return clean;
  for (const [key, choices] of Object.entries(SETTINGS_CHOICES)) if (choices.includes(raw[key])) clean[key] = raw[key];
  return clean;
}

// 配色和启动页仍可保存。pageSize、images、limit 固定为默认值，本机里的旧偏好不再读进来。
function withFixed(settings) {
  return { ...FIXED_PREFERENCES, ...settings };
}

function readSettings() {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY);
    return normalizeSettings(saved ? JSON.parse(saved) : { theme: localStorage.getItem("pdd-theme") });
  } catch (_) {
    settingsMessage = "偏好未能读取，当前使用默认设置。你可以重新调整。";
    settingsError = true;
    return { ...SETTINGS_DEFAULTS };
  }
}
let preferences = withFixed(readSettings());
const systemDarkMode = window.matchMedia("(prefers-color-scheme: dark)");

function applyPreferences() {
  const root = document.documentElement;
  root.dataset.theme = preferences.theme === "system" ? (systemDarkMode.matches ? "ink" : "day") : preferences.theme;
}
systemDarkMode.addEventListener("change", () => { if (preferences.theme === "system") applyPreferences(); });

function settingsStatus(message, error = false) {
  settingsMessage = message;
  settingsError = error;
  const target = document.getElementById("settings-status");
  if (target) { target.textContent = message; target.classList.toggle("error", error); }
}

function saveSettings(next) {
  const clean = normalizeSettings(next);
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(clean)); }
  catch (_) { settingsStatus("保存失败，本次更改未应用。请检查是否允许保存本地数据后重试。", true); return false; }
  preferences = withFixed(clean);
  applyPreferences();
  settingsStatus("已保存。配色立即生效，启动页在下次打开时生效。");
  return true;
}

function settingsPage() {
  const select = (key, options) => `<select id="setting-${key}" data-preference="${key}" aria-describedby="setting-${key}-help">${options.map(([value, label]) => `<option value="${value}" ${String(preferences[key]) === String(value) ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  const row = (key, title, help, control) => `<div class="setting-row"><div class="setting-copy"><label for="setting-${key}">${title}</label><p id="setting-${key}-help">${help}</p></div><div class="setting-control">${control}</div></div>`;
  return `<section class="settings-page" aria-labelledby="settings-title">
    <header class="settings-header"><h1 class="page-title" id="settings-title">系统设置</h1><p class="lede">调整配色和启动页面。</p><p id="settings-status" class="settings-status ${settingsError ? "error" : ""}" role="status">${esc(settingsMessage)}</p></header>
    <section class="settings-group" aria-labelledby="settings-appearance"><h2 id="settings-appearance">外观与启动</h2>
      ${row("theme", "配色方案", "选择固定配色，或随系统切换明暗。", select("theme", [["day","白天"],["ink","墨色"],["warm","暖纸"],["system","跟随系统"]]))}
      ${row("startup", "启动页面", "下次打开软件或刷新页面时生效。", select("startup", [["home","主界面 · 登录管理"],["tools","功能区概览"]]))}
    </section>
    <section class="settings-group" aria-labelledby="settings-data"><h2 id="settings-data">登录与数据</h2>
      <div class="setting-row"><div class="setting-copy"><h3>登录信息</h3><p id="settings-login-state">${state.auth.configured ? "本机已保存登录信息；是否有效以网站响应为准。" : "尚未保存登录信息。"}</p></div><button type="button" class="ghost" data-section="home">管理登录信息</button></div>
      <div class="setting-row"><div class="setting-copy"><h3>商品数据</h3><p>结果按词条自动保存在软件目录的 <code>data/searches/</code> 中。备份时请复制整个文件夹。</p></div><button type="button" class="ghost" data-section="tools">返回功能区</button></div>
      <details class="settings-reset"><summary>恢复默认偏好</summary><p>恢复配色和启动页，不删除登录信息或商品数据。</p><button type="button" class="ghost" id="settings-reset">恢复默认偏好</button></details>
    </section>
  </section>`;
}

document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-preference]");
  if (!input) return;
  const key = input.dataset.preference;
  if (!saveSettings({ ...preferences, [key]: input.value })) input.value = String(preferences[key]);
});

document.addEventListener("click", (event) => {
  if (event.target.closest("#settings-reset") && window.confirm("恢复默认偏好？登录信息和已保存的商品不会被删除。")) {
    if (saveSettings(SETTINGS_DEFAULTS)) {
      renderStage();
      document.getElementById("settings-title").setAttribute("tabindex", "-1");
      document.getElementById("settings-title").focus();
    }
  }
});
