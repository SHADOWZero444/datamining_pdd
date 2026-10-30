const TOP = [
  { id: "home", label: "主界面", icon: "nav-home.png" },
  { id: "dashboard", label: "仪表盘", icon: "nav-dashboard.png" },
  { id: "tools", label: "功能区", icon: "nav-tools.png" },
  { id: "settings", label: "系统设置", icon: "nav-settings.png" },
  { id: "about", label: "关于系统", icon: "nav-about.png" },
];

const TAGS = [
  { id: "bybt", label: "百亿补贴" },
  { id: "black", label: "品牌黑标" },
  { id: "official", label: "官方旗舰" },
  { id: "flagship", label: "旗舰店" },
];

const state = {
  section: preferences.startup,
  toolLabel: "功能区概览",
  keywords: [],
  job: { running: false, status: "idle", message: "", keyword: "" },
  tabs: [],
  active: null,
  keywordsOpen: true,
  library: { groups: [], entries: [] },
  multi: false,
  selected: [],
  goods: Object.create(null),
  revisions: Object.create(null),
  metadata: Object.create(null),
  views: Object.create(null),
  auth: { running: false, status: "idle", message: "正在读取登录状态…", configured: false, cookie: "" },
  form: { keyword: "", priceMin: "", priceMax: "", requiredWords: "", banned: "", tags: [], limit: preferences.limit },
};

// 每页条数和序号范围一起生效。任一栏填错，两项都不改。

// 词条操作用图片图标，和左侧导航同一套，放在 icons 文件夹
const ICONS = {
  more: icon("nav-more.png"),
  pin: icon("nav-pin.png"),
  close: icon("nav-close.png"),
  rename: icon("nav-rename.png"),
  group: icon("nav-group.png"),
  trash: icon("nav-trash.png"),
};

const EYE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M2.5 12s3.4-5.5 9.5-5.5S21.5 12 21.5 12 18.1 17.5 12 17.5 2.5 12 2.5 12Z"></path><circle cx="12" cy="12" r="2.5"></circle></svg>`;

function badgeText(id) {
  return TAGS.find((item) => item.id === id)?.label || id;
}

const { openTab, closeTab, activeSearch, renderTabs, moveTab, bindTabDrag } =
  createNavigationFeature({ state, ICONS, esc, render });
const { entryOf, renderNav, openEntryMenu, openGroupMenu, moveKeywords, removeKeywords } =
  createLibraryFeature({ state, TOP, ICONS, esc, icon, askModal, api, render, closeTab });
const { applyListTools, viewOf, describeConditions, sortedAndRangedRows, searchForm, resultPage, detailPage, updateResultPage } =
  createSearchFeature({ state, preferences, TAGS, badgeText, esc, money, numericValue, safeWebUrl, entryOf, renderStage });
const { renderCaptureBar } = createCaptureFeature({ state, capturePanel });
const { topPage } = createPagesFeature({ state, TOP, EYE_ICON, esc, settingsPage });
const handleClick = createActionHandler({ state, api, render, renderStage, renderNav, renderCaptureBar, capturePanel,
  loadGoods, openTab, closeTab, viewOf, openOverviewFeature, askModal, openEntryMenu, openGroupMenu, moveKeywords, removeKeywords });
const handleSubmit = createFormHandler({ state, api, render, applyListTools, viewOf, sortedAndRangedRows, renderStage, loadGoods, openTab });
document.body.addEventListener("click", (event) => handleClick(event).catch(showActionError));
document.body.addEventListener("submit", (event) => handleSubmit(event).catch(showActionError));
bindClipboard();

function showActionError(error) {
  askModal({ title: "操作未完成", text: error.message || "请稍后重试。", confirmLabel: "知道了" });
}

const stagePositions = new Map();
let renderedStageKey = null;

function renderStage() {
  const stage = $("stage");
  // active 已经切换，用上次实际渲染的标识保存离开的页面。
  if (renderedStageKey !== null) {
    stagePositions.delete(renderedStageKey);
    stagePositions.set(renderedStageKey, {
      top: stage.scrollTop, left: stage.scrollLeft,
      tableLeft: stage.querySelector(".catalog-scroll")?.scrollLeft || 0,
    });
    if (stagePositions.size > 100) stagePositions.delete(stagePositions.keys().next().value);
  }
  const tab = state.tabs.find((item) => item.id === state.active);
  if (state.section !== "tools") {
    stage.innerHTML = topPage();
  } else {
    if (tab && tab.kind === "goods") stage.innerHTML = detailPage(tab);
    else if (tab && tab.kind === "search") stage.innerHTML = resultPage(tab.keyword);
    else if (state.toolLabel === "搜索词条爬取") stage.innerHTML = searchForm();
    else { stage.innerHTML = toolsOverview(); hydrateFeatureImages(); }
  }
  const key = state.section !== "tools" ? state.section
    : tab ? `${tab.id}:${tab.kind === "search" ? JSON.stringify(viewOf(tab.keyword)) : ""}`
    : `tools:${state.toolLabel}`;
  renderedStageKey = key;
  const position = stagePositions.get(key);
  stage.scrollTop = position?.top || 0;
  stage.scrollLeft = position?.left || 0;
  const table = stage.querySelector(".catalog-scroll");
  if (table) table.scrollLeft = position?.tableLeft || 0;
}

