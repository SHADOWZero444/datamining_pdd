"""可选命令行入口，复用桌面程序的抓取逻辑。"""
import argparse
import json
from pathlib import Path
from pdd.scraping.service import run_search
from pdd.storage.results import atomic_write


def main():
    parser = argparse.ArgumentParser(description="按搜索词抓取商品到指定 JSONL 文件")
    parser.add_argument("keyword", help="搜索词")
    parser.add_argument("--output", type=Path, required=True, help="结果文件路径（JSONL）")
    parser.add_argument("--limit", type=int, default=None, help="最多保存的符合条件商品条数")
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 10000:
        parser.error("limit 必须为 1 到 10000 的整数")
    if args.output.exists() or args.output.with_suffix(".run.json").exists():
        parser.error("输出文件已存在，请选择新文件名")
    rows = {}

    def save_batch(batch):
        rows.update((row["goods_id"], row) for row in batch)
        atomic_write(args.output, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows.values()))

    diagnostics = {}
    _, _, errors = run_search(args.keyword, limit=args.limit, on_batch=save_batch, diagnostics=diagnostics)
    atomic_write(args.output.with_suffix(".run.json"), json.dumps(diagnostics, ensure_ascii=False, indent=2))
    print(f"已保存 {len(rows)} 条：{args.output}")
    if errors:
        print("；".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
