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
  section: "home",
  toolLabel: "功能区概览",
  keywords: [],
  job: { running: false, status: "idle", message: "", keyword: "" },
  tabs: [],
  active: null,
  keywordsOpen: true,
  library: { groups: [], entries: [] },
  multi: false,
  selected: [],
  goods: {},
  revisions: {},
  metadata: {},
  views: {},
  auth: { running: false, status: "idle", message: "正在读取登录状态…", configured: false, cookie: "" },
  form: { keyword: "", priceMin: "", priceMax: "", banned: "", tags: [], limit: "" },
};

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

// 每页条数和序号范围一起生效。任一栏填错，两项都不改。
function applyListTools(form) {
  const keyword = form.dataset.listTools;
  const values = new FormData(form);
  const pageSize = Number(values.get("pageSize"));
  const startId = String(values.get("startId") || "").trim();
  const endId = String(values.get("endId") || "").trim();
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 10000) return;
  if (startId && endId && Number(startId) > Number(endId)) return;
  const view = viewOf(keyword);
  view.pageSize = pageSize;
  view.startId = startId;
  view.endId = endId;
  view.page = 1;
  renderStage();
}

function viewOf(keyword) {
  if (!state.views[keyword]) {
    state.views[keyword] = { page: 1, pageSize: 100, startId: "", endId: "", sort: null };
  }
  return state.views[keyword];
}

function describeConditions(keyword) {
  const metadata = state.metadata[keyword] || {};
  const filters = metadata.filters || {};
  const tags = (filters.tags || []).map(badgeText).join("、") || "无";
  const low = filters.price_min;
  const high = filters.price_max;
  const price = low == null && high == null ? "无" : `${low == null ? "不限" : `¥${low}`} 至 ${high == null ? "不限" : `¥${high}`}`;
  return `搜索词：${metadata.keyword || keyword}　标签：${tags}　价格区间：${price}　禁用词：${filters.banned || "无"}`;
}

function sortedAndRangedRows(keyword) {
  const view = viewOf(keyword);
  const rows = [...(state.goods[keyword] || [])];
  const start = numericValue(view.startId);
  const end = numericValue(view.endId);
  let visible = rows.filter((item) => {
    const seq = numericValue(item.seq);
    return (start < 0 || seq >= start) && (end < 0 || seq <= end);
  });
  const sort = view.sort;
  if (!sort) return visible.sort((a, b) => numericValue(a.seq) - numericValue(b.seq));
  const field = sort.field;
  const factor = sort.direction === "asc" ? 1 : -1;
  return visible.sort((a, b) => {
    const aValue = field === "title" || field === "shop_name" ? String(a[field] || "") : numericValue(a[field]);
    const bValue = field === "title" || field === "shop_name" ? String(b[field] || "") : numericValue(b[field]);
    if (typeof aValue === "string") return factor * aValue.localeCompare(bValue, "zh-CN");
    return factor * (aValue - bValue);
  });
}

function icon(name) {
  return `<img class="nav-ico" src="/icons/${name}" alt="">`;
}

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

function entryOf(keyword) {
  return state.library.entries.find((item) => item.keyword === keyword);
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

function badgeText(id) {
  return TAGS.find((item) => item.id === id)?.label || id;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}

function openTab(tab) {
  if (!state.tabs.some((item) => item.id === tab.id)) state.tabs.push(tab);
  state.active = tab.id;
  if (tab.kind === "form" || tab.kind === "search" || tab.kind === "goods") state.section = "tools";
  render();
}

function closeTab(id) {
  const tab = state.tabs.find((item) => item.id === id);
  state.tabs = state.tabs.filter((item) => item.id !== id && !(tab && tab.kind === "search" && item.kind === "goods" && item.keyword === tab.keyword));
  if (tab && tab.kind === "goods" && state.active === id) {
    state.active = `result:${tab.keyword}`;
  } else if (!state.tabs.some((item) => item.id === state.active)) {
    state.active = null;
  }
  render();
}

function renderNav() {
  $("bookmark").textContent = TOP.find((item) => item.id === state.section)?.label || "功能区";
  const bookmarks = TOP.map((item) => `
    <button class="nav-btn" type="button" data-section="${item.id}" ${state.section === item.id ? 'aria-current="page"' : ""}>
      ${icon(item.icon)}${item.label}
    </button>
  `).join("");
  const entries = [...state.library.entries].sort((a, b) => Number(b.pinned) - Number(a.pinned));
  const renderEntry = (item) => `
    <div class="nav-item">
      <button class="dot" type="button" data-select="${esc(item.keyword)}" aria-pressed="${state.selected.includes(item.keyword)}" aria-label="选择${esc(item.title)}"></button>
      <button class="sub-btn" type="button" data-keyword="${esc(item.keyword)}" title="${esc(item.title)}" ${state.active === `result:${item.keyword}` ? 'aria-current="page"' : ""}><span class="nav-label">${esc(item.title)}</span></button>
      ${item.pinned ? `<span class="pin-mark" title="已置顶">${ICONS.pin}</span>` : ""}
      <button class="more" type="button" data-more="${esc(item.keyword)}" aria-label="${esc(item.title)}的操作">${ICONS.more}</button>
    </div>`;
  const groups = [...state.library.groups]
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)))
    .map((group) => `
      <div class="group-block">
        <div class="group-head">
          <button class="group-toggle" type="button" data-toggle-group="${esc(group.id)}" aria-expanded="${group.open !== false}"><span class="group-arrow ${group.open === false ? "" : "is-open"}" aria-hidden="true"></span><span class="nav-label">${esc(group.name)}</span></button>
          ${group.pinned ? `<span class="pin-mark" title="已置顶">${ICONS.pin}</span>` : ""}
          <button class="more" type="button" data-group-more="${esc(group.id)}" aria-label="${esc(group.name)}的操作">${ICONS.more}</button>
        </div>
        <div class="sub-list" ${group.open === false ? "hidden" : ""}>${entries.filter((item) => item.groupId === group.id).map(renderEntry).join("") || '<p class="help">这个分组还没有词条。</p>'}</div>
      </div>`).join("");
  const loose = entries.filter((item) => !item.groupId).map(renderEntry).join("");
  const tools = state.section === "tools" ? `
    <div class="sub-head">
      <button class="sub-btn" type="button" data-open-form ${state.toolLabel === "搜索词条爬取" && !state.active ? 'aria-current="page"' : ""}><span class="nav-label">搜索词条爬取</span></button>
      <button class="fold ${state.keywordsOpen ? "is-open" : ""}" type="button" data-fold-keywords aria-expanded="${state.keywordsOpen}" aria-label="${state.keywordsOpen ? "收起词条" : "展开词条"}"></button>
      <button class="ghost nav-mini" type="button" data-new-group>分组</button>
    </div>
    <div class="sub-list" ${state.keywordsOpen ? "" : "hidden"}>
      ${state.multi ? `<div class="multi-bar"><button class="icon-btn" type="button" data-exit-multi aria-label="退出多选">${ICONS.close}</button><button class="ghost nav-mini" type="button" data-delete-selected>删除所选</button><button class="ghost nav-mini" type="button" data-group-selected>归入分组</button></div>` : ""}
      ${groups}${loose || '<p class="help">抓取完成后，词条会出现在这里。</p>'}
    </div>
  ` : "";
  $("nav").innerHTML = `<div class="nav-group">${bookmarks}</div>${tools}`;
}

