"""存量 FTS5 + jieba 预分词迁移（幂等，可重跑）

背景：FTS5 表由 external content 改为独立 fts5(search_text)，检索切换为
jieba 预分词 + bm25。存量库的 clauses 缺 search_text 列、clauses_fts 仍是
旧 external 结构。本脚本通过 init_db() 触发幂等迁移：
1. 加 search_text 列
2. drop 旧 external FTS 表，重建独立 fts5(search_text)
3. backfill 存量条文生成 search_text 并回填 FTS（先 DELETE 再 INSERT，避免 rowid 冲突）

服务启动时 init_db() 也会自动执行同样迁移；本脚本用于显式触发/查看统计。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, get_db


def main():
    init_db()  # 幂等迁移（加列 + FTS 重建 + backfill）
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE search_text IS NOT NULL AND search_text != ''"
        ).fetchone()[0]
        fts_n = conn.execute("SELECT COUNT(*) FROM clauses_fts").fetchone()[0]
    print(f"迁移完成：{n} 条条文已生成 search_text，FTS 表共 {fts_n} 行。")
    print("提示：检索已切换为 jieba 预分词 + bm25；服务启动时 init_db 也会自动执行此迁移。")


if __name__ == "__main__":
    main()
