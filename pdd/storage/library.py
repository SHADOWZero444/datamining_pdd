"""词条分组和导航库；不依赖 HTTP 或抓取任务实现。"""
import json
from uuid import uuid4
from pdd.paths import program_dir
from . import results

LIBRARY_FILE = program_dir() / "data" / "library.json"

@results.synchronized
def load_library():
    if LIBRARY_FILE.exists():
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError("词条目录文件损坏，请从备份恢复；原文件已保留。") from None
    else:
        data = {"groups": [], "entries": []}
    if not isinstance(data, dict) or any(not isinstance(data.get(key, []), list) or any(not isinstance(item, dict) for item in data.get(key, [])) for key in ("groups", "entries")):
        raise ValueError("词条目录格式无效；原文件已保留。")
    data.setdefault("groups", [])
    data.setdefault("entries", [])
    known = {item.get("keyword") for item in data["entries"]}
    for item in results.list_keywords():
        keyword = item["keyword"]
        if keyword not in known:
            data["entries"].append({"keyword": keyword, "title": keyword, "pinned": False, "groupId": ""})
            known.add(keyword)
    return data


@results.synchronized
def save_library(data):
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    results.atomic_write(LIBRARY_FILE, json.dumps(data, ensure_ascii=False, indent=2))


@results.synchronized
def library_state():
    library = load_library()
    entries = []
    for entry in library["entries"]:
        keyword = entry.get("keyword") or ""
        if not keyword:
            continue
        entries.append({
            "keyword": keyword,
            "title": entry.get("title") or keyword,
            "pinned": bool(entry.get("pinned")),
            "groupId": entry.get("groupId") or "",
            "count": len(results.read_jsonl(results.result_path(keyword))),
        })
    entries.sort(key=lambda item: (not item["pinned"], item["title"]))
    return {"groups": library["groups"], "entries": entries}


@results.synchronized
def update_library(payload, running_keyword=None):
    library = load_library()
    for field in ("keyword", "groupId", "name", "title", "action"):
        if field in payload and not isinstance(payload[field], str):
            raise ValueError("词条操作参数无效。")
    keywords = payload.get("keywords", [])
    if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
        raise ValueError("请选择有效的词条。")
    for keyword in keywords:
        results.safe_name(keyword)
    action = payload.get("action")
    if action == "create-group":
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RuntimeError("请填写分组名称。")
        library["groups"].append({"id": "g" + uuid4().hex, "name": name, "open": True, "pinned": False})
    elif action == "toggle-group":
        group = next((item for item in library["groups"] if item["id"] == payload.get("groupId")), None)
        if group:
            group["open"] = not group.get("open", True)
    elif action in ("rename-group", "pin-group", "delete-group"):
        group = next((item for item in library["groups"] if item["id"] == payload.get("groupId")), None)
        if not group:
            raise RuntimeError("没有找到这个分组。")
        if action == "rename-group":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise RuntimeError("请填写新的分组名称。")
            group["name"] = name
        elif action == "pin-group":
            group["pinned"] = not group.get("pinned", False)
        else:
            for entry in library["entries"]:
                if entry.get("groupId") == group["id"]:
                    entry["groupId"] = ""
            library["groups"] = [item for item in library["groups"] if item.get("id") != group["id"]]
    elif action == "rename":
        entry = next((item for item in library["entries"] if item.get("keyword") == payload.get("keyword")), None)
        title = str(payload.get("title") or "").strip()
        if not entry or not title:
            raise RuntimeError("请填写新的标题。")
        entry["title"] = title
    elif action == "pin":
        entry = next((item for item in library["entries"] if item.get("keyword") == payload.get("keyword")), None)
        if entry:
            entry["pinned"] = not entry.get("pinned")
    elif action == "move":
        keywords = payload.get("keywords") or ([payload.get("keyword")] if payload.get("keyword") else [])
        for entry in library["entries"]:
            if entry.get("keyword") in keywords:
                entry["groupId"] = payload.get("groupId") or ""
    elif action == "delete":
        keywords = set(payload.get("keywords") or [])
        if running_keyword in keywords:
            raise RuntimeError("请先停止这个词条的抓取，再删除数据。")
        library["entries"] = [item for item in library["entries"] if item.get("keyword") not in keywords]
        for keyword in keywords:
            path = results.result_path(keyword)
            if path.exists():
                path.unlink()
            meta = results.metadata_path(keyword)
            if meta.exists():
                meta.unlink()
            path.with_suffix(".run.json").unlink(missing_ok=True)
    else:
        raise RuntimeError("无法处理这个操作。")
    save_library(library)
    return {"ok": True, "library": library_state()}


@results.synchronized
def ensure_library_entry(keyword):
    library = load_library()
    if not any(item.get("keyword") == keyword for item in library["entries"]):
        library["entries"].append({"keyword": keyword, "title": keyword, "pinned": False, "groupId": ""})
        save_library(library)


@results.synchronized
def remove_library_entries(keywords):
    library = load_library()
    removed = set(keywords)
    library["entries"] = [item for item in library["entries"] if item.get("keyword") not in removed]
    save_library(library)
