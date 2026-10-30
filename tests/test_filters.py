"""关键词筛选回归；仅使用固定样本与临时目录。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdd.server import http as app
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping import filters as filter_tools, service
from pdd.scraping.filters import apply_filters


class KeywordFilterTests(unittest.TestCase):
    def test_blank_keeps_legacy_behavior(self):
        rows = [{"title": "蓝牙耳机"}, {"title": "二手耳机"}, {}]
        for value in (None, "", "  ", " ,， , "):
            with self.subTest(value=value):
                self.assertEqual(apply_filters(rows, {"required_words": value, "banned": "二手"}), [rows[0], rows[2]])

    def test_requires_every_word_in_title(self):
        rows = [{"title": "无线蓝牙降噪耳机"}, {"title": "蓝牙耳机"},
                {"title": "耳机", "shop": "蓝牙降噪", "keyword": "蓝牙降噪"},
                {"title": None}, {}]
        self.assertEqual(apply_filters(rows, {"required_words": " 蓝牙，降噪, ,蓝牙 "}), [rows[0]])

    def test_case_insensitive_literal_substrings(self):
        rows = [{"title": "USB-C 耳机 (Pro+)"}, {"title": "USB-C 耳机 Professional"}]
        self.assertEqual(apply_filters(rows, {"required_words": "usb-c,pro+"}), [rows[0]])

    def test_combines_with_existing_filters(self):
        good = {"title": "蓝牙降噪耳机", "price": "99", "badges": ["bybt", "official"]}
        rows = [good, {**good, "title": "二手蓝牙降噪耳机"}, {**good, "price": "200"},
                {**good, "price": "1"}, {**good, "badges": []}, {**good, "price": None}]
        filters = {"required_words": "蓝牙,降噪", "banned": "二手,翻新",
                   "price_min": 50, "price_max": 100, "tags": ["bybt", "official"]}
        self.assertEqual(apply_filters(rows, filters), [good])

    def test_conflicting_terms_match_nothing(self):
        self.assertEqual(apply_filters([{"title": "蓝牙耳机"}],
                                      {"required_words": "蓝牙", "banned": "蓝牙"}), [])

    def test_request_and_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(results, "DATA_DIR", Path(temp)):
            filters = filter_tools.read_filters({"required_words": " 蓝牙，降噪 ", "banned": "二手"})
            self.assertEqual(filters["required_words"], "蓝牙，降噪")
            results.save_search_metadata("耳机", filters)
            self.assertEqual(results.search_metadata("耳机")["filters"], filters)

    def test_old_requests_and_metadata_default_to_unrestricted(self):
        self.assertEqual(filter_tools.read_filters({})["required_words"], "")
        with tempfile.TemporaryDirectory() as temp, patch.object(results, "DATA_DIR", Path(temp)):
            self.assertEqual(results.search_metadata("耳机")["filters"]["required_words"], "")
            results.metadata_path("耳机").write_text(json.dumps({"filters": {"banned": "二手"}}), encoding="utf-8")
            filters = results.search_metadata("耳机")["filters"]
            self.assertEqual(filters["required_words"], "")
            self.assertEqual(filters["banned"], "二手")


if __name__ == "__main__":
    unittest.main()
