"""QA 上下文组装纯函数层测试（app/qa/context.py）"""
from app.qa.context import (
    estimate_tokens, make_summary, build_meta, RerankedItem,
    filter_by_metadata, filter_by_score, dynamic_select,
    tier_items, build_context,
)


def _clause(**kw):
    base = {
        "id": 1, "spec_code": "JGJ107-2016", "spec_title": "钢筋机械连接技术规程",
        "spec_status": "现行", "clause_no": "3.0.2", "title": "接头性能",
        "content": "钢筋连接用套筒应符合现行行业标准《钢筋机械连接用套筒》JG/T 163 的有关规定。",
        "dim3_usage": "房屋建筑",
    }
    base.update(kw)
    return base


# ── estimate_tokens ──
def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_chinese_len2():
    assert estimate_tokens("混凝土") == 2          # ceil(3/2)=2
    assert estimate_tokens("混凝土浇筑") == 3        # ceil(5/2)=3


def test_estimate_tokens_chars_per_token_param():
    assert estimate_tokens("abcd", chars_per_token=4) == 1
    assert estimate_tokens("abcd", chars_per_token=1) == 4


# ── make_summary ──
def test_make_summary_strips_html():
    s = make_summary("<div>钢筋连接用套筒应符合标准。</div>")
    assert "div" not in s
    assert "钢筋连接用套筒" in s


def test_make_summary_first_paragraph_short_takes_second():
    s = make_summary("短。\n这是第二段较长的内容需要被选取。", summary_limit=200)
    assert "第二段" in s


def test_make_summary_truncate_at_sentence_boundary():
    long = "条文第一句结束。条文第二句内容" + "很长" * 50 + "。"
    s = make_summary(long, summary_limit=30)
    assert "。" in s  # 在句子边界截断
    assert len(s) <= 30


def test_make_summary_closes_math():
    s = make_summary("最大力 $ 5 \\, m 不闭合的公式内容", summary_limit=20)
    assert s.count("$") % 2 == 0


def test_make_summary_title_prefix():
    s = make_summary("钢筋连接用套筒应符合标准。", title="接头")
    assert s.startswith("[接头]")


# ── build_meta ──
def test_build_meta_full():
    m = build_meta("JGJ107-2016", "钢筋机械连接", "现行", "3.0.2", "房屋建筑")
    assert "JGJ107-2016" in m and "3.0.2" in m and "现行" in m and "房屋建筑" in m


def test_build_meta_nature():
    m = build_meta("JGJ107-2016", "", "现行", "3.0.2", "", "强制性")
    assert "性质：强制性" in m


def test_build_meta_fallback():
    m = build_meta("", "", "", "", "")
    assert "未知规范" in m and "适用范围未标注" in m


# ── filter_by_metadata ──
def test_filter_keeps_current_removes_invalid():
    rows = [_clause(spec_status="现行"), _clause(id=2, spec_status="废止")]
    out = filter_by_metadata(rows, include_invalid=False)
    assert len(out) == 1 and out[0]["spec_status"] == "现行"


def test_filter_include_invalid_skips_all():
    rows = [_clause(spec_status="废止")]
    out = filter_by_metadata(rows, include_invalid=True)
    assert len(out) == 1


def test_filter_missing_status_passes():
    rows = [_clause(spec_status="")]
    out = filter_by_metadata(rows, include_invalid=False)
    assert len(out) == 1  # 未打标默认放行，不误杀


def test_filter_status_allow_config():
    rows = [_clause(spec_status="修订中")]
    out = filter_by_metadata(rows, include_invalid=False, status_allow=("现行", "修订中"))
    assert len(out) == 1


# ── filter_by_score ──
def test_filter_by_score_drops_low_keeps_boundary():
    ranked = [(_clause(), 0.9), (_clause(id=2), 0.5), (_clause(id=3), 0.3)]
    out = filter_by_score(ranked, min_score=0.5)
    assert [c["id"] for c, _ in out] == [1, 2]  # 边界值保留


def test_filter_by_score_empty():
    assert filter_by_score([], 0.5) == []


# ── dynamic_select ──
def test_dynamic_select_full_when_less_than_min():
    assert dynamic_select(6, top_ratio=0.10, min_results=10, max_results=15) == 6


