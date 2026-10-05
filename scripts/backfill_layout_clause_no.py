"""把条文号回填进**既有** `layout.json`（P2.5 的 `finalize_layout` 接线之前导入的任务）。

**为什么需要它**：`app/ocr/layout.py` 的 `finalize_layout` 直到 P2.5 才接线 `align_clauses`
⇒ 在那之前写的 `layout.json` 里，块**没有 `clause_no` 字段**。而审查页的块标题栏与
「点条号跳转」都读 `/layout` 端点下发的这份数据 ⇒ 老任务的条号恒为空、点击恒不跳转。

⚠️ **重导数据库条文（`scripts/reimport_specs.py`）解决不了这个问题** —— 那条路重建的是
`clauses` 表，而 `layout.json` 是 **OCR 阶段**由 `finalize_layout` 落盘的，两者不是一回事。

**做法**：用该任务**同一份 md** 重算 `align_clauses(md, blocks)` 并写回 `layout.json`。
不碰数据库、**不重跑 OCR**（重跑会把 OCR 差异与解析差异混在一起，无法归因 —— 批一硬要求）。

**安全护栏**：
  1. 先校验 `base_sha256(md) == layout.json 的 base_sha256`；**不一致就跳过**（说明 md 变了，
     此时对齐结果不可信，宁可留空也不写错）。
  2. 块里**已经有** `clause_no` 字段的一律跳过（幂等，可重复运行）。
  3. 默认 **dry-run**，只有 `--apply` 才写盘。

用法：
    D:/Python/python.exe scripts/backfill_layout_clause_no.py            # 只打印计划
    D:/Python/python.exe scripts/backfill_layout_clause_no.py --apply    # 真正写回
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.config import OUTPUT_DIR  # noqa: E402
from app.database import get_db  # noqa: E402
from app.ocr.layout import align_clauses, base_sha256, save_layout  # noqa: E402


def _md_of(task_id: str) -> str | None:
    """取该任务的 md（与审查页 `/preview` 用的是同一来源：`import_tasks.md_text`）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT md_text FROM import_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    return (row["md_text"] if row else None) or None


def main() -> int:
    apply = "--apply" in sys.argv
    root = Path(OUTPUT_DIR)
    if not root.is_dir():
        print(f"没有 {root} 目录")
        return 2

    todo = skipped = done = 0
    for out in sorted(p for p in root.iterdir() if p.is_dir()):
        lay_path = out / "layout.json"
        if not lay_path.is_file():
            continue
        layout = json.loads(lay_path.read_text(encoding="utf-8"))
        blocks = layout.get("blocks") or []
        if not blocks:
            continue

        print(f"任务 {out.name}：{len(blocks)} 个块")
        if "clause_no" in blocks[0]:
            print("  已有 clause_no 字段 ⇒ 跳过（幂等）")
            skipped += 1
            continue

        md = _md_of(out.name)
        if not md:
            print("  库里没有该任务的 md_text ⇒ 跳过")
            skipped += 1
            continue
        if base_sha256(md) != layout.get("base_sha256"):
            print("  ⚠ 指纹不匹配（md 与 layout.json 不是同一次锚定）⇒ 跳过，不写错")
            skipped += 1
            continue

        aligned, stats = align_clauses(md, blocks)
        merged_stats = {
            **{k: v for k, v in (layout.get("stats") or {}).items()
               if not k.startswith("clause_")},
            "clause_blocks": stats["blocks"],
            "clause_labeled": stats["labeled"],
            "clause_unlabeled": stats["unlabeled"],
            "clause_distinct": stats["distinct_clauses"],
        }
        print(f"  可对齐：标注 {stats['labeled']} / 未标注 {stats['unlabeled']}，"
              f"涉及条文号 {stats['distinct_clauses']} 个")
        todo += 1
        if apply:
            save_layout({**layout, "blocks": aligned, "stats": merged_stats}, out)
            print("  ✅ 已写回 layout.json")
            done += 1

    print()
    if apply:
        print(f"完成：写回 {done} 个任务，跳过 {skipped} 个")
    else:
        print(f"dry-run：将写回 {todo} 个任务，跳过 {skipped} 个（加 --apply 才写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
