# 系统设置

当前设置分为“外观与启动”和“登录与数据”两组。配色支持白天、墨色、暖纸、跟随系统；启动页支持主界面和功能区。更改立即保存，写入失败时保留原值并显示错误。

列表默认每页 100 条、显示图片、抓取上限留空。这三项沿用固定默认值，不从旧版偏好中恢复。恢复默认偏好只重置配色和启动页，不删除商品或登录信息。

## 代码与存储

- `web/js/features/settings.js`：默认值、校验、持久化、页面及交互。
- `web/css/settings.css`：布局、响应式与状态样式。
- `web/js/features/search.js`：列表与搜索表单读取偏好。
- `pdd/desktop.py`：桌面窗口使用 `data/ui-profile/` 持久化界面浏览器存储。

偏好保存在 localStorage 的 `pdd-preferences-v1`。桌面窗口与外部浏览器各自保存；清理浏览器数据会清除偏好。没有新版设置时兼容读取旧版 `pdd-theme`。

运行 `python tests/verify_settings.py` 验证主题、启动页、恢复默认、存储失败及窄窗口布局。
