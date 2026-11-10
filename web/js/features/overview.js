// 新增入口：在这里追加配置，并在 FEATURE_ACTIONS 中登记对应操作。
const FEATURE_CARDS = [
  {
    id: "keyword-search", title: "词条搜索爬取",
    description: "",
    image: "assets/features/keyword-search.png", fallback: "从一个关键词开始",
    action: "keywordSearch", enabled: true,
  },
  {
    id: "coming-soon", title: "尚未开发",
    description: "",
    image: "assets/features/coming-soon.png", fallback: "留给下一种可能",
    action: null, enabled: false,
  },
];

const FEATURE_ACTIONS = {
  keywordSearch() {
    state.toolLabel = "搜索词条爬取";
    state.active = null;
    state.section = "tools";
    render();
    const heading = document.querySelector("#stage h1");
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
  },
};

function toolsOverview() {
  return `<section class="tools-overview" aria-labelledby="tools-overview-title">
    <header class="tools-overview-heading">
      <h1 class="page-title" id="tools-overview-title">功能区概览</h1>
      <p class="lede"></p>
    </header>
    <div class="feature-grid">${FEATURE_CARDS.map((card) => {
      const available = card.enabled && typeof FEATURE_ACTIONS[card.action] === "function";
      return `<article class="feature-card ${available ? "is-available" : "is-unavailable"}" ${available ? "" : 'aria-disabled="true"'}>
        <div class="feature-art" data-feature-image="${esc(card.id)}" aria-hidden="true">
          <span class="feature-art-fallback">${esc(card.fallback)}</span>
        </div>
        <div class="feature-body">
          <span class="feature-status">${available ? "可使用" : "暂未开放"}</span>
          <h2 id="feature-title-${esc(card.id)}">${esc(card.title)}</h2>
          <p id="feature-description-${esc(card.id)}">${esc(card.description)}</p>
          <button type="button" class="feature-enter" data-feature="${esc(card.id)}"
            aria-labelledby="feature-title-${esc(card.id)} feature-action-${esc(card.id)}"
            aria-describedby="feature-description-${esc(card.id)}" ${available ? "" : "disabled"}>
            <span id="feature-action-${esc(card.id)}">${available ? "进入功能" : "尚不可用"}</span>
            ${available ? '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 10h13M11 5l5 5-5 5" stroke="currentColor" stroke-width="2" stroke-linejoin="miter"/></svg>' : ""}
          </button>
        </div>
      </article>`;
    }).join("")}</div>
  </section>`;
}

function hydrateFeatureImages() {
  document.querySelectorAll("[data-feature-image]").forEach((slot) => {
    const card = FEATURE_CARDS.find((item) => item.id === slot.dataset.featureImage);
    if (!card?.image) return;
    const img = new Image();
    img.alt = "";
    img.width = 960;
    img.height = 540;
    img.onload = () => { if (slot.isConnected) slot.replaceChildren(img); };
    // 用户尚未提供图片时保留完整的文字底板，不显示破图或内部路径。
    img.onerror = () => {};
    img.src = card.image;
  });
}

function openOverviewFeature(id) {
  const card = FEATURE_CARDS.find((item) => item.id === id);
  if (card?.enabled && typeof FEATURE_ACTIONS[card.action] === "function") FEATURE_ACTIONS[card.action]();
}
