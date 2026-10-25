import json
import pdd_search
from playwright.sync_api import sync_playwright

SCRIPT = """() => {
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
                };
            }
            fiber = fiber.return;
        }
        return null;
    }
    const cards = [...document.querySelectorAll("div")].filter((node) => {
        const text = node.innerText || "";
        return text.includes("¥") && (text.includes("已拼") || text.includes("已抢")) && text.length < 280 && node.querySelector("img");
    });
    const rows = [];
    const seen = new Set();
    cards.forEach((card) => {
        const row = readCard(card);
        if (row && !seen.has(row.goods_id)) {
            seen.add(row.goods_id);
            rows.push(row);
        }
    });
    return { cards: cards.length, rows: rows.slice(0, 3), count: rows.length };
}"""

with sync_playwright() as factory:
    browser = factory.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=pdd_search.PHONE_UA,
        viewport={"width": 390, "height": 844},
        locale="zh-CN",
        has_touch=True,
    )
    context.add_cookies(pdd_search.load_cookie_items())
    page = context.new_page()
    page.goto(pdd_search.search_url("蓝牙耳机"), wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(7000)
    info = page.evaluate(SCRIPT)
    open("_probe_out.txt", "w", encoding="utf-8").write(json.dumps(info, ensure_ascii=False, indent=2))
    browser.close()
