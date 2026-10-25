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
  keywords: [],
  job: { running: false, status: "idle", message: "", keyword: "" },
  tabs: [{ id: "search-form", title: "搜索词条爬取", kind: "form" }],
  active: "search-form",
  keywordsOpen: true,
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
  state.tabs = state.tabs.filter((item) => item.id !== id);
  if (state.active === id) state.active = state.tabs.at(-1)?.id || null;
  render();
}

function renderNav() {
  $("bookmark").textContent = TOP.find((item) => item.id === state.section)?.label || "功能区";
  const bookmarks = TOP.map((item) => `
    <button class="nav-btn" type="button" data-section="${item.id}" ${state.section === item.id ? 'aria-current="page"' : ""}>
      ${icon(item.icon)}${item.label}
    </button>
  `).join("");
  const keywords = state.keywords.map((item) => `
    <a class="keyword-link" href="#result:${encodeURIComponent(item.keyword)}" data-keyword="${esc(item.keyword)}" ${state.active === `result:${item.keyword}` ? 'aria-current="page"' : ""}>
      ${esc(item.keyword)}<span class="count">${item.count}</span>
    </a>
  `).join("");
  const tools = state.section === "tools" ? `
    <div class="sub-head">
      <button class="sub-btn" type="button" data-open-form ${state.active === "search-form" ? 'aria-current="page"' : ""}>搜索词条爬取</button>
      <button class="fold" type="button" data-fold-keywords aria-expanded="${state.keywordsOpen}" aria-label="展开或收起词条">${state.keywordsOpen ? "−" : "+"}</button>
    </div>
    <div class="sub-list" ${state.keywordsOpen ? "" : "hidden"}>${keywords || '<p class="help">抓取完成后，词条会出现在这里。</p>'}</div>
  ` : "";
  $("nav").innerHTML = `<div class="nav-group">${bookmarks}</div>${tools}`;
}

function renderTabs() {
  $("tabs").innerHTML = state.tabs.map((tab) => `
    <div class="tab" role="tab" aria-selected="${tab.id === state.active}" data-tab="${esc(tab.id)}">
      <span>${esc(tab.title)}</span>
      <button class="tab-close" type="button" data-close="${esc(tab.id)}" aria-label="关闭${esc(tab.title)}">×</button>
    </div>
  `).join("");
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
      <td>${item.image ? `<img class="thumb" src="${esc(item.image)}" alt="">` : '<span class="ph"></span>'}</td>
      <td>
        <p class="name">${esc(item.title || "未命名商品")}</p>
        <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
      </td>
      <td class="price">${money(item.price)}</td>
      <td class="sales">${esc(item.sales || "—")}</td>
    </tr>
  `).join("") : `<tr><td colspan="4"><p class="empty">${running ? "正在抓取，结果会陆续出现在这里。" : "还没有符合条件的商品。"}</p></td></tr>`;
  return `
    <div class="result-head">
      <div>
        <h1 class="page-title">${esc(keyword)}</h1>
        <p class="lede">${esc(state.job.keyword === keyword ? (state.job.message || `已保存 ${rows.length} 条`) : `已保存 ${rows.length} 条`)}</p>
        <button class="ghost" type="button" data-delete-keyword="${esc(keyword)}">删除这个词条</button>
      </div>
    </div>
    <table class="catalog">
      <thead><tr><th>图片</th><th>商品</th><th>价格</th><th>销量</th></tr></thead>
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
          <dt>店铺编号</dt><dd>${esc(item.mall_id || "未知")}</dd>
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
  const tab = state.tabs.find((item) => item.id === state.active);
  if (!tab) {
    $("stage").innerHTML = topPage();
    return;
  }
  if (tab.kind === "form") $("stage").innerHTML = searchForm();
  else if (tab.kind === "search") $("stage").innerHTML = resultPage(tab.keyword);
  else if (tab.kind === "goods") $("stage").innerHTML = detailPage(tab);
  else $("stage").innerHTML = topPage();
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

async function refresh() {
  const data = await api("/api/state");
  const jobChanged = JSON.stringify(data.job) !== JSON.stringify(state.job);
  const keywordsChanged = JSON.stringify(data.keywords) !== JSON.stringify(state.keywords);
  state.keywords = data.keywords || [];
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
      openTab({ id: "search-form", title: "搜索词条爬取", kind: "form" });
    } else {
      state.active = null;
      state.tabs = state.tabs.filter((item) => item.kind === "search" || item.kind === "goods");
      render();
    }
    $("sidebar").classList.remove("open");
    return;
  }
  if (event.target.closest("[data-open-form]")) {
    openTab({ id: "search-form", title: "搜索词条爬取", kind: "form" });
    return;
  }
  if (event.target.closest("[data-fold-keywords]")) {
    state.keywordsOpen = !state.keywordsOpen;
    renderNav();
    return;
  }
  const keywordLink = event.target.closest("[data-keyword]");
  if (keywordLink && keywordLink.matches("a")) {
    event.preventDefault();
    const keyword = keywordLink.dataset.keyword;
    await loadGoods(keyword);
    openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
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
    if (!confirm(`删除「${keyword}」的全部已保存商品？删除后不能恢复。`)) return;
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
    if (!confirm("删除这条已保存商品？")) return;
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

document.documentElement.dataset.theme = localStorage.getItem("pdd-theme") || "day";
render();
refresh().catch((exc) => {
  $("stage").innerHTML = `<p class="empty">${esc(exc.message)}</p>`;
});
setInterval(() => refresh().catch(() => {}), 2000);
