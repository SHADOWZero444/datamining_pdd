// 功能依赖由入口显式注入；内部函数共享本功能状态。
function createSearchFeature({ state, preferences, TAGS, badgeText, esc, money, numericValue, safeWebUrl, entryOf, renderStage }) {
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
      state.views[keyword] = { page: 1, pageSize: preferences.pageSize, startId: "", endId: "", sort: null };
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
    return `搜索词：${metadata.keyword || keyword}　标签：${tags}　价格区间：${price}　关键词：${filters.required_words || "无"}　禁用词：${filters.banned || "无"}`;
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
      if (field === "updated") return factor * (Number(a.updated === true) - Number(b.updated === true));
      const aValue = field === "title" || field === "shop_name" ? String(a[field] || "") : numericValue(a[field]);
      const bValue = field === "title" || field === "shop_name" ? String(b[field] || "") : numericValue(b[field]);
      if (typeof aValue === "string") return factor * aValue.localeCompare(bValue, "zh-CN");
      return factor * (aValue - bValue);
    });
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
          <label class="field-label" for="required-words">关键词</label>
          <input id="required-words" name="requiredWords" type="text" value="${esc(state.form.requiredWords)}" autocomplete="off" aria-describedby="required-words-help">
          <p class="help" id="required-words-help">仅保留商品标题包含全部关键词的结果，留空不限。多个词用逗号分隔，英文不区分大小写。</p>
        </div>
        <div>
          <label class="field-label" for="banned">禁用词</label>
          <input id="banned" name="banned" type="text" value="${esc(state.form.banned)}" autocomplete="off">
          <p class="help">多个禁用词请用英文逗号 , 分隔。例如：二手,翻新</p>
        </div>
        <div class="scrape-limit">
          <label class="field-label" for="scrape-limit">累计商品目标</label>
          <div class="limit-input"><input id="scrape-limit" name="limit" type="number" min="1" max="10000" step="1" inputmode="numeric" placeholder="不限" aria-describedby="limit-help" value="${esc(state.form.limit)}"></div>
          
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
    const pending = state.pendingRefresh === keyword;
    const busy = Boolean(state.pendingRefresh || state.job.running || state.auth.running);
    let status = `已保存 ${rows.length} 条`;
    if (state.job.keyword === keyword && state.job.message) status = state.job.message;
    if (!running && busy) status = state.auth.running ? "登录完成后即可更新。" : "当前抓取结束后即可更新。";
    if (!running && state.refreshErrors[keyword]) status = state.refreshErrors[keyword];
    if (pending) status = "正在启动更新…";
    const sortHeader = (field, label, hint = "") => {
      const sort = view.sort;
      const direction = sort?.field === field ? sort.direction : null;
      const indicator = direction === "asc" ? "升序" : direction === "desc" ? "降序" : "未排序";
      return `<th scope="col"${hint ? ` title="${esc(hint)}"` : ""} aria-sort="${direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"}"><button class="table-sort" type="button" data-sort-keyword="${esc(keyword)}" data-sort-field="${field}">${label}<span aria-hidden="true" class="sort-mark ${direction || ""}"></span><span class="sr-only">${indicator}</span></button></th>`;
    };
    const body = pageRows.length ? pageRows.map((item) => `
      <tr data-open-goods="${esc(item.goods_id)}" data-keyword="${esc(keyword)}">
        <td class="seq">${esc(item.seq || "")}</td>
        <td>${preferences.images && item.image ? `<img class="thumb" src="${esc(safeWebUrl(item.image))}" alt="" loading="lazy" decoding="async">` : '<span class="ph"></span>'}</td>
        <td>
          <p class="name">${esc(item.title || "未命名商品")}</p>
          <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
        </td>
        <td class="shop">${esc(item.shop_name || "未知")}</td>
        <td class="price">${esc(money(item.price))}</td>
        <td class="sales">${esc(item.sales || "—")}</td>
        <td class="update-flag${item.updated === true ? " changed" : ""}">${item.updated === true ? "是" : "否"}</td>
      </tr>
    `).join("") : `<tr><td colspan="7"><p class="empty">${running ? "正在抓取，结果会陆续出现在这里。" : rows.length ? "没有符合当前序号范围的商品。" : "还没有符合条件的商品。"}</p></td></tr>`;
    return `
      <div class="result-head">
        <div>
          <h1 class="page-title">${esc(entryOf(keyword)?.title || keyword)}</h1>
          <p class="conditions">${esc(describeConditions(keyword))}</p>
          <p class="storage-note">本次数据保存位置：<code>${esc(metadata.storage_path || `data/searches/${keyword}.jsonl`)}</code></p>
          <p class="lede result-status" role="status">${esc(status)}</p>
          <div class="result-actions">
            <button class="start" type="button" data-refresh-keyword="${esc(keyword)}" ${busy ? "disabled" : ""}>${pending ? "正在启动…" : running ? (state.job.refresh ? "正在更新…" : "正在抓取…") : "更新数据"}</button>
            <button class="ghost" type="button" data-delete-keyword="${esc(keyword)}" ${running || pending ? 'disabled title="请先停止抓取"' : ''}>删除这个词条</button>
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
        <thead><tr>${sortHeader("seq", "ID")}<th>图片</th>${sortHeader("title", "商品")}${sortHeader("shop_name", "店名")}${sortHeader("price", "价格")}${sortHeader("sales", "销量")}${sortHeader("updated", "更新", "本轮更新中，已有商品的内容是否发生变化")}</tr></thead>
        <tbody>${body}</tbody>
      </table></div>
      <nav class="pagination" aria-label="商品列表翻页"><button class="ghost" type="button" data-page-step="prev" data-page-keyword="${esc(keyword)}" ${view.page === 1 ? "disabled" : ""}>上一页</button><form class="page-jump" data-page-form="${esc(keyword)}"><label for="page-number-${esc(keyword)}">第</label><input id="page-number-${esc(keyword)}" name="page" type="number" min="1" max="${pageCount}" step="1" inputmode="numeric" value="${view.page}"><span>/ ${pageCount} 页</span><button class="ghost" type="submit">跳转</button></form><button class="ghost" type="button" data-page-step="next" data-page-keyword="${esc(keyword)}" ${view.page === pageCount ? "disabled" : ""}>下一页</button></nav>`;
  }


  function detailPage(tab) {
    const item = (state.goods[tab.keyword] || []).find((row) => row.goods_id === tab.goodsId);
    if (!item) return `<p class="empty">这条商品不在当前结果里。</p>`;
    return `
      <article class="detail">
        ${item.image ? `<img src="${esc(safeWebUrl(item.image))}" alt="${esc(item.title || "")}">` : `<span class="ph"></span>`}
        <div>
          <h1 class="page-title">${esc(item.title || "未命名商品")}</h1>
          <p class="price">${esc(money(item.price))}</p>
          <div class="badges">${(item.badges || []).map((id) => `<span>${esc(badgeText(id))}</span>`).join("")}</div>
          <dl>
            <dt>销量</dt><dd>${esc(item.sales || "未知")}</dd>
            <dt>商品编号</dt><dd>${esc(item.goods_id)}</dd>
            <dt>ID</dt><dd>${esc(item.seq || "")}</dd>
            <dt>店名</dt><dd>${esc(item.shop_name || "未知")}</dd>
            <dt>词条</dt><dd>${esc(tab.keyword)}</dd>
          </dl>
          <p><a href="${esc(safeWebUrl(item.url, true))}" target="_blank" rel="noreferrer">在拼多多打开</a></p>
          <p><button class="ghost" type="button" data-delete-goods="${esc(item.goods_id)}" data-keyword="${esc(tab.keyword)}" ${state.job.running && state.job.keyword === tab.keyword ? 'disabled title="请先停止抓取"' : ''}>删除这条</button></p>
        </div>
      </article>`;
  }


  function updateResultPage(keyword) {
    // 不重建筛选表单；刷新表格时保留滚动位置、焦点和未提交的输入。
    const stage = $("stage");
    const template = document.createElement("template");
    template.innerHTML = resultPage(keyword);
    const scrollTop = stage.scrollTop;
    for (const selector of ["[data-refresh-keyword]", "[data-delete-keyword]"]) {
      const current = stage.querySelector(selector);
      const next = template.content.querySelector(selector);
      if (current && next) {
        current.disabled = next.disabled;
        current.title = next.title;
        if (current.textContent !== next.textContent) current.textContent = next.textContent;
      }
    }
    for (const selector of [".conditions", ".result-status", ".table-count", ".catalog tbody"]) {
      const current = stage.querySelector(selector);
      const next = template.content.querySelector(selector);
      if (current && next && current.innerHTML !== next.innerHTML) {
        // 选择商品文字时暂缓替换，选择结束后即使没有新批次也会补上。
        if (selector === ".catalog tbody") {
          const selection = window.getSelection();
          if (current.contains(document.activeElement) || (selection && !selection.isCollapsed && selection.containsNode(current, true))) {
            current.dataset.deferred = "";
            continue;
          }
        }
        current.innerHTML = next.innerHTML;
      }
      if (current) delete current.dataset.deferred;
    }
    const pagination = stage.querySelector(".pagination");
    const nextPagination = template.content.querySelector(".pagination");
    if (pagination && !pagination.contains(document.activeElement) && pagination.innerHTML !== nextPagination.innerHTML) pagination.innerHTML = nextPagination.innerHTML;
    stage.scrollTop = scrollTop;
  }


  return { applyListTools, viewOf, describeConditions, sortedAndRangedRows, searchForm, resultPage, detailPage, updateResultPage };
}
