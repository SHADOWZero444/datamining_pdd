// 功能依赖由入口显式注入；内部函数共享本功能状态。
function createCaptureFeature({ state, capturePanel }) {
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
    const paused = state.job.status === "paused";
    const cooling = paused && state.job.intervention_kind !== "rate_limit_ready";
    resume.hidden = !needsVerification && !paused;
    resume.disabled = cooling || state.job.status === "stopping";
    resume.textContent = cooling ? "冷却中…" : "继续抓取";
  }


  return { renderCaptureBar };
}