function render() {
  renderCaptureBar();
  renderNav();
  renderTabs();
  renderStage();
}

async function loadGoods(keyword) {
  const revision = state.revisions[keyword] || "";
  const data = await api(`/api/results?keyword=${encodeURIComponent(keyword)}&revision=${encodeURIComponent(revision)}`);
  if (!data.unchanged) {
    if (data.delta) {
      const rows = new Map((state.goods[keyword] || []).map((row) => [row.goods_id, row]));
      (data.removed || []).forEach((id) => rows.delete(id));
      (data.goods || []).forEach((row) => rows.set(row.goods_id, row));
      state.goods[keyword] = [...rows.values()];
    } else state.goods[keyword] = data.goods || [];
  }
  state.revisions[keyword] = data.revision;
  state.metadata[keyword] = data.metadata || {};
  return !data.unchanged;
}

async function loadCookie() {
  const data = await api("/api/cookie");
  state.auth = { ...state.auth, ...(data.auth || {}) };
}

let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
  const active = state.active;
  const section = state.section;
  const data = await api("/api/state");
  const jobChanged = JSON.stringify(data.job) !== JSON.stringify(state.job);
  const keywordsChanged = JSON.stringify(data.keywords) !== JSON.stringify(state.keywords);
  const libraryChanged = JSON.stringify(data.library) !== JSON.stringify(state.library);
  state.keywords = data.keywords || [];
  if (data.library) state.library = data.library;
  state.library = data.library || { groups: [], entries: (data.keywords || []).map((item) => ({ ...item, title: item.keyword, pinned: false, groupId: "" })) };
  state.job = data.job || state.job;
  const wasWaitingForLogin = Boolean(state.auth.running);
  const authChanged = JSON.stringify({ ...state.auth, cookie: undefined }) !== JSON.stringify({ ...(data.auth || {}), cookie: undefined });
  state.auth = { ...state.auth, ...(data.auth || {}) };
  if (wasWaitingForLogin && !state.auth.running && state.auth.status === "ok") await loadCookie();
  const tab = state.tabs.find((item) => item.id === state.active);
  let goodsChanged = false;
  if (tab && (tab.kind === "search" || tab.kind === "goods")) {
    goodsChanged = await loadGoods(tab.keyword);
  }
  renderCaptureBar();
  if (keywordsChanged || libraryChanged) renderNav();
  if (state.active !== active || state.section !== section) return;
  if (tab?.kind === "search" && state.section === "tools" && (jobChanged || goodsChanged)) {
    updateResultPage(tab.keyword);
  } else if (tab?.kind === "goods" && state.section === "tools" && (goodsChanged || jobChanged)) {
    if (!$("stage").contains(document.activeElement) && !window.getSelection()?.toString()) renderStage();
  } else if (state.section === "home" && authChanged && !$("cookie-form")?.contains(document.activeElement)) {
    renderStage();
  }
  const start = document.querySelector("#search-form .start");
  if (state.section === "settings" && authChanged && $("settings-login-state")) {
    $("settings-login-state").textContent = state.auth.configured ? "本机已保存登录信息；是否有效以网站响应为准。" : "尚未保存登录信息。";
  }
  if (start) { start.disabled = state.job.running; start.textContent = start.disabled ? "正在抓取" : "开始"; }
  } finally { refreshing = false; }
}

$("nav-toggle").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("open");
  $("nav-toggle").setAttribute("aria-expanded", String(open));
});

bindTabDrag($("tabs-main"));
bindTabDrag($("tabs-detail"));

applyPreferences();
render();
loadCookie().then(() => render()).catch(() => {});
refresh().catch((exc) => {
  $("stage").innerHTML = `<p class="empty">${esc(exc.message)}</p>`;
});
async function poll() {
  try { await refresh(); }
  catch (_) {
    if (state.job.running) {
      state.job.message = "暂时无法连接，正在重试；已显示的结果仍然保留。";
      renderCaptureBar();
    }
  } finally { setTimeout(poll, 1000); }
}
setTimeout(poll, 1000);
