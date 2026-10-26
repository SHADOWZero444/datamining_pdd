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
  section: "tools",
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
  form: { keyword: "", priceMin: "", priceMax: "", banned: "", tags: [] },
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
  const groups = state.library.groups.map((group) => `
    <div>
      <button class="group-toggle" type="button" data-toggle-group="${esc(group.id)}"><span class="nav-label">${group.open === false ? "展开" : "收起"} ${esc(group.name)}</span></button>
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
      <p class="error" id="form-error" role="alert"></p>
      <button class="start" type="submit" ${state.job.running ? "disabled" : ""}>${state.job.running ? "正在抓取" : "开始"}</button>
    </form>`;
}

function resultPage(keyword) {
  const rows = state.goods[keyword] || [];
  const running = state.job.running && state.job.keyword === keyword;
  const body = rows.length ? rows.map((item) => `
    <tr data-open-goods="${esc(item.goods_id)}" data-keyword="${esc(keyword)}">
      <td class="seq">${esc(item.seq || "")}</td>
      <td>${item.image ? `<img class="thumb" src="${esc(item.image)}" alt="">` : '<span class="ph"></span>'}</td>
      <td>
        <p class="name">${esc(item.title || "未命名商品")}</p>
        <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
      </td>
      <td class="shop">${esc(item.shop_name || "未知")}</td>
      <td class="price">${money(item.price)}</td>
      <td class="sales">${esc(item.sales || "—")}</td>
    </tr>
  `).join("") : `<tr><td colspan="6"><p class="empty">${running ? "正在抓取，结果会陆续出现在这里。" : "还没有符合条件的商品。"}</p></td></tr>`;
  return `
    <div class="result-head">
      <div>
        <h1 class="page-title">${esc(entryOf(keyword)?.title || keyword)}</h1>
        <p class="lede">搜索词条：${esc(keyword)}</p>
        <p class="lede">${esc(state.job.keyword === keyword ? (state.job.message || `已保存 ${rows.length} 条`) : `已保存 ${rows.length} 条`)}</p>
        <button class="ghost" type="button" data-delete-keyword="${esc(keyword)}">删除这个词条</button>
      </div>
    </div>
    <table class="catalog">
      <thead><tr><th>ID</th><th>图片</th><th>商品</th><th>店名</th><th>价格</th><th>销量</th></tr></thead>
      <tbody>${body}</tbody>
    </table>`;
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
        <p><button class="ghost" type="button" data-delete-goods="${esc(item.goods_id)}" data-keyword="${esc(tab.keyword)}">删除这条</button></p>
      </div>
    </article>`;
}

function topPage() {
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

function renderStage() {
  if (state.section !== "tools") {
    $("stage").innerHTML = topPage();
    return;
  }
  const tab = state.tabs.find((item) => item.id === state.active);
  if (tab && tab.kind === "goods") $("stage").innerHTML = detailPage(tab);
  else if (tab && tab.kind === "search") $("stage").innerHTML = resultPage(tab.keyword);
  else if (state.toolLabel === "搜索词条爬取") $("stage").innerHTML = searchForm();
  else $("stage").innerHTML = `<h1 class="page-title">功能区概览</h1>`;
}

function render() {
  renderNav();
  renderTabs();
  renderStage();
}

async function loadGoods(keyword) {
  const data = await api(`/api/results?keyword=${encodeURIComponent(keyword)}`);
  state.goods[keyword] = data.goods || [];
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

async function refresh() {
  const data = await api("/api/state");
  const jobChanged = JSON.stringify(data.job) !== JSON.stringify(state.job);
  const keywordsChanged = JSON.stringify(data.keywords) !== JSON.stringify(state.keywords);
  state.keywords = data.keywords || [];
  if (data.library) state.library = data.library;
  state.library = data.library || { groups: [], entries: (data.keywords || []).map((item) => ({ ...item, title: item.keyword, pinned: false, groupId: "" })) };
  state.job = data.job || state.job;
  const tab = state.tabs.find((item) => item.id === state.active);
  let goodsChanged = false;
  if (tab && (tab.kind === "search" || tab.kind === "goods")) {
    const before = JSON.stringify(state.goods[tab.keyword] || []);
    await loadGoods(tab.keyword);
    goodsChanged = before !== JSON.stringify(state.goods[tab.keyword] || []);
  }
  const typing = ["keyword", "banned", "priceMin", "priceMax"].includes(document.activeElement?.name) || document.activeElement?.id === "keyword";
  if (typing && !jobChanged && !goodsChanged) {
    if (keywordsChanged) renderNav();
    return;
  }
  if (jobChanged || keywordsChanged || goodsChanged) render();
}

document.body.addEventListener("click", async (event) => {
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
    await api(`/api/results?keyword=${encodeURIComponent(keyword)}`, { method: "DELETE" });
    delete state.goods[keyword];
    state.keywords = state.keywords.filter((item) => item.keyword !== keyword);
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
  if (event.target.id !== "search-form") return;
  event.preventDefault();
  const form = new FormData(event.target);
  const keyword = String(form.get("keyword") || "").trim();
  const priceMin = String(form.get("priceMin") || "").trim();
  const priceMax = String(form.get("priceMax") || "").trim();
  const banned = String(form.get("banned") || "").trim();
  const tags = form.getAll("tags");
  const error = document.getElementById("form-error");
  state.form = { keyword, priceMin, priceMax, banned, tags };
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
  error.textContent = "";
  try {
    await api("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword, limit: 300, tags, banned, price_min: priceMin, price_max: priceMax }),
    });
    if (!state.keywords.some((item) => item.keyword === keyword)) state.keywords.push({ keyword, count: 0 });
    openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
  } catch (exc) {
    error.textContent = exc.message;
  }
});

document.body.addEventListener("change", (event) => {
  if (event.target.name !== "theme") return;
  document.documentElement.dataset.theme = event.target.value;
  localStorage.setItem("pdd-theme", event.target.value);
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
refresh().catch((exc) => {
  $("stage").innerHTML = `<p class="empty">${esc(exc.message)}</p>`;
});
setInterval(() => refresh().catch(() => {}), 2000);
