// 功能依赖由入口显式注入；内部函数共享本功能状态。
function createNavigationFeature({ state, ICONS, esc, render }) {
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


  return { openTab, closeTab, activeSearch, renderTabs, moveTab, bindTabDrag };
}
