function createActionHandler({ state, api, render, renderStage, renderNav, renderCaptureBar, capturePanel, loadGoods, openTab, closeTab, viewOf, updateResultPage, sortedAndRangedRows, openOverviewFeature, askModal, openEntryMenu, openGroupMenu, moveKeywords, removeKeywords }) {
  async function handleClick(event) {
    const toggle = event.target.closest("[data-toggle-filters]");
    if (toggle) {
      const view = viewOf(toggle.dataset.toggleFilters);
      view.filtersOpen = !view.filtersOpen;
      toggle.setAttribute("aria-expanded", String(view.filtersOpen));
      $("result-filter-fields").hidden = !view.filtersOpen;
      return;
    }
    const reset = event.target.closest("[data-reset-filters]");
    if (reset) {
      const term = reset.dataset.resetFilters;
      const form = $("result-filter-fields");
      for (const input of form.querySelectorAll("input")) {
        if (input.type === "checkbox") input.checked = false;
        else input.value = "";
      }
      const view = viewOf(term);
      readResultFilters(form, view);
      if (!view.exporting) view.exportMessage = "";
      updateResultPage(term);
      return;
    }
    const exportButton = event.target.closest("[data-export-format]");
    if (exportButton) {
      const term = exportButton.dataset.exportTerm;
      const view = viewOf(term);
      if (view.exporting || view.filterError) return;
      const ids = sortedAndRangedRows(term).map(row => row.goods_id);
      if (!ids.length) return;
      const refreshVisible = () => {
        if (state.section === "tools" && state.active === `result:${term}`) updateResultPage(term);
      };
      view.exporting = true;
      view.exportMessage = `请选择保存位置，共 ${ids.length} 条。`;
      refreshVisible();
      try {
        const result = await api("/api/results/export", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({keyword: term, format: exportButton.dataset.exportFormat, ids, revision: state.revisions[term]}),
        });
        view.exportMessage = result.cancelled ? "已取消导出。" : `已将 ${result.count} 条商品保存到 ${result.path}`;
      } catch (error) {
        view.exportMessage = error.message;
      } finally {
        view.exporting = false;
        refreshVisible();
      }
      return;
    }
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
        // 验证是否结束由后台确认；仍需操作时保留当前画面。
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
    const feature = event.target.closest("[data-feature]");
    if (feature) { openOverviewFeature(feature.dataset.feature); return; }
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
    const refreshButton = event.target.closest("[data-refresh-keyword]");
    if (refreshButton) {
      if (state.pendingRefresh || state.job.running || state.auth.running) return;
      const keyword = refreshButton.dataset.refreshKeyword;
      const updateVisiblePage = () => {
        const tab = state.tabs.find((item) => item.id === state.active);
        if (state.section === "tools" && tab?.kind === "search") updateResultPage(tab.keyword);
      };
      delete state.refreshErrors[keyword];
      state.pendingRefresh = keyword;
      updateVisiblePage();
      try {
        const data = await api("/api/search/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword }),
        });
        state.job = data.job;
        renderCaptureBar();
      } catch (exc) {
        state.refreshErrors[keyword] = exc.message;
      } finally {
        state.pendingRefresh = null;
        updateVisiblePage();
      }
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
  }
  return handleClick;
}
