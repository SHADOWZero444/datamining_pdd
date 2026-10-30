"""搜索条件校验与商品标题、标签、价格筛选。"""
import re
import math

def read_filters(payload):
    """把界面传来的筛选收成抓取函数能用的条件。空着的价格不当成 0。"""
    tags = payload.get("tags") or []
    if not isinstance(tags, list) or any(tag not in ("bybt", "black", "official", "flagship") for tag in tags):
        raise ValueError("请选择有效的商品标签。")
    for key in ("required_words", "banned"):
        if payload.get(key) is not None and (not isinstance(payload[key], str) or len(payload[key]) > 2000):
            raise ValueError("关键词和禁用词应为不超过 2000 字的文字。")
    filters = {
        "tags": list(dict.fromkeys(tags)),
        "required_words": (payload.get("required_words") or "").strip(),
        "banned": payload.get("banned") or "",
        "price_min": None,
        "price_max": None,
    }
    for key in ("price_min", "price_max"):
        raw = payload.get(key)
        if raw in ("", None):
            continue
        try:
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
                raise ValueError()
            filters[key] = value
        except (TypeError, ValueError):
            raise ValueError("价格请填写大于或等于 0 的有效数字。") from None
    if filters["price_min"] is not None and filters["price_max"] is not None and filters["price_min"] > filters["price_max"]:
        raise ValueError("最低价不能高于最高价。")
    return filters


def parse_price(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def apply_filters(goods, filters):
    """按标签、价格区间、标题关键词和禁用词留下符合条件的商品。"""
    filters = filters or {}
    wanted = set(filters.get("tags") or [])
    required_words = [word.strip().casefold() for word in re.split(r"[,，]", str(filters.get("required_words") or "")) if word.strip()]
    banned = [word.strip() for word in str(filters.get("banned") or "").split(",") if word.strip()]
    low = filters.get("price_min")
    high = filters.get("price_max")
    kept = []
    for item in goods:
        marks = set(item.get("badges") or [])
        if wanted and not wanted.issubset(marks):
            continue
        price = parse_price(item.get("price"))
        if low is not None and (price is None or price < low):
            continue
        if high is not None and (price is None or price > high):
            continue
        title = (item.get("title") or "").casefold()
        if not all(word in title for word in required_words):
            continue
        if any(word.casefold() in title for word in banned):
            continue
        kept.append(item)
    return kept
