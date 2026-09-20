"""清理词库同词跨组冲突（一次性数据迁移；幂等；先备份后执行，失败自动回滚）

**批准依据**：`docs/lexicon-conflict-review-2026-09-19.md` 经用户逐簇过审
（2026-09-20），裁决为：

- **37 簇机械清理**：SAME_SET（同词汇、方向相反）与 CHAIN（同 canonical、
  variants 不同）→ 保留 `alias` 侧（规范词方向符合 spec）、variants 取并集、删冗余行
- **5 簇人工合并**：canonical 由裁决指定（见 `APPROVED_MERGES`）
- **安全带**：从「安全带」组**移除**「安全绳」——二者在规范里是两个不同产品
  （坠落悬挂用安全带 vs 分立的绳，配套但不等价），合并会造成语义错误
- **石子**：**删除** canonical=石子 且 variants 含碎石 的那一行——碎石/卵石 是
  粗骨料 的**子类**，把子类当同义词是错的

**背景**：读侧 `store._check_equiv_unique` 一旦发现同词跨组即丢弃**整张表**
（fail-closed）。2026-09-06 一批 alias CSV 导入造成 76 个冲突词面 / 44 个冲突簇，
使 720 条 active 词条（509 等价 + 211 confusable）全部失效 13 天。写入侧校验已
补（见 store.find_equiv_conflict），本迁移负责清理存量。

用法：`PYTHONUTF8=1 D:/Python/python.exe scripts/migrate_lexicon_conflicts.py`
"""

import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB = "data/spec_query.db"
EQUIV = ("synonym", "alias")

# 人工裁决：探测词 → 簇级动作。**必须作用于整簇**——早先把它写成"簇处理前的
# 专项操作"是错的：专项一旦失效（如 SELECT 无 ORDER BY 选中了另一行），
# 通用合并就会把该簇按"机械并集"处理，把不该等价的词并进去（2026-09-20 实际发生）。
#   ("merge", canonical)               整簇并成一组
#   ("split", {canonical: [variants]}) 整簇拆成指定几组（上下位/不同产品被误当同义）
#   ("drop_group", (canonical, 触发词)) 删除该行，簇内其余行原样保留
_APPROVED = {
    "天花板": ("merge", "顶棚"),
    "混凝土浇筑": ("merge", "混凝土浇筑"),
    "承包单位": ("merge", "施工单位"),
    "卷帘门": ("merge", "防火卷帘"),
    "满堂架": ("merge", "满堂脚手架"),
    # 安全带与安全绳在规范里是两个不同产品（坠落悬挂安全带 vs 分立的绳），不得等价
    "安全带": ("split", {"安全带": ["保险带"], "安全绳": ["生命绳"]}),
    # 石子=碎石,卵石 是把子类当同义词：删该行，粗骨料/碎石 各自成组
    "石子": ("drop_group", ("石子", "碎石")),
}


def words_of(row) -> set[str]:
    return {row["canonical"]} | {
        v.strip() for v in (row["variants"] or "").split(",") if v.strip()}


def _load_equiv(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, canonical, variants FROM lexicon_entries "
        "WHERE kind IN (?, ?) AND is_active = 1 ORDER BY id", EQUIV)]


def _clusters(rows):
    """同词跨组 → 并查集连通分量（同 report_lexicon_conflicts 的口径）"""
    parent: dict[int, int] = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    owner = defaultdict(list)
    for r in rows:
        for w in sorted(words_of(r)):
            owner[w].append(r["id"])
    for ids in owner.values():
        for i in ids[1:]:
            union(ids[0], i)

    groups = defaultdict(list)
    for r in rows:
        groups[find(r["id"])].append(r)
    out = []
    for members in groups.values():
        seen = defaultdict(set)
        for r in members:
            for w in words_of(r):
                seen[w].add(r["id"])
        if any(len(v) > 1 for v in seen.values()):
            out.append(members)
    return out


