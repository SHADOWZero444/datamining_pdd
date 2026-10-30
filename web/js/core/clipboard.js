function bindClipboard() {
  let copyTarget = null;

  function hideCopyMenu() {
    $("context-menu").hidden = true;
  }

  function selectedText(target) {
    if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
      return target.value.slice(target.selectionStart ?? 0, target.selectionEnd ?? 0);
    }
    return window.getSelection()?.toString() || "";
  }

  async function writeClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const box = document.createElement("textarea");
      box.value = text;
      document.body.appendChild(box);
      box.select();
      document.execCommand("copy");
      box.remove();
    }
  }

  async function readClipboard() {
    try {
      return await navigator.clipboard.readText();
    } catch {
      return "";
    }
  }

  function pasteInto(target, text) {
    const start = target.selectionStart ?? target.value.length;
    const end = target.selectionEnd ?? start;
    target.value = target.value.slice(0, start) + text + target.value.slice(end);
    const next = start + text.length;
    target.selectionStart = target.selectionEnd = next;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.focus();
  }

  document.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    const field = event.target.closest("input, textarea");
    copyTarget = field || event.target;
    const menu = $("context-menu");
    const copy = `<button type="button" data-copy-act="copy" role="menuitem">复制<kbd>Ctrl+C</kbd></button>`;
    const paste = field ? `<button type="button" data-copy-act="paste" role="menuitem">粘贴<kbd>Ctrl+V</kbd></button>` : "";
    menu.innerHTML = copy + paste;
    menu.hidden = false;
    menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - 210))}px`;
    menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - (field ? 90 : 52)))}px`;
  });

  $("context-menu").addEventListener("click", async (event) => {
    const act = event.target.closest("button")?.dataset.copyAct;
    if (act === "copy") {
      const text = selectedText(copyTarget) || copyTarget?.innerText || "";
      if (text.trim()) await writeClipboard(text);
    }
    if (act === "paste" && copyTarget) {
      const text = await readClipboard();
      if (text) pasteInto(copyTarget, text);
    }
    hideCopyMenu();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("#context-menu")) hideCopyMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideCopyMenu();
  });




}
