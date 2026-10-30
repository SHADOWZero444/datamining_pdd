# 拼多多数据挖掘

Windows 本机辅助工具：按搜索词抓取商品，支持关键词、禁用词、价格和标签筛选，以及结果查看和词条分组。

## 运行

需要 Python 3.13、Microsoft Edge 或 Chrome，以及 WebView2 Runtime。

```powershell
python -m pip install -r requirements.txt
python app.py
```

在界面中导入自己的登录信息，再开始搜索。关键词匹配商品标题，多个关键词必须全部包含；留空不限。

## 代码结构

- `app.py`：桌面入口；`pdd_search.py`：命令行入口。
- `pdd/browser/`：浏览器会话和人工验证交互。
- `pdd/scraping/`：解析、筛选、翻页和采集。
- `pdd/storage/`：商品结果、筛选条件和词条分组保存。
- `pdd/services/`：登录和抓取任务管理。
- `pdd/server/`：本机接口。
- `web/js/core/`：通用工具；`web/js/features/`：功能模块；`web/js/app.js`：组装与刷新。
- `tests/`：功能回归；`build/`：原有打包脚本。

登录信息和抓取结果保存在 `cookie.txt`、`data/`，开源时不要上传。`.artifacts/`、`__pycache__/` 和构建缓存不属于源码。项目仍在开发，店名识别、数据更新等待办以 AGENTS.md 为准。
