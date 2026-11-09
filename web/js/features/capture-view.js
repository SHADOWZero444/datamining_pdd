// 同一后台页面的画面与用户输入；不会打开新窗口或创建新的站点会话。
const capturePanel = (() => {
  const dialog = document.getElementById("capture-dialog");
  const image = document.createElement("img");
  Object.assign(image, { id: "capture-image", alt: "当前抓取页面，可点击或拖动操作", tabIndex: 0, draggable: false });
  const status = document.getElementById("capture-view-status");
  const text = document.getElementById("capture-text");
  let jobId = null, timer = null, generation = 0, points = null, lastPointAt = 0;
  let commandChain = Promise.resolve();

  async function command(action, values = {}, id = jobId) {
    return api("/api/search/browser", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Capture-View": "1" },
      body: JSON.stringify({ action, job_id: id, ...values }),
    });
  }

  function send(action, values) {
    const id = jobId, version = generation;
    commandChain = commandChain.then(async () => {
      if (version !== generation || !dialog.open) return;
      try { await command(action, values, id); }
      catch (error) { if (version === generation) status.textContent = error.message; }
    });
    return commandChain;
  }

  function hide(notify = true) {
    const id = jobId;
    generation += 1;
    clearTimeout(timer);
    points = null;
    jobId = null;
    text.value = "";
    image.hidden = true;
    image.remove();
    image.removeAttribute("src");
    dialog.close();
    if (notify && id) command("close", {}, id).catch(() => {});
  }

  async function poll(version) {
    try {
      const frame = await api(`/api/search/browser/frame?job_id=${encodeURIComponent(jobId)}`, {
        headers: { "X-Capture-View": "1" },
      });
      if (version !== generation || !dialog.open) return;
      if (!frame.opened) { hide(false); return; }
      if (!frame.image && frame.error) image.hidden = true;
      if (frame.image && !points) {
        image.src = `data:image/jpeg;base64,${frame.image}`;
        if (!image.isConnected) document.getElementById("capture-screen").append(image);
        image.hidden = false;
      }
      status.textContent = frame.error || (frame.image ? "正在显示当前页面。点击页面后，也可用方向键、回车或退格键操作。" : "正在加载页面…");
      document.getElementById("capture-continue").hidden = state.job.status !== "verification";
    } catch (error) {
      if (version !== generation || !dialog.open) return;
      status.textContent = error.message;
    }
    if (version === generation && dialog.open) timer = setTimeout(() => poll(version), 600);
  }

  function point(event) {
    const rect = image.getBoundingClientRect(), now = performance.now();
    const result = [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)), Math.min(100, now - lastPointAt)];
    lastPointAt = now;
    return result;
  }
  image.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || points) return;
    event.preventDefault();
    image.focus({ preventScroll: true });
    image.setPointerCapture(event.pointerId);
    points = [point(event)];
  });
  image.addEventListener("pointermove", (event) => {
    if (!points) return;
    if (points.length < 127) points.push(point(event));
    else points[126] = point(event);
  });
  image.addEventListener("pointerup", (event) => {
    if (!points) return;
    points.push(point(event));
    send("pointer", { points });
    points = null;
  });
  image.addEventListener("pointercancel", () => { points = null; });
  image.addEventListener("dragstart", (event) => event.preventDefault());
  image.addEventListener("wheel", (event) => {
    if (document.activeElement !== image) return;
    event.preventDefault();
    send("wheel", { delta: Math.max(-1000, Math.min(1000, event.deltaY)) });
  }, { passive: false });
  image.addEventListener("keydown", (event) => {
    if (["Enter", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault(); send("key", { key: event.key });
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing) {
      event.preventDefault(); send("text", { text: event.key });
    }
  });
  document.getElementById("capture-text-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (text.value) { send("text", { text: text.value }); text.value = ""; }
  });
  document.getElementById("capture-tab").addEventListener("click", () => send("key", { key: "Tab" }));
  document.getElementById("capture-close").addEventListener("click", () => hide());
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); hide(); });
  return {
    hide,
    show(id) {
      generation += 1;
      clearTimeout(timer);
      jobId = id;
      status.textContent = "正在加载页面…";
      document.getElementById("capture-continue").hidden = state.job.status !== "verification";
      if (!dialog.open) dialog.showModal();
      poll(generation);
    },
    get isOpen() { return dialog.open; },
    get jobId() { return jobId; },
  };
})();
