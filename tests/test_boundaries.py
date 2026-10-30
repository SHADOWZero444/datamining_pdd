"""词条存储与任务失败回归；使用假凭证与临时数据。"""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pdd.services import auth, jobs
from pdd.storage import library, results
from pdd.scraping.filters import read_filters
from pdd.scraping.parsing import goods_from_api


class DataBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(results, "DATA_DIR", self.root / "searches"),
                        patch.object(library, "LIBRARY_FILE", self.root / "library.json"),
                        patch.object(jobs, "job", {"running": False}),
                        patch.object(auth, "auth", {"running": False})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_unique_windows_safe_filenames(self):
        terms = ["a/b", "a\\b", "a_b", "ABC", "abc", "CON", "con", "耳机.", "耳机", "..", "a\x00b", "Ä", "ä", "Ａ", "ａ"]
        names = [results.safe_name(term) for term in terms]
        self.assertEqual(len({name.casefold() for name in names}), len(terms))
        for term in terms:
            results.save_search_metadata(term, {})
            results.merge_goods(term, [{"goods_id": "1", "keyword": term, "title": term}])
        self.assertEqual(len(list(results.DATA_DIR.glob("*.jsonl"))), len(terms))

    def test_legacy_filename_requires_matching_owner(self):
        results.DATA_DIR.mkdir()
        (results.DATA_DIR / "a_b.meta.json").write_text(json.dumps({"keyword": "a/b", "filters": {}}), encoding="utf-8")
        (results.DATA_DIR / "a_b.jsonl").write_text('{"goods_id":"1","keyword":"a/b"}\n', encoding="utf-8")
        self.assertEqual(results.result_path("a/b").name, "a_b.jsonl")
        self.assertNotEqual(results.result_path("a\\b"), results.result_path("a/b"))
        self.assertNotEqual(results.result_path("a_b"), results.result_path("a/b"))

    def test_concurrent_library_updates_are_atomic_and_unique(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: library.update_library({"action": "create-group", "name": str(i)}), range(30)))
        groups = library.load_library()["groups"]
        self.assertEqual(len(groups), 30)
        self.assertEqual(len({group["id"] for group in groups}), 30)

    def test_empty_result_keeps_original_long_keyword(self):
        keyword = "abcdefghijklmnopqrstuvwxyz"
        results.save_search_metadata(keyword, {})
        results.write_jsonl(results.result_path(keyword), [])
        entries = library.library_state()["entries"]
        self.assertEqual([(item["keyword"], item["count"]) for item in entries], [(keyword, 0)])

    def test_initialization_failure_does_not_occupy_job_slot(self):
        for module, name in ((results, "save_search_metadata"), (library, "ensure_library_entry")):
            with self.subTest(name=name), patch.object(module, name, side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    jobs.start_job("测试", 1, {})
                self.assertFalse(jobs.job["running"])

    def test_filter_boundary_rejects_invalid_values(self):
        for payload in ({"price_min": "nan"}, {"price_max": "inf"}, {"price_min": -1},
                        {"price_min": 2, "price_max": 1}, {"tags": "bybt"}, {"required_words": []}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                read_filters(payload)

    def test_disk_failure_still_releases_running_job(self):
        jobs.job.update(running=True, saved=3)
        with patch('pdd.scraping.service.run_search', side_effect=OSError('fixture')), \
                patch.object(results, 'read_jsonl', side_effect=OSError('fixture')), \
                patch.object(results, 'atomic_write', side_effect=OSError('fixture')), \
                patch.object(jobs, 'job_capture_view', None):
            jobs.run_job('测试', 1, {}, threading.Event())
        self.assertFalse(jobs.job['running'])
        self.assertEqual(jobs.job['status'], 'partial')
        self.assertEqual(jobs.job['count'], 3)

    def test_parser_keyword_is_per_call(self):
        data = {"items": [{"item_data": {"goods_model": {"goods_id": 1, "goods_name": "商品"}}}]}
        first = goods_from_api(data, "词条甲")
        second = goods_from_api(data, "词条乙")
        self.assertEqual(first[0]["keyword"], "词条甲")
        self.assertEqual(second[0]["keyword"], "词条乙")


if __name__ == "__main__":
    unittest.main()