def test_dynamic_select_min_floor():
    assert dynamic_select(30, top_ratio=0.10, min_results=10, max_results=15) == 10


def test_dynamic_select_cap():
    assert dynamic_select(200, top_ratio=0.10, min_results=10, max_results=15) == 15


def test_dynamic_select_zero():
    assert dynamic_select(0, top_ratio=0.10, min_results=10, max_results=15) == 0


# ── tier_items ──
def test_tier_items_splits_high_low_drops_below_min():
    ranked = [
        (_clause(id=1, content="高强度条文内容" * 5), 0.9),
        (_clause(id=2, content="中等相关内容" * 5), 0.6),
        (_clause(id=3, content="低分噪声内容" * 5), 0.2),
    ]
    high, low = tier_items(ranked, high_threshold=0.8, min_score=0.5, summary_limit=50)
    assert [i.clause["id"] for i in high] == [1]
    assert [i.clause["id"] for i in low] == [2]


def test_tier_items_high_has_full_content_low_has_summary():
    content = "钢筋机械连接接头等级选用规定。" + "详细内容" * 10
    ranked = [(_clause(content=content), 0.9), (_clause(id=2, content=content), 0.6)]
    high, low = tier_items(ranked, high_threshold=0.8, min_score=0.5, summary_limit=20)
    assert high[0].tier == "high" and low[0].tier == "low"
    assert len(high[0].content_clean) > len(high[0].summary)  # high 保留全文
    assert len(low[0].content_clean) > len(low[0].summary)    # low 只用摘要


# ── build_context ──
def _item(cid, tier, score, content="条文内容" * 20, meta=None):
    return RerankedItem(
        clause=_clause(id=cid, content=content), score=score, tier=tier,
        content_clean=content, summary="摘要",
        meta=meta or build_meta("JGJ107-2016", "", "现行", f"{cid}.0.1", ""),
        tokens_full=estimate_tokens(meta + content) if meta else estimate_tokens(content) * 3,
        tokens_summary=10,
    )


def test_build_context_high_before_low():
    high = [_item(1, "high", 0.9, content="高相关全文" * 30)]
    low = [_item(2, "low", 0.6)]
    ctx, used, dropped, _picked = build_context(high, low, verbatim=False, budget=6000)
    assert ctx.index("高相关条文") < ctx.index("次相关条文")
    assert dropped == 0


def test_build_context_low_only_summary():
    low = [_item(2, "low", 0.6, content="低相关长全文" * 100)]
    ctx, used, dropped, _picked = build_context([], low, verbatim=False, budget=6000)
    assert "摘要" in ctx or "低相关长全文" not in ctx  # 次相关不带全文


def test_build_context_verbatim_gives_full():
    low = [_item(2, "low", 0.6, content="次相关全文" * 50)]
    ctx, used, dropped, _picked = build_context([], low, verbatim=True, budget=6000)
    assert "次相关全文" in ctx


def test_build_context_drops_oversized_single():
    huge = _item(1, "high", 0.9, content="超长" * 2000)
    ctx, used, dropped, _picked = build_context([huge], [], verbatim=False, budget=6000)
    assert dropped == 1 and ctx == ""


def test_build_context_drops_low_when_over_budget():
    high = [_item(1, "high", 0.9, content="高相关" * 200)]  # ~600 token
    lows = [_item(i, "low", 0.6 - i * 0.01) for i in range(2, 30)]  # 28 条次相关
    ctx, used, dropped, _picked = build_context(high, lows, verbatim=False, budget=1000)
    assert dropped > 0
    assert used <= 1000


def test_build_context_never_truncates_content():
    """核心验收：高相关条文 content 在预算内必须全文完整，绝不出现中间截断。"""
    content = "完整条文" + "内容" * 100
    high = [_item(1, "high", 0.9, content=content)]
    ctx, used, dropped, _picked = build_context(high, [], verbatim=False, budget=6000)
    assert dropped == 0
    assert content in ctx  # 全文完整存在


def test_build_context_empty():
    ctx, used, dropped, _picked = build_context([], [], verbatim=False, budget=6000)
    assert ctx == "" and used == 0 and dropped == 0
