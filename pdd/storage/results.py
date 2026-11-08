"""商品结果、筛选条件和增量结果缓存；所有文件操作共享可重入锁。"""
import json
import hashlib
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict, deque
from functools import lru_cache, wraps
from pathlib import Path
from uuid import uuid4
from pdd.paths import program_dir

DATA_DIR = program_dir() / "data" / "searches"
data_lock = threading.RLock()
result_feeds = OrderedDict()

def synchronized(fn):
    @wraps(fn)
    def locked(*args, **kwargs):
        with data_lock:
            return fn(*args, **kwargs)
    return locked

def safe_name(keyword):
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 40:
        raise ValueError("搜索词请填写 1 到 40 个字。")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", keyword).strip().rstrip(".") or "词条"
    # Windows 文件名不区分大小写，含英文或非法字符的搜索词使用唯一摘要。
    if cleaned != keyword or keyword.lower() != keyword.upper() or cleaned in (".", ".."):
        return cleaned[:32] + "--" + hashlib.sha256(keyword.encode("utf-8")).hexdigest()[:16]
    return cleaned


def result_stem(keyword):
    """兼容旧数据：仅在元数据明确属于该词条时复用旧文件名。"""
    name = safe_name(keyword)
    legacy = re.sub(r'[<>:"/\\|?*]', "_", keyword).strip()
    if name != legacy and legacy and not re.search(r'[\x00-\x1f]', legacy):
        metadata = DATA_DIR / f"{legacy}.meta.json"
        if metadata.is_file():
            try:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and saved.get("keyword") == keyword:
                    return legacy
            except (OSError, ValueError):
                pass
        old_result = DATA_DIR / f"{legacy}.jsonl"
        if old_result.is_file() and not metadata.exists():
            rows = read_jsonl(old_result)
            if rows and all(row.get("keyword") == keyword for row in rows):
                return legacy
    return name


def result_path(keyword):
    return DATA_DIR / f"{result_stem(keyword)}.jsonl"


def metadata_path(keyword):
    return DATA_DIR / f"{result_stem(keyword)}.meta.json"


def storage_path(keyword):
    """返回给界面显示的相对保存位置，避免泄露本机绝对目录。"""
    return (Path("data") / "searches" / f"{result_stem(keyword)}.jsonl").as_posix()


def search_metadata(keyword):
    defaults = {
        "keyword": keyword,
        "filters": {"tags": [], "required_words": "", "banned": "", "price_min": None, "price_max": None},
        "storage_path": storage_path(keyword),
    }
    path = metadata_path(keyword)
    if not path.exists():
        return defaults
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(saved, dict):
        return defaults
    if isinstance(saved.get("filters"), dict):
        defaults["filters"].update(saved["filters"])
    defaults["keyword"] = saved.get("keyword") or keyword
    return defaults


@synchronized
def save_search_metadata(keyword, filters):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "keyword": keyword,
        "filters": filters,
        "storage_path": storage_path(keyword),
        "saved_at": int(time.time()),
    }
    atomic_write(metadata_path(keyword), json.dumps(payload, ensure_ascii=False, indent=2))


def read_jsonl(path):
    with data_lock:
        if not path.exists():
            return []
        stat = path.stat()
        return [dict(row) for row in _read_jsonl_cached(str(path), stat.st_mtime_ns, stat.st_size)]


@lru_cache(maxsize=16)
def _read_jsonl_cached(filename, modified, size):
    path = Path(filename)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path, rows):
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    atomic_write(path, text + ("\n" if text else ""))


def atomic_write(path, text):
    """同目录替换，轮询和异常退出都不会读到只写了一半的文件。"""
    with data_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


def result_feed(keyword, revision=None):
    """返回变动商品；历史游标过期或应用重启时自动返回完整快照。"""
    with data_lock:
        rows = load_goods(keyword)
        current = {row["goods_id"]: row for row in rows if row.get("goods_id")}
        feed = result_feeds.get(keyword)
        if feed is None:
            feed = {"rows": current, "revision": uuid4().hex, "history": deque(maxlen=16)}
            result_feeds[keyword] = feed
        elif current != feed["rows"]:
            changed = [row for key, row in current.items() if feed["rows"].get(key) != row]
            removed = list(feed["rows"].keys() - current.keys())
            previous = feed["revision"]
            feed["revision"] = uuid4().hex
            # 单次大变动直接回退完整快照，避免在历史队列里重复保留大量数据。
            if len(changed) + len(removed) > 2000:
                feed["history"].clear()
            else:
                feed["history"].append((previous, changed, removed))
            feed["rows"] = current
        result_feeds.move_to_end(keyword)
        while len(result_feeds) > 8:
            result_feeds.popitem(last=False)
        payload = {"keyword": keyword, "revision": feed["revision"],
                   "metadata": search_metadata(keyword), "count": len(rows)}
        if revision == feed["revision"]:
            return {**payload, "unchanged": True}
        history = list(feed["history"])
        start = next((i for i, event in enumerate(history) if event[0] == revision), None)
        if start is not None:
            changes, removed = {}, set()
            for _, updated, deleted in history[start:]:
                for key in deleted:
                    changes.pop(key, None)
                    removed.add(key)
                for row in updated:
                    changes[row["goods_id"]] = row
                    removed.discard(row["goods_id"])
            return {**payload, "delta": True, "goods": list(changes.values()), "removed": list(removed)}
        return {**payload, "goods": rows}


def ensure_seq(rows):
    """给没有序号的商品补一个不重复的 ID。已有序号保持不变。"""
    used = [int(row["seq"]) for row in rows if str(row.get("seq", "")).isdigit()]
    next_id = max(used, default=0) + 1
    changed = False
    for row in rows:
        if not str(row.get("seq", "")).isdigit():
            row["seq"] = next_id
            next_id += 1
            changed = True
    return changed


@synchronized
def load_goods(keyword):
    rows = read_jsonl(result_path(keyword))
    if rows:
        if ensure_seq(rows):
            write_jsonl(result_path(keyword), rows)
        return rows
    return []

def list_keywords():
    found = {}
    if DATA_DIR.exists():
        for path in DATA_DIR.glob("*.jsonl"):
            rows = read_jsonl(path)
            keyword = rows[0].get("keyword") if rows else None
            if not keyword:
                meta = path.with_suffix(".meta.json")
                try:
                    saved = json.loads(meta.read_text(encoding="utf-8"))
                    keyword = saved.get("keyword") if isinstance(saved, dict) else None
                except (OSError, ValueError):
                    pass
            found[keyword or path.stem] = len(rows)
    return [{"keyword": name, "count": count} for name, count in found.items()]


@synchronized
def merge_goods(keyword, new_rows):
    """新结果并进旧文件。没有新商品时不覆盖，避免一次失败把历史清掉。"""
    old = read_jsonl(result_path(keyword))
    merged = {row.get("goods_id"): row for row in old if row.get("goods_id")}
    for row in new_rows:
        goods_id = row.get("goods_id")
        if not goods_id:
            continue
        previous = merged.get(goods_id, {})
        row["seq"] = previous.get("seq") or row.get("seq")
        if not row.get("shop_name"):
            row["shop_name"] = previous.get("shop_name") or ""
        merged[goods_id] = {**previous, **row}
    rows = list(merged.values())
    changed = rows != old
    assigned_seq = ensure_seq(rows)
    # 重复读取未变化的商品不再重写整个文件，减少续抓时的磁盘等待。
    if rows and (changed or assigned_seq):
        write_jsonl(result_path(keyword), rows)
    return rows