function activeSearch() {
  const current = state.tabs.find((item) => item.id === state.active);
  if (!current) return null;
  if (current.kind === "search") return current;
  if (current.kind === "goods") return state.tabs.find((item) => item.kind === "search" && item.keyword === current.keyword) || null;
  return null;
}

function renderTabs() {
  const stack = $("tab-stack");
  if (state.section !== "tools") {
    stack.hidden = true;
    return;
  }
  stack.hidden = false;
  const fixedSelected = !activeSearch() && !state.tabs.some((item) => item.id === state.active && item.kind === "goods");
  const keywords = state.tabs.filter((item) => item.kind === "search").map((tab) => `
    <div class="tab" role="tab" draggable="true" aria-selected="${Boolean(activeSearch() && activeSearch().id === tab.id)}" data-tab="${esc(tab.id)}">
      <span>${esc(tab.title)}</span>
      <button class="tab-close" type="button" data-close="${esc(tab.id)}" aria-label="关闭${esc(tab.title)}">×</button>
    </div>`).join("");
  $("tabs-main").innerHTML = `<button class="fixed-tab" type="button" data-fixed aria-selected="${fixedSelected}">${esc(state.toolLabel)}</button>${keywords}`;
  const parent = activeSearch();
  const details = parent ? state.tabs.filter((item) => item.kind === "goods" && item.keyword === parent.keyword) : [];
  const detailBar = $("tabs-detail");
  detailBar.hidden = !parent;
  detailBar.innerHTML = details.map((tab) => `
    <div class="tab" role="tab" draggable="true" aria-selected="${tab.id === state.active}" data-tab="${esc(tab.id)}">
      <span>${esc(tab.title)}</span>
      <button class="tab-close" type="button" data-close="${esc(tab.id)}" aria-label="关闭${esc(tab.title)}">×</button>
    </div>`).join("");
}

function searchForm() {
  const chips = TAGS.map((tag) => `
    <label class="chip"><input type="checkbox" name="tags" value="${tag.id}" ${state.form.tags.includes(tag.id) ? "checked" : ""}>${tag.label}</label>
  `).join("");
  return `
    <form class="sheet" id="search-form">
      <div>
        <h1 class="page-title">搜索词条爬取</h1>
        <p class="lede">填写搜索词和筛选条件。点开始之后，结果会打开一个新页面，词条也会出现在左侧。</p>
      </div>
      <div>
        <label class="field-label" for="keyword">搜索词</label>
        <input id="keyword" name="keyword" type="text" maxlength="40" required value="${esc(state.form.keyword)}" autocomplete="off">
      </div>
      <fieldset>
        <legend>标签</legend>
        <div class="chips">${chips}</div>
        <p class="help">同时勾选多个标签时，商品需要同时带有这些标记。</p>
      </fieldset>
      <fieldset>
        <legend>价格区间</legend>
        <div class="range">
          <input name="priceMin" type="number" min="0" step="0.01" inputmode="decimal" placeholder="最低价" aria-label="最低价" value="${esc(state.form.priceMin)}">
          <span>到</span>
          <input name="priceMax" type="number" min="0" step="0.01" inputmode="decimal" placeholder="最高价" aria-label="最高价" value="${esc(state.form.priceMax)}">
        </div>
      </fieldset>
      <div>
        <label class="field-label" for="banned">禁用词</label>
        <input id="banned" name="banned" type="text" value="${esc(state.form.banned)}" autocomplete="off">
        <p class="help">多个禁用词请用英文逗号 , 分隔。例如：二手,翻新</p>
      </div>
      <div class="scrape-limit">
        <label class="field-label" for="scrape-limit">本次抓取上限（选填）</label>
        <div class="limit-input"><input id="scrape-limit" name="limit" type="number" min="1" max="10000" step="1" inputmode="numeric" placeholder="不限" aria-describedby="limit-help" value="${esc(state.form.limit)}"></div>
        <p class="help" id="limit-help">留空将持续抓取，直到你停止或页面没有更多结果。也可填写 1–10000，达到符合筛选条件的条数后停止。结果会边抓取边保存。</p>
      </div>
      <p class="error" id="form-error" role="alert"></p>
      <button class="start" type="submit" ${state.job.running ? "disabled" : ""}>${state.job.running ? "正在抓取" : "开始"}</button>
    </form>`;
}

