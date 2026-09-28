import logging
from datetime import timedelta

import lancedb
import pyarrow as pa
from app.config import LANCE_DB_PATH
from app.database import get_db
from app.ai.embedding import embed_texts
from app.search.chunking import build_embed_chunks

logger = logging.getLogger(__name__)


def embedding_schema(dim: int, with_chunk_index: bool = True) -> pa.Schema:
    """`clause_embeddings` 的**唯一** schema 定义处。

    半精度向量列必须显式声明固定长度（`pa.list_(pa.float32(), dim)`），
    否则 LanceDB 推断出的列类型无法做向量检索。三处建表路径
    （index_clause / batch_index / 导入首建）全部走本函数。

    `with_chunk_index=False` 产出加列前的旧形态（5 列），供构造历史夹具；
    生产建表路径一律用默认值（含 `chunk_index`）。
    """
    chunk_index_field = [pa.field("chunk_index", pa.int64())] if with_chunk_index else []
    return pa.schema([
        pa.field("clause_id", pa.int64()),
        pa.field("spec_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), dim)),
        pa.field("dim_scores", pa.string()),
        *chunk_index_field,
    ])


# 向量臂超取倍数（R2/R9）：去重按 clause_id 进行，一条长条文可能占多个块位；
# 不超取会让块数多的条文挤掉其他条文的候选。R9 实测驳斥了「×3 就够」——
# 重叠（R4）后相邻块文本近似重复、距离簇拥，一条长条文可以独占全部 3×top_k 个块位，
# 去重后只剩它一条、向量臂塌成个位数。故倍数放到 10，**并在应用层限制
# 「同一 clause_id 最多保留 _MAX_CHUNKS_PER_CLAUSE 块」**——席位由这个上限保证，
# 而不是靠倍数凑巧够用。**唯一定义处**：超取/去重/截断是同一段固定顺序（R2），
# 常量放在调用方会让两处倍数静默分叉（调用方再乘一次就是 ×100）。
_VECTOR_FETCH_MULTIPLIER = 10
_MAX_CHUNKS_PER_CLAUSE = 2


def _dedupe_by_clause(rows: list[dict], max_per_clause: int = _MAX_CHUNKS_PER_CLAUSE,
                      top_k: int | None = None) -> list[dict]:
    """按 clause_id 去重，保留 `_distance` 最小（最相关）的若干块，再按距离截回 top_k。

    R2：**顺序固定为「超取 → 去重取最优 → 截回 top_k」**——超取的目的是把
    「被自己的块挤掉的候选」找回来，不是让向量臂拿更多席位；截回后普通查询的
    RRF 行为与改前等价（无偏斜时）。R9：`max_per_clause` 保证每个条文都有席位
    （否则一条长条文可以独占全部块位，去重后向量臂反而比改前更薄）。
    """
    best: dict[int, list[dict]] = {}
    for r in sorted(rows, key=lambda d: d.get("_distance", float("inf"))):
        cid = r["clause_id"]
        kept = best.setdefault(cid, [])
        if len(kept) < max_per_clause:
            kept.append(r)
    flat = [r for kept in best.values() for r in kept]
    flat.sort(key=lambda d: d.get("_distance", float("inf")))
    return flat[:top_k] if top_k is not None else flat


class VectorStore:
    def __init__(self):
        self.db = lancedb.connect(LANCE_DB_PATH)

    def _table_exists(self) -> bool:
        try:
            return "clause_embeddings" in self.db.table_names()
        except Exception:
            return False

    def _get_table(self):
        return self.db.open_table("clause_embeddings")

    def needs_rebuild(self) -> bool:
        """现存表是否缺少 chunk_index 列。

        ⚠ **C-8**：不要写「LanceDB 不支持 ALTER」——实测本机 `lancedb 0.17.0`
        提供 `add_columns` / `alter_columns` / `drop_columns`，加一个全空列是
        零拷贝的元数据操作。**本批之所以重建**，是因为面包屑改了既有两列
        （`text` / `embedding`）的**内容**，必须重嵌——`add_columns` 救不了内容变更。
        将来若只是纯加元数据列，应优先试 `add_columns` 而非全量重建（见 TODOS T27）。

        维护页据此显示「向量索引需重建」，而不是等写入时才发现列不存在。

        ⚠ **U7 裁定（批二收口）**：本探测**不**接线到维护页判定/重建门禁。它只看
        「列是否存在」，察觉不到本批「面包屑改了 text/embedding **内容**」的重建理由
        （内容变更≠列缺失），把它当「索引健康」判定会误导（列就绪 ≠ 内容已按新公式
        重建）。维护页因此用定时态静态文案（「升级后须重建一次」，见
        maintenance_health_result.html 的 R6 注），本批真正的门禁是全量重建 + 验收跑。
        """
        if not self._table_exists():
            return False
        try:
            names = {f.name for f in self._get_table().schema}
        except Exception as e:
            logger.warning("读取向量表 schema 失败: %s", e)
            return True
        return "chunk_index" not in names

    def optimize(self) -> bool:
        """收尾压实向量表（合并数据文件、清理过期版本）。

        **为什么需要**：超长条文按子块写入使行数上升 ~15-18%，而每次 `add` / `delete`
        都产生一个新版本，版本数增长随之加快（真实表曾落到 `rows=73 / version=173`，
        读放大与启动开销一起变差）。导入与全量重建收尾各压实一次。

        **为什么必须传 `cleanup_older_than=timedelta(0)`**：lancedb 0.17 的 `optimize()`
        默认 `cleanup_older_than=None` ⇒ 保留期 **7 天**：数据文件会合并，但**近期版本文件
        照旧留存、留存版本数不会下降**。而本批要治的版本膨胀是**一次会话内**发生的
        （真实表 rows=73 / version=173），7 天保留期对它完全无效。传 `0` = 只保留最新版本。
        本机 lancedb 0.17.0 实测（同表同一串写入）：不传参 → 留存 9 个版本清单、压实后
        反增至 11；传 0 → 留存 1 个（数据文件同时 5 → 1）。
        `delete_unverified` 保持默认 `False`：不动「未验证」文件，避免清掉中断写入的残留。

        **绝不抛异常**：压实是收尾优化，**不是**导入/重建的交付物——压实失败不得让
        「条文已入库」的导入变成失败（也正因为它同时记了 WARNING，吞掉异常才可接受）。

        Returns:
            True  = 压实已执行；
            False = 未压实（无表——新库首次导入前的合法状态，不告警；或失败——已记 WARNING）。
        """
        if not self._table_exists():
            return False
        try:
            self._get_table().optimize(cleanup_older_than=timedelta(0))
        except Exception as e:
            logger.warning("向量表压实失败（不影响本次导入/重建结果）: %s", e)
            return False
        return True

    def index_clause(self, clause_id: int, spec_id: int, text: str, dim_scores: str = "",
                     chunk_index: int = 0) -> int:
        """写**单块**向量（`chunk_index` 标明它是该条文的第几块）。

        先删该 clause 的全部旧行再写。⚠ 故一条条文的多块必须**一次**交给
        `index_clause_chunks`——逐块循环调用本方法会「后块删前块」，只剩最后一块。
        """
        return self._write_clause_vectors(clause_id, spec_id, [text], dim_scores, chunk_index)

    def index_clause_chunks(self, clause_id: int, spec_id: int, texts: list[str],
                            dim_scores: str = "") -> int:
        """写一条条文的**全部子块**：先删旧行，再一次性批量 add（chunk_index 0..N-1）。

        texts 由 `app.search.chunking.build_embed_chunks` 产出（已含前缀预算预留，
        故每块模型输入不超上限）。空列表不写、返回 0（空正文不产向量行）。

        ⚠ 一次 add 是有意的：实测逐条 add 比批量慢 40 倍，且每次 add 产生一个新版本。
        """
        return self._write_clause_vectors(clause_id, spec_id, texts, dim_scores, 0)

    def _write_clause_vectors(self, clause_id: int, spec_id: int, texts: list[str],
                              dim_scores: str, first_chunk_index: int) -> int:
        """删该 clause 的旧行 + 一次性批量写 N 行（chunk_index 自 first_chunk_index 递增）。"""
        import numpy as np
        if not texts:
            return 0
        # 先算完（慢）再删：编码失败时不动既有行，不会把旧向量删成窟窿
        vectors = embed_texts(texts)
        records = [
            {
                "clause_id": clause_id,
                "spec_id": spec_id,
                "text": text,
                "embedding": np.array(vectors[i], dtype=np.float32),
                "dim_scores": dim_scores,
                "chunk_index": first_chunk_index + i,
            }
            for i, text in enumerate(texts)
        ]

        # 先删旧记录，防止同一 clause_id 的旧块残留（多块时整条一起替换）
        if self._table_exists():
            try:
                self._get_table().delete(f"clause_id = {clause_id}")
            except Exception as e:
                # 删不掉 → 旧块与新块并存（同一 clause 被重复召回），不得静默
                logger.warning("删除 clause_id=%s 的旧向量失败: %s", clause_id, e)

        if not self._table_exists():
            # 显式指定 schema，确保 embedding 列是固定大小向量类型
            schema = embedding_schema(len(records[0]["embedding"]))
            tbl = self.db.create_table("clause_embeddings", schema=schema)
            tbl.add(records)
        else:
            self._get_table().add(records)
        return len(records)

    def search(self, query_text: str, top_k: int = 10,
               dim_filter: str | None = None,
               dedupe_by_clause: bool = True) -> list[dict]:
        """向量检索：超取 top_k × `_VECTOR_FETCH_MULTIPLIER` → 去重取最优块 → 截回 top_k。

        超长条文在表里是多行（每块一行），故**同一条文只返回一次**（取距离最小的
        那块）：否则一条长条文的多块会挤掉其他条文的候选，且调用方拿到的最差块
        距离会拖低它的融合名次。

        返回项含 `clause_id` / `spec_id` / `text` / `chunk_index` / `_distance`。
        `chunk_index` 用 `.get(..., 0)`：本机现存表仍是加列前的 5 列形态（重建才落地），
        缺列时取 0，读写都不因此报错。
        `dedupe_by_clause=False` 保留原始块（供需要看各块的调用方/探针）。
        """
        if not self._table_exists():
            return []
        import numpy as np
        q_vec = embed_texts([query_text])[0]
        q_vec = np.array(q_vec, dtype=np.float32)
        tbl = self._get_table()
        fetch_k = top_k * _VECTOR_FETCH_MULTIPLIER if dedupe_by_clause else top_k
        results = tbl.search(q_vec, vector_column_name="embedding").limit(fetch_k).to_list()
        rows = [
            {"clause_id": r["clause_id"], "spec_id": r["spec_id"],
             "text": r["text"], "chunk_index": r.get("chunk_index", 0),
             "_distance": r.get("_distance", 0)}
            for r in results
        ]
        if not dedupe_by_clause:
            return rows
        return _dedupe_by_clause(rows, max_per_clause=_MAX_CHUNKS_PER_CLAUSE, top_k=top_k)

    def iter_clause_ids(self) -> set[int]:
        """只读 `clause_id` 列（lance 数据集**列投影**），返回集合。

        集合天然去重 ⇒ 天然适配「一条超长条文多行子块」的形态。
        表不存在或读取失败时降级为空集（`read_clause_ids()` 已记日志）；
        需要区分「空表」与「读失败」的调用方直接用 `read_clause_ids()`。

        ⚠ **R1：必须走列投影**，不能用 `Table.to_arrow()`——本机 `lancedb 0.17.0`
        的 `Table.to_arrow(self) -> pa.Table` **没有** columns 参数，而
        `to_arrow().select([...])` 只是 pyarrow 视图：数据已全量物化
        （2026-09-28 在真实库实测 971 行：全量 2.55 MB vs 仅 `clause_id`
        0.0078 MB，约 330×；按 spec 预估的 2.4 万条约 62 MB/次，
        而维护页每次打开都调用）。
        """
        if not self._table_exists():
            return set()
        return self.read_clause_ids() or set()

    def read_clause_ids(self) -> set[int] | None:
        """只读 `clause_id` 列；**读失败返回 None**（已记日志），不静默降级。

        返回 None 而非空集是刻意的：**「空表」与「读失败」必须可区分**。
        前者是正常态（新库尚无条文），后者若被当成空集，
        `index_missing()` 会误判「全部条文都缺向量」而重嵌整个语料，
        健康检查也会把「读失败」误报成「正常 0 条缺失」。

        实现走 `lance.dataset(<表目录>)` 的列投影（R1，理由见 `iter_clause_ids`）；
        表目录由本实例实际连接的库拼出（`self.db.uri`），不重读 `LANCE_DB_PATH`。
        """
        try:
            import lance
            from pathlib import Path
            table_dir = Path(self.db.uri) / "clause_embeddings.lance"
            tbl = lance.dataset(str(table_dir)).to_table(columns=["clause_id"])
            return {int(v) for v in tbl.column("clause_id").to_pylist()}
        except Exception as e:
            logger.warning("只读 clause_id 失败（表目录 %s）: %s", self.db.uri, e)
            return None

    def get_orphans(self) -> list[int]:
        """返回向量表有而 SQLite 无的孤儿 clause_id（只读，不删除）

        读失败时返回 []：孤儿清理是「有则清」的可选自愈，读不出内容就没有
        可清理的对象（既有语义，见 `iter_clause_ids` 的降级）。
        """
        if not self._table_exists():
            return []
        vector_ids = self.iter_clause_ids()
        if not vector_ids:
            return []
        with get_db() as conn:
            db_ids = {r[0] for r in conn.execute("SELECT id FROM clauses").fetchall()}
        return sorted(vector_ids - db_ids)

    def sync_with_db(self) -> int:
        """对比向量表与数据库现存条文，删除孤儿向量（返回清理条数）

        用于自愈历史遗留或删除未同步的向量记录，避免旧数据污染语义检索候选池。
        仅比对 clause_id，不涉及 embedding，开销轻量，可在启动时/删除后调用。
        """
        orphans = self.get_orphans()
        if not orphans:
            return 0
        tbl = self._get_table()
        removed = 0
        for cid in orphans:
            try:
                tbl.delete(f"clause_id = {cid}")
                removed += 1
            except Exception as e:
                logger.warning("清理孤儿向量 clause_id=%s 失败: %s", cid, e)
        return removed

    def index_missing(self) -> int:
        """SQLite 有而向量表无的条文补索引（返回补齐数）。

        向量表不存在/读取失败返回 -1（区别于「无需补齐」的 0），供调用方
        识别「需全量重建」而非「单项修复」。

        缺失集在 Python 侧做差集：先全量查 SQLite 条文，再过滤不在 vector_ids
        中的记录，避免 NOT IN 动态占位符数量超过 SQLite 变量上限（32766）。

        ⚠ 读失败必须返回 -1（而不是当成空集）：空集会把**全部**条文判成缺失，
        维护页点一次「修复」就会白跑一遍全语料嵌入（写完仍是坏的）。
        """
        if not self._table_exists():
            return -1
        vector_ids = self.read_clause_ids()
        if vector_ids is None:
            return -1
        with get_db() as conn:
            all_rows = conn.execute(
                """SELECT c.id, c.spec_id, c.clause_no, c.title, c.content, c.section_path,
                          s.code, s.title as spec_title
                   FROM clauses c JOIN specifications s ON c.spec_id = s.id
                   ORDER BY c.id"""
            ).fetchall()
        missing = [r for r in all_rows if r["id"] not in vector_ids]
        added = 0
        for r in missing:
            # 超长条文切块（与导入/重建同一条路径、同一处前缀预留）；
            # 补齐若只写单块，长条文经此修补后尾部仍不可召回。
            texts = build_embed_chunks(
                r["content"], code=r["code"], spec_title=r["spec_title"],
                clause_no=r["clause_no"], clause_title=r["title"],
                section_path=r["section_path"] or "")
            if not texts:
                logger.debug("跳过无正文条文的向量补齐: clause_id=%s", r["id"])
                continue
            try:
                self.index_clause_chunks(r["id"], r["spec_id"], texts)
                added += 1
            except Exception as e:
                logger.warning("补齐向量 clause_id=%s 失败: %s", r["id"], e)
        return added

    def delete_clause(self, clause_id: int):
        if self._table_exists():
            self._get_table().delete(f"clause_id = {clause_id}")

    def clear_all(self):
        """删除整个向量表及磁盘文件，用于完全重建索引

        磁盘路径的**唯一来源是本实例实际连接的库**（self.db.uri），
        绝不重新读 app.config.LANCE_DB_PATH——历史实现那样写会与 self.db
        的来源脱钩（测试只 patch 模块级常量，self.db 连临时目录而 rmtree
        落到真实库），导致跑测试静默删掉开发机真实向量表。
        """
        import shutil
        from pathlib import Path

        # 先通过 LanceDB API 删表
        if self._table_exists():
            self.db.drop_table("clause_embeddings")

        # 清理可能残留的 WAL/日志文件，防止旧数据被重放到新表
        conn_dir = Path(self.db.uri).resolve()
        table_dir = conn_dir / "clause_embeddings.lance"
        # 守卫：解析后必须仍落在本实例库目录内，否则拒绝删除。
        # 当前拼接方式恒真，此判断防的是将来改成动态路径时静默越界删库。
        if not table_dir.is_relative_to(conn_dir):
            logger.warning("拒绝删除越界的向量表目录: %s（本实例库: %s）",
                           table_dir, conn_dir)
            return
        if table_dir.exists():
            logger.info("清理向量表目录: %s", table_dir)
            shutil.rmtree(table_dir, ignore_errors=True)

    def batch_index(self, clauses: list[dict], batch_size: int = 32,
                    progress_cb=None):
        """批量索引条文（先建表再逐批插入）

        clauses: [{"clause_id": int, "spec_id": int, "text": str, "dim_scores": str,
                   "chunk_index": int}, ...]（同一条文的多块 = 多行，chunk_index 递增）
        progress_cb(done: int, total: int) — 每处理完一批回调一次（供后台重建显示进度）
        """
        import numpy as np

        if not clauses:
            return
        total = len(clauses)

        # 先清空旧表
        self.clear_all()

        # 计算第一批嵌入以确定向量维度
        first_batch = clauses[:batch_size]
        first_embs = embed_texts([c["text"] for c in first_batch])
        vec_dim = len(first_embs[0])

        # 建表
        schema = embedding_schema(vec_dim)
        tbl = self.db.create_table("clause_embeddings", schema=schema)

        # 写入第一批
        records = []
        for i, c in enumerate(first_batch):
            records.append({
                "clause_id": c["clause_id"],
                "spec_id": c["spec_id"],
                "text": c["text"],
                "embedding": np.array(first_embs[i], dtype=np.float32),
                "dim_scores": c.get("dim_scores", ""),
                "chunk_index": c.get("chunk_index", 0),
            })
        tbl.add(records)
        if progress_cb:
            progress_cb(len(first_batch), total)

        # 逐批写入剩余
        for start in range(batch_size, len(clauses), batch_size):
            batch = clauses[start:start + batch_size]
            embs = embed_texts([c["text"] for c in batch])
            records = []
            for i, c in enumerate(batch):
                records.append({
                    "clause_id": c["clause_id"],
                    "spec_id": c["spec_id"],
                    "text": c["text"],
                    "embedding": np.array(embs[i], dtype=np.float32),
                    "dim_scores": c.get("dim_scores", ""),
                    "chunk_index": c.get("chunk_index", 0),
                })
            tbl.add(records)
            if progress_cb:
                progress_cb(min(start + batch_size, total), total)
