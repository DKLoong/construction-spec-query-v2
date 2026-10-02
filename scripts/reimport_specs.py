"""按「同一份 md」重导已入库规范（解析器改动后的库级验收）

**为什么需要它**：导入路径是「新增」语义（无 DELETE、specifications 也无 code 唯一约束），
直接重导会让库里并排出现两份同名规范。故重导 = 先按应用自身的删除路径清掉旧行
（清向量 → 清替代引用 → CASCADE 删条文/FTS），再喂同一份 md 重建。

**必须喂 md、不得重跑 PDF 的 OCR**（批一经验的硬要求）：OCR 结果不确定，重跑会引入
新的文本差异，使「解析器改动带来的差异」与「OCR 差异」混在一起、无法归因。md 的
来源按 ① `data/outputs/{task_id}/{task_id}.md` ② `data/uploads/{task_id}.md` 依次探。

用法：
    D:/Python/python.exe scripts/reimport_specs.py 27 28 30            # 只打印计划（dry-run）
    D:/Python/python.exe scripts/reimport_specs.py 27 28 30 --apply    # 真正删除并重导

⚠️ 破坏性：`--apply` 会删除目标规范的全部条文与向量。执行前请自行备份
`data/spec_query.db` 与 `lance_db/`。
"""
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.config import OUTPUT_DIR, UPLOAD_DIR  # noqa: E402
from app.database import get_db  # noqa: E402
from app.parser.md_parser import parse_markdown  # noqa: E402
from app.parser.ocr_clean import clean_ocr_text  # noqa: E402
from app.routes.import_routes import _filter_cover_clauses  # noqa: E402


def _read_md(md_path: Path) -> str:
    """读 md 并**照导入路径前置清洗**。

    ⚠️ `_process_import_phase2` **不负责清洗** —— `clean_ocr_text` 在三条导入路径上
    都于 phase 2 之前调用（`_process_import` 的 .md/.pdf 两支 + OCR 支）。本脚本直接
    调 phase 2，故必须自己补这一步；漏掉会把页码行/纯数字行/OCR 失败标记/重复页眉
    一并灌进条文 content（已实测踩过：GB 50086-2015 会多出 5 条、条文本内容带脏行）。
    """
    return clean_ocr_text(md_path.read_text(encoding="utf-8"))


def _expected_count(md_text: str) -> int:
    """按导入路径同一套变换算预期条数，供导入后自校验（口径：夹具级）"""
    return len(_filter_cover_clauses(parse_markdown(md_text)))


def _find_md(task_id: str) -> Path | None:
    """按既定来源顺序找该任务的 md"""
    for cand in (Path(OUTPUT_DIR) / task_id / f"{task_id}.md",
                 Path(UPLOAD_DIR) / f"{task_id}.md"):
        if cand.is_file():
            return cand
    return None


def _plan(spec: dict) -> tuple[Path | None, str]:
    """→ (md 路径, task_id)；task_id 取自 source_path 的文件名主干"""
    task_id = Path(spec["source_path"]).stem if spec["source_path"] else ""
    return _find_md(task_id), task_id


def _delete_spec(spec_id: int) -> int:
    """按 `spec_routes.delete_spec` 的顺序删除：清向量 → 清替代引用 → 删规范行。

    返回删除的条文数。**不调用 `_cleanup_task_artifacts`**（它只挂在 HTTP 收尾路径上），
    故 OCR 目录与原始 md 不会被误删。
    """
    from app.search.vector_search import VectorStore

    with get_db() as conn:
        clause_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM clauses WHERE spec_id = ?", (spec_id,))]

    try:
        vs = VectorStore()
        for cid in clause_ids:
            try:
                vs.delete_clause(cid)
            except Exception:
                pass
    except Exception as e:                      # 表不存在等情况：不阻断删除
        print(f"    [WARN] 向量清理失败（可能表不存在）: {e}")

    with get_db() as conn:
        conn.execute("UPDATE specifications SET replace_by_spec_id = NULL "
                     "WHERE replace_by_spec_id = ?", (spec_id,))
        conn.execute("DELETE FROM specifications WHERE id = ?", (spec_id,))
    return len(clause_ids)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2

    with get_db() as conn:
        specs = [dict(r) for r in conn.execute(
            "SELECT id, code, title, status, source_path, file_hash, clause_count, "
            "replaced_by_code FROM specifications WHERE id IN (%s) ORDER BY id"
            % ",".join("?" * len(args)), [int(a) for a in args])]

    if len(specs) != len(args):
        print(f"只找到 {len(specs)} 份规范（要 {len(args)} 份），请核对 id")
        return 2

    for spec in specs:
        md_path, task_id = _plan(spec)
        print(f"spec {spec['id']} {spec['code']}（现行 {spec['clause_count']} 条）")
        print(f"  source_path = {spec['source_path']}")
        if md_path is None:
            print("  md          = ❌ 未找到（不得重跑 OCR）")
            return 2
        print(f"  md          = {md_path}")
        print(f"  重导预期条数 = {_expected_count(_read_md(md_path))}（清洗后口径）")

    if not apply:
        print("\n（dry-run：加 --apply 才会删除并重导）")
        return 0

    for spec in specs:
        md_path, task_id = _plan(spec)
        if md_path is None:                     # 上面已统一校验过；此处仅为类型收窄
            print(f"spec {spec['id']} 的 md 未找到，跳过")
            continue
        print(f"\n=== 重导 spec {spec['id']} {spec['code']} ===")
        deleted = _delete_spec(spec["id"])
        print(f"  已删除旧规范行与 {deleted} 条条文")

        import app.routes.import_routes as ir
        ir.create_task(task_id)
        ir._update_task(task_id, status="processing", progress=0)
        md_text = _read_md(md_path)
        expected = _expected_count(md_text)
        ir._process_import_phase2(
            task_id, md_text, spec["title"], spec["code"],
            spec["source_path"], spec["file_hash"] or "",
            spec["status"] or "现行", spec["replaced_by_code"] or "",
        )
        state = ir._get_task(task_id) or {}
        print(f"  进度: {state.get('status')} / {state.get('message')}")

        # 自校验：库内条数必须等于导入前算出的预期值。**只信库、不信进度文案**
        # ——本脚本第一版漏了 clean_ocr_text，进度文案照样报「done」，是库内条数
        # 与预期不符才暴露的。
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, clause_count FROM specifications WHERE code = ? AND status != '已删除' "
                "ORDER BY id DESC LIMIT 1", (spec["code"],)).fetchone()
        got = row["clause_count"] if row else -1
        flag = "✓" if got == expected else "✗"
        print(f"  {flag} 库内条数 {got} / 预期 {expected}（spec_id={row['id'] if row else '?'}）")

    print("\n=== 重导后库内状态 ===")
    with get_db() as conn:
        for r in conn.execute("SELECT id, code, clause_count, status FROM specifications ORDER BY id"):
            print("  ", dict(r))
        print("   clauses 总数:", conn.execute("SELECT COUNT(*) FROM clauses").fetchone()[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
