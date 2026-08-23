"""RRF（Reciprocal Rank Fusion）融合纯函数测试"""
from app.search.rrf import rrf_fusion


def _clause(id_, **extra):
    """构造一条最简 clause dict，附带可选字段"""
    d = {"id": id_, "clause_no": f"c{id_}", "content": f"content {id_}"}
    d.update(extra)
    return d


def test_rrf_hybrid_hit_ranks_first():
    """双路都命中的条文分数累加，应排最前"""
    sql = [_clause(1), _clause(2)]
    vec = [_clause(2, _distance=0.3), _clause(3, _distance=0.5)]

    result = rrf_fusion(sql, vec)

    # 分数：id1=1/61, id2=1/62+1/61, id3=1/62 → id2 最高
    assert [r["id"] for r in result] == [2, 1, 3]


def test_rrf_single_source_rank():
    """单路命中时按各自 rank 顺序（无交叉）"""
    sql = [_clause(1), _clause(2)]
    result = rrf_fusion(sql, [])
    assert [r["id"] for r in result] == [1, 2]
    assert result[0]["_source"] == "keyword"

    vec = [_clause(10, _distance=0.1), _clause(20, _distance=0.2)]
    result2 = rrf_fusion([], vec)
    assert [r["id"] for r in result2] == [10, 20]
    assert result2[0]["_source"] == "semantic"


def test_rrf_k_parameter_affects_order():
    """k 越大越接近平均化：大 k 双低命中胜出，小 k 单高命中胜出"""
    # id1 仅 SQL rank1；id3 在 SQL rank3 + 向量 rank3（双低命中）
    sql = [_clause(1), _clause(2), _clause(3)]
    vec = [_clause(4, _distance=0.1), _clause(5, _distance=0.2),
           _clause(3, _distance=0.3)]

    big_k = rrf_fusion(sql, vec, k=60)
    small_k = rrf_fusion(sql, vec, k=0.5)

    def idx(results, id_):
        return [r["id"] for r in results].index(id_)

    # 大 k：双低命中(rank3+rank3) > 单高命中(rank1)
    assert idx(big_k, 3) < idx(big_k, 1)
    # 小 k：单高命中(rank1) > 双低命中(rank3+rank3)
    assert idx(small_k, 1) < idx(small_k, 3)


def test_rrf_dedup_by_id():
    """同一 id 双路命中只保留一条，且标记 hybrid"""
    sql = [_clause(1), _clause(2)]
    vec = [_clause(2, _distance=0.1)]

    result = rrf_fusion(sql, vec)

    ids = [r["id"] for r in result]
    assert len(ids) == 2
    assert ids.count(2) == 1
    assert result[0]["id"] == 2
    assert result[0]["_source"] == "hybrid"


def test_rrf_source_markers():
    """_source 标记覆盖三种来源"""
    sql = [_clause(1), _clause(2)]
    vec = [_clause(2, _distance=0.1), _clause(3, _distance=0.2)]

    by_id = {r["id"]: r for r in rrf_fusion(sql, vec)}

    assert by_id[1]["_source"] == "keyword"
    assert by_id[2]["_source"] == "hybrid"
    assert by_id[3]["_source"] == "semantic"


def test_rrf_preserves_original_fields():
    """融合后保留每条 dict 原有字段（含向量的 _distance）"""
    sql = [_clause(1, title="钢筋")]
    vec = [_clause(2, _distance=0.42, title="混凝土")]

    by_id = {r["id"]: r for r in rrf_fusion(sql, vec)}

    assert by_id[1]["title"] == "钢筋"
    assert by_id[1]["clause_no"] == "c1"
    assert by_id[2]["title"] == "混凝土"
    assert by_id[2]["_distance"] == 0.42


def test_rrf_empty():
    """空输入返回空列表"""
    assert rrf_fusion([], []) == []
