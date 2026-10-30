function createFormHandler({ state, api, render, applyListTools, viewOf, sortedAndRangedRows, renderStage, loadGoods, openTab }) {
  async function handleSubmit(event) {
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
    const requiredWords = String(form.get("requiredWords") || "").trim();
    const banned = String(form.get("banned") || "").trim();
    const limit = String(form.get("limit") || "").trim();
    const tags = form.getAll("tags");
    const error = document.getElementById("form-error");
    state.form = { keyword, priceMin, priceMax, requiredWords, banned, tags, limit };
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
        body: JSON.stringify({ keyword, limit, tags, required_words: requiredWords, banned, price_min: priceMin, price_max: priceMax }),
      });
      state.job = { running: true, keyword, status: "running", message: "正在打开搜索页", count: 0 };
      if (!state.keywords.some((item) => item.keyword === keyword)) state.keywords.push({ keyword, count: 0 });
      openTab({ id: `result:${keyword}`, title: keyword, kind: "search", keyword });
    } catch (exc) {
      error.textContent = exc.message;
    }
  }
  return handleSubmit;
}
