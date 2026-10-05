"""回填既有 `layout.json`：**重锚行区间** → 再补条文号。

**取代** `backfill_layout_clause_no.py`（那个只补条号；本脚本把「重锚」也做了，
顺序不可颠倒 —— `clause_no` 是按 `start_line` 推出来的，不先把区间重锚对，条号也跟着错）。

**为什么需要它**：改 `anchor_blocks` 只影响**新导入**。存量 `layout.json` 是旧代码写的，
两类缺口都可能是旧的：
  1. **表格块未锚定**（2026-10-05 修）：md 侧是带样式、跨多行的 HTML，块侧是去样式单行的
     `content` ⇒ 逐行比对必然失败 ⇒ `start_line` 为 None（既不能高亮、也没有写回路径）。
     新增「按格内文字签名」的兜底后，新导入能锚上，**存量要靠本脚本**。
  2. **缺 `clause_no`**（P2.5 Task 1 接线前导入的）。

**安全护栏**（三条，缺一不可）：
  1. **指纹校验**：`base_sha256(md) == layout.json 的 base_sha256`，不一致就跳过 ——
     md 变了说明不是同一次锚定，**宁可留空也不写错**。
  2. **幂等**：重跑结果相同（`anchor_blocks` 是纯函数，输入不变则输出不变），可放心重复运行。
  3. 默认 **dry-run**，只有 `--apply` 才写盘。

**不碰数据库、不重跑 OCR**（重跑会把 OCR 差异与解析差异混在一起，无法归因 —— 批一硬要求）。

用法：
    D:/Python/python.exe scripts/backfill_layout.py            # 只打印计划
    D:/Python/python.exe scripts/backfill_layout.py --apply    # 真正写回
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.config import OUTPUT_DIR  # noqa: E402
from app.database import get_db  # noqa: E402
from app.ocr.layout import align_clauses, anchor_blocks, base_sha256, save_layout  # noqa: E402


def _md_of(task_id: str) -> str | None:
    """取该任务的 md（与审查页 `/preview` 同源：`import_tasks.md_text`）"""
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

    todo = skipped = written = 0
    for out in sorted(p for p in root.iterdir() if p.is_dir()):
        lay_path = out / "layout.json"
        if not lay_path.is_file():
            continue
        layout = json.loads(lay_path.read_text(encoding="utf-8"))
        blocks = layout.get("blocks") or []
        if not blocks:
            continue

        md = _md_of(out.name)
        if not md:
            print(f"任务 {out.name}：库里没有 md_text ⇒ 跳过")
            skipped += 1
            continue
        if base_sha256(md) != layout.get("base_sha256"):
            print(f"任务 {out.name}：⚠ 指纹不匹配（md 与 layout.json 非同一次锚定）⇒ 跳过")
            skipped += 1
            continue

        before_a = sum(1 for b in blocks if b.get("start_line") is not None)
        anchored, astats = anchor_blocks(md, blocks)
        aligned, cstats = align_clauses(md, anchored)
        after_a = astats["anchored"]

        print(f"任务 {out.name}：{len(blocks)} 块")
        print(f"  锚定 {before_a} → {after_a}（missed {astats['missed']}，"
              f"stale {astats['stale_anchors']}）")
        print(f"  条文号 标注 {cstats['labeled']} / 未标注 {cstats['unlabeled']}，"
              f"涉及 {cstats['distinct_clauses']} 个条号")
        todo += 1
        if apply:
            merged = {
                **{k: v for k, v in (layout.get("stats") or {}).items()
                   if not k.startswith("clause_")},
                **{k: v for k, v in astats.items() if k != "residual_miss"},
                "clause_blocks": cstats["blocks"],
                "clause_labeled": cstats["labeled"],
                "clause_unlabeled": cstats["unlabeled"],
                "clause_distinct": cstats["distinct_clauses"],
            }
            save_layout({**layout, "blocks": aligned, "stats": merged}, out)
            print("  ✅ 已写回 layout.json")
            written += 1

    print()
    if apply:
        print(f"完成：写回 {written} 个任务，跳过 {skipped} 个")
    else:
        print(f"dry-run：将写回 {todo} 个任务，跳过 {skipped} 个（加 --apply 才写盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
