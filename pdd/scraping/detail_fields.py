"""仅从当前商品详情提取可核对的字段。不依赖构建时生成的 CSS 类名。"""

DETAIL_FIELDS = r"""id => {
    const init = window.rawData?.store?.initDataObj;
    const root = document.querySelector('.goods-container-v2') || document.querySelector('#main') || document.body;
    const url = new URL(location.href);
    if (!root || url.origin !== 'https://mobile.pinduoduo.com' || url.pathname !== '/goods.html'
        || url.searchParams.get('goods_id') !== id ||
        (init?.goods?.goodsID != null && String(init.goods.goodsID) !== id)) return null;
    const txt = el => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    const before = (a,b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const button = [...root.querySelectorAll('span[role="button"]')].find(e => txt(e) === '进店逛逛');
    let shop = '';
    const brand = init?.goods?.ui?.brand_section?.black_brand?.brand ||
                  init?.oakData?.ui?.brandSection?.blackBrand?.brand;
    const badge = root.querySelector('img[src*="brand_black_label"],img[data-src*="brand_black_label"]');
    if (typeof brand === 'string' && brand.trim()) shop = brand.trim();
    else if (badge) {
        const guarantee = root.querySelector('[aria-label*="假一赔十"]');
        for (let box=guarantee?.parentElement,n=0; box && n<4 && !shop; box=box.parentElement,n++) {
            const labels=[...box.querySelectorAll('span')].filter(el =>
                before(el,guarantee) && !el.contains(guarantee) && !el.children.length &&
                txt(el) && txt(el).length<=60);
            if (labels.length) shop=txt(labels.at(-1));
        }
    } else if (button) {
        for (let box=button.parentElement; box && box!==root; box=box.parentElement) {
            const logo = box.querySelector('img');
            if (!logo || !before(logo,button)) continue;
            for (const el of box.querySelectorAll('div')) {
                if (!before(logo,el) || !before(el,button) || el.querySelector('div,img,button,[role="button"]')) continue;
                const name = txt(el);
                if (name && name.length<=100 && !/本店已拼|进店逛逛|在线中/.test(name)) { shop=name; break; }
            }
            if (shop) break;
        }
        if (!shop && typeof init?.mall?.mallName === 'string') shop=init.mall.mallName.trim();
    }
    const images = new Set(), groups = new Set();
    for (const el of root.querySelectorAll('[aria-label="商品大图"],[aria-label="查看图片"]')) {
        for (const img of el.matches('img') ? [el] : el.querySelectorAll('img')) {
            for (const src of [img.dataset.src,img.currentSrc,img.getAttribute('src')]) {
                if (!src) continue;
                try {
                    const u=new URL(src,location.href);
                    if (u.protocol==='http:' && /(^|\.)pddpic\.com$/i.test(u.hostname)) u.protocol='https:';
                    if (u.protocol==='https:' && !u.username && !u.password) {
                        images.add(u.href); groups.add(el.getAttribute('aria-label')); break;
                    }
                } catch {}
            }
        }
    }
    return {shop_name:shop, images:[...images],
            _settled:document.readyState !== 'loading' && groups.size===2};
}"""


def complete(detail):
    return all(detail.get(key) for key in ("shop_name", "images"))
