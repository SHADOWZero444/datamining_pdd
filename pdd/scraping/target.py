"""按商品 ID 计算累计目标；不读取文件或控制浏览器。"""
from .filters import apply_filters


class CollectionTarget:
    def __init__(self, limit=None, existing_goods=None, filters=None):
        rows = [row for row in (existing_goods or []) if row.get("goods_id")]
        self.limit = limit
        self.filters = filters
        self.known_ids = {str(row["goods_id"]) for row in rows}
        self.matched_ids = {str(row["goods_id"]) for row in apply_filters(rows, filters)}
        self.existing_matched = len(self.matched_ids)
        self.new_ids = set()

    @property
    def count(self):
        return len(self.matched_ids)

    @property
    def reached(self):
        return self.limit is not None and self.count >= self.limit

    def accept(self, row):
        key = str(row.get("goods_id") or "")
        if not key or not apply_filters([row], self.filters):
            return False
        if key not in self.matched_ids and self.reached:
            return False
        self.matched_ids.add(key)
        if key not in self.known_ids:
            self.new_ids.add(key)
        return True

    def snapshot(self):
        return {"target_count": self.limit, "existing_matched": self.existing_matched,
                "cumulative_matched": self.count, "new_goods": len(self.new_ids)}

    def description(self):
        target = f"/{self.limit}" if self.limit is not None else ""
        return f"累计符合条件 {self.count}{target} 条，本次新增 {len(self.new_ids)} 条"
