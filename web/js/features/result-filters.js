// 每个词条独立保存输入和最后一组有效条件。
function emptyResultFilters() {
  return {tags: [], required: "", banned: "", shop: "", minPrice: "", maxPrice: "", minSales: "", maxSales: ""};
}

function resultFilterWords(text) {
  return text.toLowerCase().split(/[,，]/).map(word => word.trim()).filter(Boolean);
}

function resultSalesValue(raw) {
  const text = String(raw ?? "").replace(/,/g, "").trim();
  const match = text.match(/(?:拼|售|销|抢)[售出量\s：:]*(\d+(?:\.\d+)?)\s*([万亿]?)/)
    || text.match(/^(\d+(?:\.\d+)?)\s*([万亿]?)\s*\+?\s*(?:件|人|单|笔)?\s*\+?(?:已拼|已售|已购|付款)?$/);
  if (!match) return -1;
  const unit = match[2] === "万" ? 10000 : match[2] === "亿" ? 100000000 : 1;
  return Number(match[1]) * unit;
}

function resultPredicate(filters) {
  const required = resultFilterWords(filters.required);
  const banned = resultFilterWords(filters.banned);
  const shops = resultFilterWords(filters.shop);
  function inRange(raw, min, max, sales = false) {
    if (min === "" && max === "") return true;
    const value = sales ? resultSalesValue(raw) : numericValue(raw);
    return value >= 0 && (min === "" || value >= Number(min)) && (max === "" || value <= Number(max));
  }
  return item => {
    const title = String(item.title || "").toLowerCase();
    const shop = String(item.detail?.shop_name || "").toLowerCase();
    return filters.tags.every(tag => (item.badges || []).includes(tag))
      && required.every(word => title.includes(word)) && !banned.some(word => title.includes(word))
      && shops.every(word => shop.includes(word))
      && inRange(item.price, filters.minPrice, filters.maxPrice)
      && inRange(item.sales, filters.minSales, filters.maxSales, true);
  };
}

function resultFilterCount(f) {
  return [f.tags.length, f.required.trim(), f.banned.trim(), f.shop.trim(),
    f.minPrice || f.maxPrice, f.minSales || f.maxSales].filter(Boolean).length;
}

function readResultFilters(form, view) {
  const data = new FormData(form);
  const draft = emptyResultFilters();
  for (const key of Object.keys(draft)) draft[key] = key === "tags" ? data.getAll(key) : String(data.get(key) || "");
  view.filterDraft = draft;
  let error = "";
  for (const [min, max, label] of [["minPrice", "maxPrice", "价格"], ["minSales", "maxSales", "销量"]]) {
    const from = form.elements[min], to = form.elements[max];
    if (!from.validity.valid || !to.validity.valid) error = label === "销量" ? "销量请填写非负整数。" : "价格请填写非负数。";
    else if (draft[min] && draft[max] && Number(draft[min]) > Number(draft[max])) error = label + "下限不能大于上限。";
    const invalid = Boolean(error);
    for (const input of [from, to]) input.setAttribute("aria-invalid", String(invalid));
    if (error) break;
  }
  view.filterError = error;
  if (!error) {
    view.filters = draft;
    view.page = 1;
  }
}

function resultFilterBar(keyword, view, tags, count) {
  const f = view.filterDraft;
  const active = resultFilterCount(view.filters);
  const words = (key, label) => `<label class="filter-field">${label}<input name="${key}" type="text" autocomplete="off" value="${esc(f[key])}"></label>`;
  function range(label, unit, min, max, step) {
    const bound = (key, name) => `<input name="${key}" type="number" aria-label="${label}${name}" aria-describedby="filter-range-error" placeholder="${name}" min="0" step="${step}" value="${esc(f[key])}">`;
    return `<fieldset class="filter-range"><legend>${label}（${unit}）</legend><div>${bound(min, "最低")}<span>至</span>${bound(max, "最高")}</div></fieldset>`;
  }
  const locked = count === 0 || view.filterError || view.exporting;
  return `<section class="result-filters" aria-label="筛选与导出">
    <div class="filter-toolbar">
      <button type="button" class="filter-toggle" data-toggle-filters="${esc(keyword)}" aria-expanded="${view.filtersOpen}" aria-controls="result-filter-fields"><i aria-hidden="true"></i>数据筛选</button>
      <span class="filter-summary" aria-live="polite">${view.filterError ? "请检查筛选区间" : active ? `已用 ${active} 项 · 匹配 ${count} 条` : ""}</span>
      <div class="filter-exports">${[["xlsx","Excel"],["csv","CSV"]].map(([format,label]) => `<button class="ghost" type="button" data-export-format="${format}" data-export-term="${esc(keyword)}" title="导出全部 ${count} 条结果" ${locked ? "disabled" : ""}>导出 ${label}</button>`).join("")}</div>
    </div>
    <form id="result-filter-fields" class="filter-fields" data-result-filters="${esc(keyword)}" ${view.filtersOpen ? "" : "hidden"}>
      <fieldset class="filter-tags"><legend>标签（同时满足）</legend><div class="chips">${tags.map(tag => `<label class="chip"><input type="checkbox" name="tags" value="${esc(tag.id)}" ${f.tags.includes(tag.id) ? "checked" : ""}>${esc(tag.label)}</label>`).join("")}</div></fieldset>
      <div class="filter-word-grid">${words("required", "商品关键词")}${words("banned", "商品禁用词")}${words("shop", "店名关键词")}</div>
      <p class="filter-hint">多个词用逗号分隔。关键词须全部匹配，含任一禁用词的商品会被排除。</p>
      <div class="filter-range-grid">${range("价格", "元", "minPrice", "maxPrice", "any")}${range("销量", "件", "minSales", "maxSales", "1")}</div>
      <div class="filter-footer"><span id="filter-range-error" class="filter-error" role="status">${esc(view.filterError)}</span><button type="button" class="text-btn" data-reset-filters="${esc(keyword)}">清除筛选</button></div>
    </form>
    <p class="export-status" role="status">${esc(view.exportMessage)}</p>
  </section>`;
}