function resultPage(keyword) {
  const rows = state.goods[keyword] || [];
  const metadata = state.metadata[keyword] || {};
  const view = viewOf(keyword);
  const visibleRows = sortedAndRangedRows(keyword);
  const pageCount = Math.max(1, Math.ceil(visibleRows.length / view.pageSize));
  view.page = Math.min(Math.max(1, view.page), pageCount);
  const pageRows = visibleRows.slice((view.page - 1) * view.pageSize, view.page * view.pageSize);
  const running = state.job.running && state.job.keyword === keyword;
  const sortHeader = (field, label) => {
    const sort = view.sort;
    const direction = sort?.field === field ? sort.direction : null;
    const indicator = direction === "asc" ? "升序" : direction === "desc" ? "降序" : "未排序";
    return `<th aria-sort="${direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"}"><button class="table-sort" type="button" data-sort-keyword="${esc(keyword)}" data-sort-field="${field}">${label}<span aria-hidden="true" class="sort-mark ${direction || ""}"></span><span class="sr-only">${indicator}</span></button></th>`;
  };
  const body = pageRows.length ? pageRows.map((item) => `
    <tr data-open-goods="${esc(item.goods_id)}" data-keyword="${esc(keyword)}">
      <td class="seq">${esc(item.seq || "")}</td>
      <td>${item.image ? `<img class="thumb" src="${esc(item.image)}" alt="" loading="lazy" decoding="async">` : '<span class="ph"></span>'}</td>
      <td>
        <p class="name">${esc(item.title || "未命名商品")}</p>
        <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
      </td>
      <td class="shop">${esc(item.shop_name || "未知")}</td>
      <td class="price">${money(item.price)}</td>
      <td class="sales">${esc(item.sales || "—")}</td>
    </tr>
  `).join("") : `<tr><td colspan="6"><p class="empty">${running ? "正在抓取，结果会陆续出现在这里。" : rows.length ? "没有符合当前序号范围的商品。" : "还没有符合条件的商品。"}</p></td></tr>`;
  return `
    <div class="result-head">
      <div>
        <h1 class="page-title">${esc(entryOf(keyword)?.title || keyword)}</h1>
        <p class="conditions">${esc(describeConditions(keyword))}</p>
        <p class="storage-note">本次数据保存位置：<code>${esc(metadata.storage_path || `data/searches/${keyword}.jsonl`)}</code></p>
        <p class="lede result-status" role="status">${esc(state.job.keyword === keyword ? (state.job.message || `已保存 ${rows.length} 条`) : `已保存 ${rows.length} 条`)}</p>
        <div class="result-actions">
          <button class="ghost" type="button" data-delete-keyword="${esc(keyword)}" ${running ? 'disabled title="请先停止抓取"' : ''}>删除这个词条</button>
        </div>
      </div>
    </div>
    <section class="table-controls" aria-label="数据列表显示方式">
      <form class="list-tools" data-list-tools="${esc(keyword)}">
        <div class="control-group"><label for="page-size-${esc(keyword)}">每页显示</label><input id="page-size-${esc(keyword)}" name="pageSize" type="number" min="1" max="10000" step="1" inputmode="numeric" value="${esc(view.pageSize)}"><span>条</span></div>
        <div class="range-filter"><label for="start-id-${esc(keyword)}">序号范围</label><input id="start-id-${esc(keyword)}" name="startId" type="number" min="1" placeholder="起始 ID" value="${esc(view.startId)}"><span>至</span><input name="endId" type="number" min="1" placeholder="结束 ID" value="${esc(view.endId)}"></div>
        <button class="list-apply" type="submit">应用</button>
        <button class="text-btn" type="button" data-clear-range="${esc(keyword)}">清除</button>
      </form>
      <p class="table-count">显示 ${pageRows.length ? (view.page - 1) * view.pageSize + 1 : 0}–${Math.min(view.page * view.pageSize, visibleRows.length)} / ${visibleRows.length} 条${visibleRows.length !== rows.length ? `（共 ${rows.length} 条）` : ""}</p>
    </section>
    <div class="catalog-scroll" role="region" aria-label="商品列表，可横向滚动" tabindex="0"><table class="catalog">
      <thead><tr>${sortHeader("seq", "ID")}<th>图片</th>${sortHeader("title", "商品")}${sortHeader("shop_name", "店名")}${sortHeader("price", "价格")}${sortHeader("sales", "销量")}</tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <nav class="pagination" aria-label="商品列表翻页"><button class="ghost" type="button" data-page-step="prev" data-page-keyword="${esc(keyword)}" ${view.page === 1 ? "disabled" : ""}>上一页</button><form class="page-jump" data-page-form="${esc(keyword)}"><label for="page-number-${esc(keyword)}">第</label><input id="page-number-${esc(keyword)}" name="page" type="number" min="1" max="${pageCount}" step="1" inputmode="numeric" value="${view.page}"><span>/ ${pageCount} 页</span><button class="ghost" type="submit">跳转</button></form><button class="ghost" type="button" data-page-step="next" data-page-keyword="${esc(keyword)}" ${view.page === pageCount ? "disabled" : ""}>下一页</button></nav>`;
}

