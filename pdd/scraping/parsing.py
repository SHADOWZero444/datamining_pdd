"""把页面卡片及搜索接口归一化为商品记录；不保存数据或驱动任务。"""
from urllib.parse import urljoin
from playwright.sync_api import Error as BrowserError
from .constants import BASE

BADGE_MARKS = {
    "bybt": "bybt",
    "black": "brand_black_label",
    "official": "6e9dba7f-bef0-4263-a355-e42dc63485c7",
    "flagship": "962149d1-b03a-47fb-be05-7f289e14ed3b",
}


def badges_from_urls(urls):
    """根据角标图片地址判断商品带了哪些标签。"""
    text = " ".join(urls).lower()
    return [key for key, mark in BADGE_MARKS.items() if mark.lower() in text]


def urls_in_model(model):
    urls = []

    def walk(node):
        if isinstance(node, str) and "http" in node:
            urls.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "img_url", "image_url") or "icon" in key.lower():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for key in ("icon_list", "prefix_icons", "prefixIcons"):
        walk(model.get(key))
    return urls


def _evaluate_goods(page, script):
    """登录/验证跳转会销毁旧 JS 上下文，交还主循环识别新页面。"""
    try:
        return page.evaluate(script)
    except BrowserError as exc:
        interrupted = any(text in str(exc) for text in (
            "Execution context was destroyed", "Cannot find context with specified id"))
        if interrupted and not page.is_closed():
            return []
        raise


def read_live_goods(page, keyword=""):
    """从已经打开的搜索页里读商品。price 是页面上显示的价格，不是分为单位的原价。"""
    rows = _evaluate_goods(page,
        """() => {
            const data = window.rawData;
            const list = data && data.stores && data.stores.store && data.stores.store.data
                && data.stores.store.data.ssrListData && data.stores.store.data.ssrListData.list;
            if (!list) return [];
            return list.filter((item) => item.goodsID).map((item) => ({
                goods_id: String(item.goodsID),
                title: item.goodsName || "",
                price: item.priceInfo || "",
                sales: item.salesTip || "",
                image: item.imgUrl || "",
                mall_id: item.mallEntrance && item.mallEntrance.mall_id ? String(item.mallEntrance.mall_id) : "",
                shop_name: item.mallName || item.mall_name || (item.mallEntrance && (item.mallEntrance.mallName || item.mallEntrance.mall_name)) || "",
                tags: (item.tagList || []).map((tag) => tag.text).filter(Boolean),
                icons: (item.prefixIcons || []).map((icon) => icon && icon.url).filter(Boolean),
                url: item.linkURL || "",
            }));
        }"""
    )
    goods = []
    for row in rows:
        link = row.get("url") or ""
        if link and not link.startswith("http"):
            row["url"] = urljoin(BASE, link)
        elif not link:
            row["url"] = f"{BASE}goods.html?goods_id={row['goods_id']}"
        row["keyword"] = keyword
        row["badges"] = badges_from_urls(row.pop("icons", []) or [])
        goods.append(row)
    return goods


def read_dom_goods(page, keyword=""):
    """接口现在是加密的。商品画在页面上，就从卡片里把编号、名字和价格读出来。"""
    rows = _evaluate_goods(page,
        """() => {
            function readCard(node) {
                const fiberKey = Object.keys(node).find((key) => key.startsWith("__reactFiber"));
                let fiber = fiberKey ? node[fiberKey] : null;
                for (let i = 0; i < 18 && fiber; i += 1) {
                    const props = fiber.memoizedProps || {};
                    const data = props.data || props.item || props.goods || props.goodsData;
                    if (data && (data.goodsID || data.goods_id)) {
                        return {
                            goods_id: String(data.goodsID || data.goods_id),
                            title: data.goodsName || data.goods_name || "",
                            price: data.priceInfo || data.price_info || "",
                            sales: data.salesTip || data.sales_tip || "",
                            image: data.imgUrl || data.thumb_url || data.hd_thumb_url || "",
                            link: data.linkURL || data.link_url || "",
                            mall_id: String((data.mallEntrance && data.mallEntrance.mall_id) || data.mall_id || ""),
                            shop_name: (data.mallName || data.mall_name || (data.mallEntrance && (data.mallEntrance.mallName || data.mallEntrance.mall_name || data.mallEntrance.name)) || "").replace(/[\uE000-\uF8FF]/g, "").trim(),
                            icons: (data.prefixIcons || []).map((icon) => icon && icon.url).filter(Boolean),
                        };
                    }
                    fiber = fiber.return;
                }
                return null;
            }
            const cards = [...new Set([...document.querySelectorAll("img")]
                .map((image) => image.parentElement).filter(Boolean))];
            const rows = [];
            const seen = new Set();
            cards.forEach((card) => {
                const row = readCard(card);
                if (row && row.goods_id && !seen.has(row.goods_id)) {
                    if (!row.shop_name) {
                        const lines = (card.innerText || "").split("\\n").map((line) => line.replace(/[\uE000-\uF8FF]/g, "").trim()).filter(Boolean);
                        row.shop_name = lines.find((line) => line !== row.title && !line.includes("¥") && !line.includes("已拼") && !line.includes("已抢") && line.length <= 20) || "";
                    }
                    seen.add(row.goods_id);
                    rows.push(row);
                }
            });
            return rows;
        }"""
    )
    goods = []
    for row in rows:
        link = row.get("link") or ""
        row["url"] = urljoin(BASE, link) if link else f"{BASE}goods.html?goods_id={row['goods_id']}"
        row.pop("link", None)
        row["keyword"] = keyword
        row["tags"] = []
        row["shop_name"] = (row.get("shop_name") or "").strip()
        row["badges"] = badges_from_urls(row.pop("icons", []) or [])
        goods.append(row)
    return goods


def normalize_model(model, keyword=""):
    """把接口里的 goods_model 收成一条商品。没有 goods_id 的就丢掉。"""
    goods_id = model.get("goods_id")
    if not goods_id or not model.get("goods_name"):
        return None
    link = model.get("link_url") or ""
    tags = [tag.get("text") for tag in (model.get("tag_list") or []) if isinstance(tag, dict) and tag.get("text")]
    return {
        "goods_id": str(goods_id),
        "title": model.get("goods_name") or "",
        "price": model.get("price_info") or "",
        "sales": model.get("sales_tip") or "",
        "image": model.get("thumb_url") or model.get("hd_thumb_url") or "",
        "mall_id": str(model.get("mall_id") or ""),
        "shop_name": model.get("mall_name") or model.get("mallName") or "",
        "tags": tags,
        "badges": badges_from_urls(urls_in_model(model)),
        "url": urljoin(BASE, link) if link else f"{BASE}goods.html?goods_id={goods_id}",
        "keyword": keyword,
    }


def goods_from_api(data, keyword=""):
    rows = []
    for item in data.get("items") or []:
        model = ((item or {}).get("item_data") or {}).get("goods_model") or {}
        row = normalize_model(model, keyword)
        if row:
            rows.append(row)
    return rows
