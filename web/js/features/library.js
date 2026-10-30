// 功能依赖由入口显式注入；内部函数共享本功能状态。
function createLibraryFeature({ state, TOP, ICONS, esc, icon, askModal, api, render, closeTab }) {
  function entryOf(keyword) {
    return state.library.entries.find((item) => item.keyword === keyword);
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


  return { entryOf, renderNav, openEntryMenu, openGroupMenu, moveKeywords, removeKeywords };
}