function detailPage(tab) {
  const item = (state.goods[tab.keyword] || []).find((row) => row.goods_id === tab.goodsId);
  if (!item) return `<p class="empty">这条商品不在当前结果里。</p>`;
  return `
    <article class="detail">
      ${item.image ? `<img src="${esc(item.image)}" alt="${esc(item.title || "")}">` : `<span class="ph"></span>`}
      <div>
        <h1 class="page-title">${esc(item.title || "未命名商品")}</h1>
        <p class="price">${money(item.price)}</p>
        <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
        <dl>
          <dt>销量</dt><dd>${esc(item.sales || "未知")}</dd>
          <dt>商品编号</dt><dd>${esc(item.goods_id)}</dd>
          <dt>ID</dt><dd>${esc(item.seq || "")}</dd>
          <dt>店名</dt><dd>${esc(item.shop_name || "未知")}</dd>
          <dt>词条</dt><dd>${esc(tab.keyword)}</dd>
        </dl>
        <p><a href="${esc(item.url)}" target="_blank" rel="noreferrer">在拼多多打开</a></p>
        <p><button class="ghost" type="button" data-delete-goods="${esc(item.goods_id)}" data-keyword="${esc(tab.keyword)}" ${state.job.running && state.job.keyword === tab.keyword ? 'disabled title="请先停止抓取"' : ''}>删除这条</button></p>
      </div>
    </article>`;
}