def _merge(conn, members, canonical: str, stats: dict) -> None:
    """把一簇并成一行：canonical 用指定值，variants 收全部同义面，删掉其余行"""
    # 保留优先级：canonical 已等于目标值 > alias > id 小
    def rank(r):
        return (0 if r["canonical"] == canonical else 1,
                0 if r["kind"] == "alias" else 1, r["id"])

    members = sorted(members, key=rank)
    keep, drop = members[0], members[1:]
    variants = sorted({w for r in members for w in words_of(r)} - {canonical})
    kind = "alias" if canonical != keep["canonical"] or keep["kind"] == "alias" else keep["kind"]

    conn.execute(
        "UPDATE lexicon_entries SET kind=?, canonical=?, variants=?, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (kind, canonical, ",".join(variants), keep["id"]))
    for r in drop:
        conn.execute("DELETE FROM lexicon_entries WHERE id=?", (r["id"],))
    stats["merged"] += 1
    stats["deleted"] += len(drop)


def _split(conn, members, groups: dict, stats: dict) -> None:
    """把一簇重写为裁决指定的若干组；多余行删除。用于「被误当同义的不同产品」。

    复用行为池：alias 优先、id 小者优先，逐组取一行改写，余者删除。
    """
    pool = sorted(members, key=lambda r: (0 if r["kind"] == "alias" else 1, r["id"]))
    for i, (canonical, variants) in enumerate(groups.items()):
        payload = ",".join(variants)
        if i < len(pool):
            conn.execute(
                "UPDATE lexicon_entries SET kind='alias', canonical=?, variants=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (canonical, payload, pool[i]["id"]))
        else:
            conn.execute(
                "INSERT INTO lexicon_entries(kind,canonical,variants,note) "
                "VALUES ('alias',?,?,?)", (canonical, payload, "冲突清理：拆分自同义簇"))
    for row in pool[len(groups):]:
        conn.execute("DELETE FROM lexicon_entries WHERE id=?", (row["id"],))
        stats["deleted"] += 1
    stats["split"] += 1


def resolve_conflicts(conn) -> dict:
    """按批准裁决清理同词跨组。**不提交**，由调用方控制事务。幂等。"""
    stats = {"merged": 0, "split": 0, "deleted": 0}

    for members in _clusters(_load_equiv(conn)):
        probe = {w for r in members for w in words_of(r)}
        decided = next((_APPROVED[w] for w in _APPROVED if w in probe), None)
        if decided is None:
            # 机械：canonical 取 alias 侧已有的值（规范词方向），其次 id 小者
            keep = sorted(members, key=lambda r: (0 if r["kind"] == "alias" else 1, r["id"]))[0]
            _merge(conn, members, keep["canonical"], stats)
        elif decided[0] == "merge":
            _merge(conn, members, decided[1], stats)
        elif decided[0] == "split":
            _split(conn, members, decided[1], stats)
        else:  # drop_group：(canonical, 触发词) 命中则删该行，簇内其余不动
            canonical, trigger = decided[1]
            for r in members:
                if r["canonical"] == canonical and trigger in words_of(r):
                    conn.execute("DELETE FROM lexicon_entries WHERE id=?", (r["id"],))
                    stats["deleted"] += 1
                    break
    return stats


def _check_no_conflict(conn) -> str | None:
    """返回首个同词跨组的词；None 表示干净（与读侧校验同规则）"""
    owner: dict[str, int] = {}
    for r in conn.execute(
            "SELECT id, canonical, variants FROM lexicon_entries "
            "WHERE kind IN (?, ?) AND is_active=1", EQUIV):
        for w in {r["canonical"]} | {v.strip() for v in (r["variants"] or "").split(",") if v.strip()}:
            prev = owner.get(w)
            if prev is not None and prev != r["id"]:
                return w
            owner[w] = r["id"]
    return None


def main():
    dry_run = "--dry-run" in sys.argv
    db = Path(DB)
    if not db.exists():
        print(f"ERROR: 库不存在 {db}")
        sys.exit(1)

    if dry_run:
        tmp = Path("data/backups") / "_dryrun_lexicon.db"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, tmp)
        import app.database as _dbmod
        _dbmod.DATABASE_PATH = str(tmp)   # 生产 loader 读此全局 → 副本上复验
        target, backup = tmp, None
        print(f"【DRY-RUN】在副本上执行，真库不动：{tmp}")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = Path("data/backups") / f"spec_query.db.before_lexicon_cleanup_{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, backup)
        target = db
        print(f"已备份：{backup}")

    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    before = conn.execute(
        "SELECT COUNT(*) FROM lexicon_entries WHERE kind IN (?, ?) AND is_active=1",
        EQUIV).fetchone()[0]
    try:
        stats = resolve_conflicts(conn)
        conflict = _check_no_conflict(conn)
        if conflict:
            raise RuntimeError(f"清理后仍存在同词跨组: {conflict!r}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        if backup is not None:
            shutil.copy2(backup, db)
            print(f"❌ 失败已回滚并还原备份：{e}")
        else:
            print(f"❌ DRY-RUN 失败（真库未受影响）：{e}")
        sys.exit(1)

    after = conn.execute(
        "SELECT COUNT(*) FROM lexicon_entries WHERE kind IN (?, ?) AND is_active=1",
        EQUIV).fetchone()[0]
    conn.close()

    print(f"合并簇 {stats['merged']} / 拆分簇 {stats['split']} / 删除行 {stats['deleted']}")
    print(f"equiv 行数：{before} → {after}")

    # 用**生产 loader** 复验（这才是唯一有效的验收：词库不再被整表作废）
    from app.lexicon.store import (invalidate_lexicon_caches, load_confusable_pairs,
                                   load_equivalent_groups)
    invalidate_lexicon_caches()
    eq = load_equivalent_groups()
    cf = load_confusable_pairs()
    ok = bool(eq) and bool(cf)
    print(f"load_equivalent_groups() -> {len(eq)} 条")
    print(f"load_confusable_pairs()  -> {len(cf)} 条")
    print("✅ 词库已恢复可用" if ok else "❌ 词库仍不可用，请检查")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    rc = getattr(sys.stdout, "reconfigure", None)
    if callable(rc):
        rc(encoding="utf-8")
    main()
