// 功能依赖由入口显式注入；内部函数共享本功能状态。
function createPagesFeature({ state, TOP, EYE_ICON, esc, settingsPage }) {
  function topPage() {
    if (state.section === "home") {
      const auth = state.auth;
      return `<section class="login-page">
        <div class="login-intro"><h1 class="page-title">登录拼多多</h1><p class="lede">导入自己的登录信息后，即可按条件抓取公开搜索结果。登录信息只保存在本机，不会上传到其他服务器。</p></div>
        <form class="login-form" id="cookie-form">
          <label class="field-label" for="cookie-input">Cookie 登录信息</label>
          <div class="cookie-field"><input id="cookie-input" name="cookie" class="cookie-input" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 cookie，或点击“前往获取”后自动导入" value="${esc(auth.cookie || "")}"><button class="eye-btn" type="button" data-toggle-cookie aria-label="显示登录信息" aria-pressed="false">${EYE_ICON}</button></div>
          <p class="help">请仅使用自己的账号。Cookie 是登录凭证，请勿发送给他人。</p>
          <p class="auth-status ${auth.status === "failed" ? "error" : ""}" role="status">${esc(auth.message || (auth.configured ? "已设置登录信息。" : "尚未设置登录信息请点击确认以开始。"))}</p>
          <div class="login-actions"><button class="start" type="submit">确认</button><button class="ghost" type="button" data-clear-cookie>清除</button><button class="ghost" type="button" data-login-cookie ${auth.running ? "disabled" : ""}>${auth.running ? "等待登录…" : "前往获取"}</button></div>
          <div class="help-text">
            <p>目标网站：https://mobile.pinduoduo.com/</p>
          </div>
        </form>
      </section>`;
    }
    if (state.section === "settings") {
      return settingsPage();
    }
    if (state.section === "about") {
      return `<h1 class="page-title">关于系统</h1><p class="lede">系统版本：测试版0.0.1</p>`;
    }
    return `<h1 class="page-title">${esc(TOP.find((item) => item.id === state.section)?.label || "")}</h1><p class="empty">这个页面还没做。请先使用功能区里的搜索词条爬取。</p>`;
  }


  return { topPage };
}