function topPage() {
  if (state.section === "home") {
    const auth = state.auth;
    return `<section class="login-page">
      <div class="login-intro"><h1 class="page-title">登录拼多多</h1><p class="lede">导入自己的登录信息后，即可按条件抓取公开搜索结果。登录信息只保存在本机，不会上传到其他服务器。</p></div>
      <form class="login-form" id="cookie-form">
        <label class="field-label" for="cookie-input">Cookie 登录信息</label>
        <div class="cookie-field"><input id="cookie-input" name="cookie" class="cookie-input" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 cookie，或点击“前往获取”后自动导入" value="${esc(auth.cookie || "")}"><button class="eye-btn" type="button" data-toggle-cookie aria-label="显示登录信息" aria-pressed="false">${EYE_ICON}</button></div>
        <p class="help">请仅使用自己的账号。Cookie 是登录凭证，请勿发送给他人。</p>
        <p class="auth-status ${auth.status === "failed" ? "error" : ""}" role="status">${esc(auth.message || (auth.configured ? "已设置登录信息。" : "尚未设置登录信息请点击确认以开始。"))}</p>
        <div class="login-actions"><button class="start" type="submit">确认</button><button class="ghost" type="button" data-clear-cookie>清除</button><button class="ghost" type="button" data-login-cookie ${auth.running ? "disabled" : ""}>${auth.running ? "等待登录…" : "前往获取"}</button></div>
      </form>
    </section>`;
  }
  if (state.section === "settings") {
    const theme = document.documentElement.dataset.theme || "day";
    const option = (value, label) => `<label><input type="radio" name="theme" value="${value}" ${theme === value ? "checked" : ""}>${label}</label>`;
    return `<h1 class="page-title">系统设置</h1><form class="theme-list" id="theme-form">${option("day", "白天")}${option("ink", "墨色")}${option("warm", "暖纸")}</form>`;
  }
  if (state.section === "about") {
    return `<h1 class="page-title">关于系统</h1><p class="lede">系统版本：测试版0.0.1</p>`;
  }
  return `<h1 class="page-title">${esc(TOP.find((item) => item.id === state.section)?.label || "")}</h1><p class="empty">这个页面还没做。请先使用功能区里的搜索词条爬取。</p>`;
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
    else stage.innerHTML = `<h1 class="page-title">功能区概览</h1>`;
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

function renderCaptureBar() {
  const bar = $("capture-bar");
  bar.hidden = !state.job.running;
  if (capturePanel.isOpen && (!state.job.running || state.job.id !== capturePanel.jobId)) capturePanel.hide(false);
  if (!state.job.running) return;
  if (!bar.firstElementChild) bar.innerHTML = `<span class="capture-message" role="status"></span><div class="capture-actions"><button type="button" class="ghost" data-capture-results>查看结果</button><button type="button" class="ghost" data-capture-browser="open" hidden>查看抓取页面</button><button type="button" class="ghost" data-capture-browser="resume" hidden>继续抓取</button><button type="button" class="stop" data-stop-search>停止抓取</button></div>`;
  const message = `${state.job.keyword}：${state.job.message || "正在抓取"}`;
  if (bar.firstElementChild.textContent !== message) bar.firstElementChild.textContent = message;
  const stop = bar.querySelector("[data-stop-search]");
  stop.disabled = state.job.status === "stopping";
  stop.textContent = stop.disabled ? "正在停止…" : "停止抓取";
  const panelStop = $("capture-dialog").querySelector("[data-stop-search]");
  panelStop.disabled = stop.disabled;
  panelStop.textContent = stop.textContent;
  const open = bar.querySelector('[data-capture-browser="open"]');
  const resume = bar.querySelector('[data-capture-browser="resume"]');
  const needsVerification = state.job.browser_available && state.job.status === "verification" && state.job.intervention_kind === "verification";
  open.hidden = !needsVerification;
  open.disabled = state.job.status === "stopping";
  open.textContent = state.job.status === "verification" && state.job.intervention_kind !== "loading" ? "前往验证" : "查看抓取页面";
  resume.hidden = !needsVerification;
}

function updateResultPage(keyword) {
  // 不重建筛选表单；刷新表格时保留滚动位置、焦点和未提交的输入。
  const stage = $("stage");
  const template = document.createElement("template");
  template.innerHTML = resultPage(keyword);
  const scrollTop = stage.scrollTop;
  for (const selector of [".result-status", ".result-actions", ".table-count", ".catalog tbody"]) {
    const current = stage.querySelector(selector);
    const next = template.content.querySelector(selector);
    if (current && next && current.innerHTML !== next.innerHTML) {
      // 正在选择或查看的行维持位置；新商品仍已进入本地数据，翻页即可查看。
      if (selector === ".catalog tbody" && (current.contains(document.activeElement) || window.getSelection()?.toString())) continue;
      current.innerHTML = next.innerHTML;
    }
  }
  const pagination = stage.querySelector(".pagination");
  const nextPagination = template.content.querySelector(".pagination");
  if (pagination && !pagination.contains(document.activeElement) && pagination.innerHTML !== nextPagination.innerHTML) pagination.innerHTML = nextPagination.innerHTML;
  stage.scrollTop = scrollTop;
}

async function loadCookie() {
  const data = await api("/api/cookie");
  state.auth = { ...state.auth, ...(data.auth || {}) };
}

async function openEntryMenu(anchor, keyword) {
  const entry = entryOf(keyword);
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = `<button type="button" data-act="rename">${ICONS.rename}重命名</button><button type="button" data-act="pin">${ICONS.pin}${entry?.pinned ? "取消置顶" : "置顶"}</button><button type="button" data-act="group">${ICONS.group}分组</button><button type="button" data-act="delete">${ICONS.trash}删除</button>`;
  menu.style.left = `${Math.max(8, rect.left - 80)}px`;
  menu.style.top = `${rect.bottom + 4}px`;
  document.body.appendChild(menu);
  const close = () => menu.remove();
  menu.onclick = async (event) => {
    const act = event.target.closest("button")?.dataset.act;
    close();
    if (act === "rename") {
      const title = await askModal({ title: "重命名", text: "这里改的是显示标题，搜索词不会变。", confirmLabel: "保存标题", field: "标题" });
      if (!title) return;
      const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "rename", keyword, title }) });
      state.library = data.library;
    }
    if (act === "pin") {
      const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin", keyword }) });
      state.library = data.library;
    }
    if (act === "group") await moveKeywords([keyword]);
    if (act === "delete") {
      const ok = await askModal({ title: "删除词条", text: `删除「${entry?.title || keyword}」及其已保存商品？删除后不能恢复。`, confirmLabel: "删除词条" });
      if (ok) await removeKeywords([keyword]);
      return;
    }
    render();
  };
  setTimeout(() => document.addEventListener("click", close, { once: true }), 0);
}

async function openGroupMenu(anchor, groupId) {
  const group = state.library.groups.find((item) => item.id === groupId);
  if (!group) return;
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = `<button type="button" data-group-act="rename">${ICONS.rename}重命名</button><button type="button" data-group-act="pin">${ICONS.pin}${group.pinned ? "取消置顶" : "置顶"}</button><button type="button" data-group-act="delete">${ICONS.trash}删除分组</button>`;
  menu.style.left = `${Math.max(8, rect.left - 112)}px`;
  menu.style.top = `${rect.bottom + 4}px`;
  document.body.appendChild(menu);
  const close = () => menu.remove();
  menu.onclick = async (event) => {
    const act = event.target.closest("button")?.dataset.groupAct;
    close();
    try {
      if (act === "rename") {
        const name = await askModal({ title: "重命名分组", text: "词条和已保存的数据不会受影响。", confirmLabel: "保存名称", field: "分组名称" });
        if (!name) return;
        const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "rename-group", groupId, name }) });
        state.library = data.library;
      }
      if (act === "pin") {
        const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin-group", groupId }) });
        state.library = data.library;
      }
      if (act === "delete") {
        const ok = await askModal({ title: "删除分组", text: `删除「${group.name}」分组？其中的词条和已保存数据会保留，并回到未分组列表。`, confirmLabel: "删除分组" });
        if (!ok) return;
        const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "delete-group", groupId }) });
        state.library = data.library;
      }
      render();
    } catch (exc) {
      render();
    }
  };
  setTimeout(() => document.addEventListener("click", close, { once: true }), 0);
}

