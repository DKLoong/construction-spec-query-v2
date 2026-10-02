"""手工回填一条 import_tasks 台账（用于持久化之前丢失、但 OCR 产物仍在磁盘的任务）。

默认**干跑**：只打印将要写入的内容，不碰数据库。加 --apply 才真正写入。

为什么需要：台账落库（2026-10-02）之前，任何 worker 重启都会清空台账，OCR 成果虽在
磁盘上却无入口可审查。本脚本为这类任务补一条处于 review_needed 的记录。

用法：
  D:/Python/python.exe scripts/backfill_import_task.py \\
      --task-id 411d4975 \\
      --md-path "data/outputs/411d4975/411d4975.md" \\
      --pdf-path "data/uploads/411d4975.pdf" \\
      --code "CJJ 2-2008" --title "城市桥梁工程施工与质量验收规范" --owner admin
  # 确认无误后追加 --apply
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

# 脚本直接以 `python scripts/xxx.py` 运行时 sys.path[0] 是 scripts/，项目根不在路径上
# （干跑分支不用 app.* 故不报错，--apply 一导入 app 就 ModuleNotFoundError）。
# 与 create_admin.py / migrate_*.py 同约定：显式把项目根插到最前。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 import_tasks 台账（默认干跑）")
    ap.add_argument("--task-id", required=True, help="8 位十六进制任务号")
    ap.add_argument("--md-path", required=True, help="OCR 产物 md 的路径")
    ap.add_argument("--pdf-path", required=True, help="原始上传文件路径")
    ap.add_argument("--code", required=True, help="规范编号（**必须准确**，它是规范身份）")
    ap.add_argument("--title", required=True, help="规范名称")
    ap.add_argument("--owner", default="admin", help="任务属主（取消/确认的鉴权用）")
    ap.add_argument("--apply", action="store_true", help="真正写入（缺省只干跑）")
    args = ap.parse_args()

    if not re.fullmatch(r"[0-9a-f]{8}", args.task_id):
        print(f"❌ 任务号必须是 8 位十六进制小写：{args.task_id!r}")
        return 2
    md_path, pdf_path = Path(args.md_path), Path(args.pdf_path)
    for p in (md_path, pdf_path):
        if not p.is_file():
            print(f"❌ 文件不存在：{p}")
            return 2

    md_text = md_path.read_text(encoding="utf-8", errors="replace")
    payload = {
        "status": "review_needed",
        "progress": 50,
        "message": "OCR 完成，请审查识别结果",
        "md_text": md_text,
        "title": args.title,
        "code": args.code,
        "file_path": str(pdf_path),
        "file_name": pdf_path.name,
        "file_hash": _sha256(pdf_path),
        "spec_status": "现行",
        "replaced_by_code": "",
    }
    print(f"任务号 {args.task_id}｜正文 {len(md_text)} 字符｜原文件 {pdf_path.name}")
    for k, v in payload.items():
        print(f"  {k} = {f'{len(v)} 字符' if k == 'md_text' else v}")

    if not args.apply:
        print("\n（干跑）确认无误后加 --apply 写入。")
        return 0

    from app.database import init_db
    from app.routes.import_routes import create_task, _get_task, _update_task
    init_db()
    if _get_task(args.task_id) is not None:
        print(f"❌ 台账里已存在 {args.task_id}，不覆盖")
        return 1
    create_task(args.task_id, owner=args.owner)
    _update_task(args.task_id, **payload)

    from app.logging_util import json_detail, log_action
    log_action("import", "WARN", "手工回填导入台账",
               detail=json_detail({"task_id": args.task_id, "code": args.code,
                                   "md_chars": len(md_text),
                                   "by": "backfill_import_task.py"}),
               username=args.owner)
    task = _get_task(args.task_id)
    if task is None:  # 刚写完就查不到 = 落库异常，不能报成功
        print(f"❌ 已提交但读不到 {args.task_id}，请手工核查")
        return 1
    print(f"✅ 已写入：status={task['status']} updated_at={task['updated_at']:.0f}")
    print(f"   审查页：/import/review/{args.task_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
