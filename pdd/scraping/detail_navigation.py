"""搜索页只读：在同一会话的独立标签页打开商品，保留搜索页的分页位置。"""
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as BrowserError
from .constants import BASE


def goods_url(url, key):
    try:
        u = urlsplit(url or "")
        return (u.scheme == "https" and u.netloc == "mobile.pinduoduo.com" and u.path == "/goods.html"
                and parse_qs(u.query).get("goods_id") == [key])
    except ValueError:
        return False


# 不点击、滚动或拦截列表请求，只读取对应商品的完整目标链接。
CARD_LINK = """id => {
    const links = new Set();
    function add(value) {
        if (!value) return;
        try {
            const u = new URL(value,location.href);
            if (u.origin === 'https://mobile.pinduoduo.com' && u.pathname === '/goods.html' &&
                u.searchParams.get('goods_id') === id) links.add(u.href);
        } catch {}
    }
    for (const a of document.querySelectorAll('a[href]')) add(a.href);
    for (const img of document.querySelectorAll('img')) {
        const node = img.parentElement;
        if (!node) continue;
        const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
        for (let f=key && node[key], n=0; f && n<18; n++, f=f.return) {
            const p=f.memoizedProps || {}, d=p.data || p.item || p.goods || p.goodsData;
            if (!d || String(d.goodsID || d.goods_id) !== id) continue;
            add(d.linkURL || d.link_url);
            break;
        }
    }
    const referral = window.Navigation?.getNextPageQuery?.() || {};
    return {links:[...links], referral:Object.fromEntries(['refer_page_id','refer_page_sn','refer_page_name']
        .filter(k => typeof referral[k] === 'string' || typeof referral[k] === 'number').map(k => [k,referral[k]]))};
}"""


def product_tab(anchor):
    """原生新标签页继承搜索页的 sessionStorage，无需把凭证读出或写入文件。"""
    if anchor.url.startswith(BASE + "search_result.html"):
        child = None
        try:
            with anchor.expect_popup(timeout=2000) as opened:
                if not anchor.evaluate("() => !!window.open('about:blank', '_blank')"):
                    raise BrowserError("popup unavailable")
            child = opened.value
            # 复制会话后断开 opener，详情页无权跳转或关闭搜索页。
            child.evaluate("window.opener = null")
            return child
        except BrowserError:
            if child and not child.is_closed():
                child.close()
    return anchor.context.new_page()


def open_product(anchor, tab, row, stop, allowed, stats):
    key = str(row["goods_id"])
    source = anchor.url
    search = source.startswith(BASE + "search_result.html")
    choices = []
    referral = {}
    if search:
        try:
            card = anchor.evaluate(CARD_LINK, key)
            choices.extend(("card_link", url) for url in card["links"] if goods_url(url, key))
            referral = card["referral"]
        except BrowserError:
            pass  # 虚拟列表可能移除卡片，仍可使用读取该记录时的链接。
    saved = urljoin(BASE, row.get("url") or "")
    if goods_url(saved, key):
        choices.append(("saved_link", saved))
    # 带上下文的当前卡片优先于可能已过期的历史链接，不能反过来用裸 ID 覆盖完整链接。
    def priority(item):
        params = parse_qs(urlsplit(item[1]).query)
        return (bool(params.keys() - {"goods_id"}), item[0] == "card_link", len(params))
    mode, link = max(choices, key=priority,
                     default=("id_link", f"{BASE}goods.html?goods_id={key}"))
    if search:
        # 录制的 GoodsList 使用当前搜索词作为 _x_query，商品页入口加 page_from=23。
        # 其余来源信息只取网站当前提供的值，保留原查询串（包括顺序和编码）。
        parts = urlsplit(link)
        present = parse_qs(parts.query, keep_blank_values=True)
        search_key = parse_qs(urlsplit(source).query).get("search_key", [""])[0]
        extra = urlencode({k: v for k, v in {"_x_query": search_key, "page_from": 23, **referral}.items()
                           if v and k not in present})
        if extra:
            link = urlunsplit(parts._replace(query=parts.query + "&" + extra))
    if stop() or not allowed():
        return "deferred"
    modes = stats.setdefault("detail_navigation", {})
    modes[mode] = modes.get(mode, 0) + 1
    tab.goto(link, referer=source if search else None, wait_until="commit", timeout=12000)
    return mode