async function moveKeywords(keywords) {
  const name = await askModal({ title: "归入分组", text: "填写分组名称。没有这个分组时会新建。", confirmLabel: "归入分组", field: "分组名称" });
  if (!name) return;
  let group = state.library.groups.find((item) => item.name === name);
  if (!group) {
    const created = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "create-group", name }) });
    state.library = created.library;
    group = state.library.groups.find((item) => item.name === name);
  }
  const moved = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "move", keywords, groupId: group.id }) });
  state.library = moved.library;
  state.multi = false;
  state.selected = [];
  render();
}

async function removeKeywords(keywords) {
  const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "delete", keywords }) });
  state.library = data.library;
  state.keywords = state.keywords.filter((item) => !keywords.includes(item.keyword));
  keywords.forEach((keyword) => closeTab(`result:${keyword}`));
  state.selected = [];
  state.multi = false;
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
  if (start) { start.disabled = state.job.running; start.textContent = start.disabled ? "正在抓取" : "开始"; }
  } finally { refreshing = false; }
}

document.body.addEventListener("click", async (event) => {
  const browserAction = event.target.closest("[data-capture-browser]");
  if (browserAction) {
    browserAction.disabled = true;
    try {
      const action = browserAction.dataset.captureBrowser;
      const jobId = state.job.id;
      await api("/api/search/browser", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Capture-View": "1" },
        body: JSON.stringify({ action, job_id: jobId }),
      });
      if (action === "open" && state.job.id === jobId) capturePanel.show(jobId);
      if (action === "resume") capturePanel.hide(false);
    } catch (exc) {
      state.job.message = exc.message;
    } finally {
      browserAction.disabled = false;
      renderCaptureBar();
    }
    return;
  }
  if (event.target.closest("[data-capture-results]")) {
    const keyword = state.job.keyword;
    await loadGoods(keyword);
    openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
    return;
  }
  const section = event.target.closest("[data-section]");
  if (section) {
    state.section = section.dataset.section;
    if (state.section === "tools") {
      state.toolLabel = "功能区概览";
      state.active = null;
      render();
    } else {
      state.active = null;
      state.tabs = state.tabs.filter((item) => item.kind === "search" || item.kind === "goods");
      render();
    }
    $("sidebar").classList.remove("open");
    return;
  }
  if (event.target.closest("[data-open-form]")) {
    state.toolLabel = "搜索词条爬取";
    state.active = null;
    state.section = "tools";
    render();
    return;
  }
  if (event.target.closest("[data-toggle-cookie]")) {
    const input = $("cookie-input");
    if (!input) return;
    const wasHidden = input.type === "password";
    input.type = wasHidden ? "text" : "password";
    const button = event.target.closest("[data-toggle-cookie]");
    button.setAttribute("aria-pressed", String(wasHidden));
    button.setAttribute("aria-label", wasHidden ? "隐藏登录信息" : "显示登录信息");
    return;
  }
  if (event.target.closest("[data-clear-cookie]")) {
    try {
      const data = await api("/api/cookie", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "clear" }) });
      state.auth = data.auth || state.auth;
    } catch (exc) {
      state.auth = { ...state.auth, status: "failed", message: exc.message };
    }
    render();
    return;
  }
  if (event.target.closest("[data-login-cookie]")) {
    try {
      const data = await api("/api/cookie", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "login" }) });
      state.auth = { ...state.auth, ...(data.auth || {}) };
    } catch (exc) {
      state.auth = { ...state.auth, status: "failed", message: exc.message };
    }
    render();
    return;
  }
  if (event.target.closest("[data-stop-search]")) {
    const button = event.target.closest("[data-stop-search]");
    button.disabled = true;
    try {
      await api("/api/search/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      state.job.status = "stopping";
      state.job.message = "正在停止，已保存的商品会保留。";
    } catch (exc) { state.job.message = exc.message; button.disabled = false; }
    renderCaptureBar();
    return;
  }
  const sortButton = event.target.closest("[data-sort-field]");
  if (sortButton) {
    const keyword = sortButton.dataset.sortKeyword;
    const view = viewOf(keyword);
    const field = sortButton.dataset.sortField;
    if (view.sort?.field !== field) view.sort = { field, direction: "asc" };
    else if (view.sort.direction === "asc") view.sort = { field, direction: "desc" };
    else view.sort = null;
    view.page = 1;
    renderStage();
    return;
  }
  const pageStep = event.target.closest("[data-page-step]");
  if (pageStep) {
    const view = viewOf(pageStep.dataset.pageKeyword);
    view.page += pageStep.dataset.pageStep === "next" ? 1 : -1;
    renderStage();
    return;
  }
  const clearRange = event.target.closest("[data-clear-range]");
  if (clearRange) {
    const view = viewOf(clearRange.dataset.clearRange);
    view.startId = "";
    view.endId = "";
    view.page = 1;
    renderStage();
    return;
  }
  if (event.target.closest("[data-fold-keywords]")) {
    state.keywordsOpen = !state.keywordsOpen;
    renderNav();
    return;
  }
  if (event.target.closest("[data-exit-multi]")) { state.multi = false; state.selected = []; renderNav(); return; }
  const select = event.target.closest("[data-select]");
  if (select) {
    state.multi = true;
    const keyword = select.dataset.select;
    state.selected = state.selected.includes(keyword) ? state.selected.filter((item) => item !== keyword) : [...state.selected, keyword];
    renderNav();
    return;
  }
  const more = event.target.closest("[data-more]");
  if (more) { openEntryMenu(more, more.dataset.more); return; }
  const groupMore = event.target.closest("[data-group-more]");
  if (groupMore) { openGroupMenu(groupMore, groupMore.dataset.groupMore); return; }
  if (event.target.closest("[data-new-group]")) {
    const name = await askModal({ title: "新建分组", text: "分组用来整理相关词条。", confirmLabel: "创建分组", field: "分组名称" });
    if (!name) return;
    const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "create-group", name }) });
    state.library = data.library;
    render();
    return;
  }
  if (event.target.closest("[data-delete-selected]")) {
    if (!state.selected.length) return;
    const ok = await askModal({ title: "删除所选词条", text: `将删除 ${state.selected.length} 个词条及其已保存商品。删除后不能恢复。`, confirmLabel: "删除所选" });
    if (ok) await removeKeywords(state.selected);
    return;
  }
  if (event.target.closest("[data-group-selected]")) {
    if (!state.selected.length) return;
    await moveKeywords(state.selected);
    return;
  }
  const toggleGroup = event.target.closest("[data-toggle-group]");
  if (toggleGroup) {
    const data = await api("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "toggle-group", groupId: toggleGroup.dataset.toggleGroup }) });
    state.library = data.library;
    render();
    return;
  }
  const keywordLink = event.target.closest("[data-keyword]");
  if (keywordLink && !event.target.closest("[data-more], [data-select], [data-open-goods], [data-delete-goods]")) {
    event.preventDefault();
    const keyword = keywordLink.dataset.keyword;
    await loadGoods(keyword);
    openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
    return;
  }
  if (event.target.closest("[data-fixed]")) {
    state.active = null;
    render();
    return;
  }
  const tab = event.target.closest("[data-tab]");
  if (tab && !event.target.closest("[data-close]")) {
    state.active = tab.dataset.tab;
    const opened = state.tabs.find((item) => item.id === state.active);
    if (opened && opened.kind === "search") await loadGoods(opened.keyword);
    render();
    return;
  }
  const close = event.target.closest("[data-close]");
  if (close) {
    closeTab(close.dataset.close);
    return;
  }
  const deleteKeyword = event.target.closest("[data-delete-keyword]");
  if (deleteKeyword) {
    const keyword = deleteKeyword.dataset.deleteKeyword;
    const ok = await askModal({ title: "删除词条", text: `删除「${keyword}」及其已保存商品？删除后不能恢复。`, confirmLabel: "删除词条" });
    if (!ok) return;
    const removed = await api(`/api/results?keyword=${encodeURIComponent(keyword)}`, { method: "DELETE" });
    delete state.goods[keyword];
    delete state.revisions[keyword];
    delete state.metadata[keyword];
    delete state.views[keyword];
    state.keywords = state.keywords.filter((item) => item.keyword !== keyword);
    if (removed.library) state.library = removed.library;
    closeTab(`result:${keyword}`);
    return;
  }
  const deleteGoods = event.target.closest("[data-delete-goods]");
  if (deleteGoods) {
    const keyword = deleteGoods.dataset.keyword;
    const goodsId = deleteGoods.dataset.deleteGoods;
    const ok = await askModal({ title: "删除这条商品", text: "只删除这一条已保存商品。删除后不能恢复。", confirmLabel: "删除这条" });
    if (!ok) return;
    await api(`/api/results?keyword=${encodeURIComponent(keyword)}&goods_id=${encodeURIComponent(goodsId)}`, { method: "DELETE" });
    state.goods[keyword] = (state.goods[keyword] || []).filter((item) => item.goods_id !== goodsId);
    closeTab(`goods:${keyword}:${goodsId}`);
    return;
  }
  const row = event.target.closest("[data-open-goods]");
  if (row) {
    const keyword = row.dataset.keyword;
    const goodsId = row.dataset.openGoods;
    const item = (state.goods[keyword] || []).find((goods) => goods.goods_id === goodsId);
    openTab({
      id: `goods:${keyword}:${goodsId}`,
      title: item?.title || "商品详情",
      kind: "goods",
      keyword,
      goodsId,
    });
  }
});

