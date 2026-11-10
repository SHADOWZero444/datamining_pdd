"""详情队列与限速边界：合成记录，不加载用户样本或启动浏览器。"""
import unittest
from unittest.mock import Mock, patch

from pdd.scraping import details, service
from pdd.scraping.parsing import normalize_model
from pdd.storage import results


class DetailTests(unittest.TestCase):
    def test_old_detail_sales_are_not_displayed_as_search_sales(self):
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value='{"sales":"旧值","detail":{"sales":"旧值","shop_name":"甲"}}\n'
                   '{"sales":"搜索原文"}'):
            old, original = results._read_jsonl_cached.__wrapped__("unused", 0, 0)
        self.assertNotIn("sales", old)
        self.assertEqual(old["detail"], {"shop_name": "甲"})
        self.assertEqual(original["sales"], "搜索原文")

    def test_search_api_sales_preserved_with_details(self):
        row = normalize_model({"goods_id": 1, "goods_name": "商品", "sales_tip": "已拼2.3万+"})
        self.assertEqual(row["sales"], "已拼2.3万+")
        with patch.object(results, "read_jsonl", return_value=[{"goods_id":"1","seq":1,"detail":{"shop_name":"甲"}}]), \
             patch.object(results, "write_jsonl"):
            saved = results.merge_goods("测试", [row, {"goods_id":"1","sales":"","detail":{"images":["a"]}}])
        self.assertEqual(saved[0]["sales"], "已拼2.3万+")
        self.assertEqual(saved[0]["detail"], {"shop_name":"甲","images":["a"]})

    def test_partial_fields_keep_previously_verified_values(self):
        row = {"goods_id": "1", "sales": "已拼1000件", "detail": {"shop_name": "旧店", "images": ["https://example.org/1"]}}
        save = Mock()
        fields = {"shop_name": "", "images": ["https://example.org/1"]}
        with patch.object(details, "read_one", return_value=(fields, "", 0)):
            stats = {}
            details.enrich(Mock(), [row], Mock(), stop=lambda: False, save=save, report=Mock(),
                           stats=stats, intervention=None, resume=None, view=None)
        self.assertEqual(row["shop_name"], "旧店")
        self.assertEqual(row["sales"], "已拼1000件")
        self.assertEqual(len(row["detail"]["images"]), 1)
        self.assertEqual(stats["detail_complete"], 0)
        save.assert_called_once_with([row])

    def test_long_detail_cooldown_stops_before_next_goods(self):
        with patch.object(details, "read_one", return_value=({}, "rate_limited", 600)) as read, \
             patch.object(details, "remember_cooldown") as cooldown:
            stats = {}
            details.enrich(Mock(), [{"goods_id": "1"}, {"goods_id": "2"}], Mock(), stop=lambda: False,
                           save=None, report=Mock(), stats=stats, intervention=None, resume=None, view=None)
            read.assert_called_once()
            cooldown.assert_called_once_with(600)
        self.assertEqual(stats["detail_stop_reason"], "rate_limited")

    def test_reached_target_only_backfills_missing_ids(self):
        old = [{"goods_id": "1"}, {"goods_id": "2", "detail": {"shop_name": "乙", "images": ["a"]}}]
        stats = {}
        with patch.object(service, "remaining_cooldown", return_value=0), \
             patch.object(service, "capture_session"), patch.object(service, "find_cookie_file"), \
             patch.object(service, "collect") as search, patch.object(service, "enrich") as enrich:
            def fill(page, rows, pacer, **kwargs):
                self.assertEqual([row["goods_id"] for row in rows], ["1"])
                kwargs["stats"].update(detail_total=1, detail_complete=1)
            enrich.side_effect = fill
            def finish_first_page(*args, detail_step, **kwargs):
                rows = {}
                while detail_step(rows, lambda: True, True):
                    pass
                stats.update(stop_reason="limit")
                return list(rows.values()), None
            search.side_effect = finish_first_page
            service.run_search("测试", 2, existing_goods=old, diagnostics=stats)
            search.assert_called_once()
            enrich.assert_called_once()
