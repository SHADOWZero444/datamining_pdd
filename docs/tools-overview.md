# 功能区概览维护说明

本页用于选择功能（Operate）：紧凑图文网格。默认 1280px 桌面窗口一排容纳 4 张，
更宽窗口自动增加列数；仅有两张时也保留空列，不拉伸卡片。较窄窗口自动减少列数，
手机宽度使用两列，极窄窗口退为单列，保证标题和按钮可读。
继承全局白天、深色、暖色主题；使用直角边框、加厚底边、下压反馈和短入场动画，
保留轻度像素游戏菜单质感，不改变其他页面。减少动态效果设置下关闭动画。

## 文件位置

- `web/js/features/overview.js`：卡片配置 `FEATURE_CARDS`、动作表 `FEATURE_ACTIONS`、渲染及图片加载。
- `web/css/tools-overview.css`：布局、配色、卡片状态和动画，全部限定在该功能内。
  `.feature-grid` 的 `200px` 是桌面卡片最小宽度，`gap: 16px` 是间距；
  使用 `auto-fill` 保留空列，不要换成会撑大少量卡片的 `auto-fit`。
- `web/js/app.js`：`renderStage()` 调用概览；点击处理调用 `openOverviewFeature()`。
- `web/index.html`：引入概览 CSS 和 JS；概览 JS 在 app.js 前加载。
- `web/assets/features/`：用户制作的图片，具体规格见目录内 README.md。

## 新增卡片

在 `FEATURE_CARDS` 数组末尾增加一个对象，例如一个尚未开放的入口：

```js
{
  id: "data-export", // 唯一值；建议小写英文和连字符
  title: "数据导出",
  description: "选择需要的商品数据并导出。",
  image: "assets/features/data-export.png", // 相对于 web/ 的路径
  fallback: "整理好的数据，随时带走",
  action: "dataExport",
  enabled: false,
},
```

准备好功能后，在 `FEATURE_ACTIONS` 中添加对应操作，再将 `enabled` 改为 `true`：

```js
dataExport() {
  // 在这里调用你已经实现的导出页面导航或导出操作。
},
```

`action` 必须与动作表中的函数名一致。动作没有实现时保持 `enabled: false`，
不要仅添加空函数后开放入口。配置没有有效动作时，即使 enabled 为 true 也会禁用。
卡片排列顺序就是数组顺序；不需要复制 HTML 或 CSS。

## 图片与交互

两张图片为 `keyword-search.png`、`coming-soon.png`，建议 960 × 540 px，16:9。
不制作替代插画，图片缺失时使用文字底板。文件加载成功后自动替换，保持版面尺寸。
可用卡片整块可点击，Tab 聚焦按钮、Enter/空格打开；不可用入口标明原因且不能触发。
卡片不会直接开始抓取，只进入搜索表单，保留表单已有输入。

当前版本未修改全局视觉系统，也未更改“关于系统”或后端抓取逻辑。