document.body.addEventListener("submit", async (event) => {
  if (event.target.id === "cookie-form") {
    event.preventDefault();
    const cookie = String(new FormData(event.target).get("cookie") || "").trim();
    try {
      const data = await api("/api/cookie", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "save", cookie }) });
      state.auth = data.auth || state.auth;
    } catch (exc) {
      state.auth = { ...state.auth, status: "failed", message: exc.message };
    }
    render();
    return;
  }
  const listTools = event.target.closest("[data-list-tools]");
  if (listTools) {
    event.preventDefault();
    applyListTools(listTools);
    return;
  }
  const pageForm = event.target.closest("[data-page-form]");
  if (pageForm) {
    event.preventDefault();
    const keyword = pageForm.dataset.pageForm;
    const view = viewOf(keyword);
    const pageCount = Math.max(1, Math.ceil(sortedAndRangedRows(keyword).length / view.pageSize));
    const page = Number(new FormData(pageForm).get("page"));
    if (!Number.isInteger(page) || page < 1 || page > pageCount) return;
    view.page = page;
    renderStage();
    return;
  }

  if (event.target.id !== "search-form") return;
  event.preventDefault();
  const form = new FormData(event.target);
  const keyword = String(form.get("keyword") || "").trim();
  const priceMin = String(form.get("priceMin") || "").trim();
  const priceMax = String(form.get("priceMax") || "").trim();
  const banned = String(form.get("banned") || "").trim();
  const limit = String(form.get("limit") || "").trim();
  const tags = form.getAll("tags");
  const error = document.getElementById("form-error");
  state.form = { keyword, priceMin, priceMax, banned, tags, limit };
  if (!keyword) {
    error.textContent = "请填写搜索词。";
    return;
  }
  if (priceMin && priceMax && Number(priceMin) > Number(priceMax)) {
    error.textContent = "最低价不能高于最高价。";
    return;
  }
  if (banned && !banned.includes(",") && banned.includes("，")) {
    error.textContent = "请使用英文逗号 , 分隔禁用词。";
    return;
  }
  if (limit && (!/^[1-9]\d*$/.test(limit) || Number(limit) > 10000)) {
    error.textContent = "抓取上限请留空，或填写 1 到 10000 之间的整数。";
    return;
  }
  error.textContent = "";
  try {
    await api("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword, limit, tags, banned, price_min: priceMin, price_max: priceMax }),
    });
    state.job = { running: true, keyword, status: "running", message: "正在打开搜索页", count: 0 };
    if (!state.keywords.some((item) => item.keyword === keyword)) state.keywords.push({ keyword, count: 0 });
    openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
  } catch (exc) {
    error.textContent = exc.message;
  }
});

document.body.addEventListener("change", (event) => {
  if (event.target.name === "theme") {
    document.documentElement.dataset.theme = event.target.value;
    localStorage.setItem("pdd-theme", event.target.value);
    return;
  }
});

$("nav-toggle").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("open");
  $("nav-toggle").setAttribute("aria-expanded", String(open));
});

let copyTarget = null;

function hideCopyMenu() {
  $("context-menu").hidden = true;
}

function selectedText(target) {
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
    return target.value.slice(target.selectionStart ?? 0, target.selectionEnd ?? 0);
  }
  return window.getSelection()?.toString() || "";
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const box = document.createElement("textarea");
    box.value = text;
    document.body.appendChild(box);
    box.select();
    document.execCommand("copy");
    box.remove();
  }
}

async function readClipboard() {
  try {
    return await navigator.clipboard.readText();
  } catch {
    return "";
  }
}

function pasteInto(target, text) {
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? start;
  target.value = target.value.slice(0, start) + text + target.value.slice(end);
  const next = start + text.length;
  target.selectionStart = target.selectionEnd = next;
  target.dispatchEvent(new Event("input", { bubbles: true }));
  target.focus();
}

document.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  const field = event.target.closest("input, textarea");
  copyTarget = field || event.target;
  const menu = $("context-menu");
  const copy = `<button type="button" data-copy-act="copy" role="menuitem">复制<kbd>Ctrl+C</kbd></button>`;
  const paste = field ? `<button type="button" data-copy-act="paste" role="menuitem">粘贴<kbd>Ctrl+V</kbd></button>` : "";
  menu.innerHTML = copy + paste;
  menu.hidden = false;
  menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - 210))}px`;
  menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - (field ? 90 : 52)))}px`;
});

$("context-menu").addEventListener("click", async (event) => {
  const act = event.target.closest("button")?.dataset.copyAct;
  if (act === "copy") {
    const text = selectedText(copyTarget) || copyTarget?.innerText || "";
    if (text.trim()) await writeClipboard(text);
  }
  if (act === "paste" && copyTarget) {
    const text = await readClipboard();
    if (text) pasteInto(copyTarget, text);
  }
  hideCopyMenu();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest("#context-menu")) hideCopyMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideCopyMenu();
});

function moveTab(from, to) {
  if (!from || !to || from === to) return;
  const moving = state.tabs.find((item) => item.id === from);
  const target = state.tabs.find((item) => item.id === to);
  if (!moving || !target || moving.kind !== target.kind) return;
  if (moving.kind === "goods" && moving.keyword !== target.keyword) return;
  const next = state.tabs.filter((item) => item.id !== from);
  next.splice(next.findIndex((item) => item.id === to), 0, moving);
  state.tabs = next;
  renderTabs();
}

function bindTabDrag(row) {
  row.addEventListener("dragstart", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (!tab || event.target.closest("[data-close]")) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData("text/plain", tab.dataset.tab);
    event.dataTransfer.effectAllowed = "move";
  });
  row.addEventListener("dragover", (event) => {
    if (event.target.closest("[data-tab]")) event.preventDefault();
  });
  row.addEventListener("drop", (event) => {
    event.preventDefault();
    moveTab(event.dataTransfer.getData("text/plain"), event.target.closest("[data-tab]")?.dataset.tab);
  });
}

bindTabDrag($("tabs-main"));
bindTabDrag($("tabs-detail"));

document.documentElement.dataset.theme = localStorage.getItem("pdd-theme") || "day";
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
