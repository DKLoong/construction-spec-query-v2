"""可调参数注册表与读取 helper（分类 classify / 检索 search / 问答 qa）

单一数据源：
- PARAM_META / PARAM_GROUPS —— 供参数设置 UI 渲染、后端校验、apply 写删键共用。
- get_param_* —— 运行期读取：DB settings 有合法值用之，否则回退 config 内置默认。
  带 TTL(2s) + DB 路径守卫缓存（一次拉取全量 key；防测试 monkeypatch 换库串缓存）。

QA 组键与 app/qa/config.py 读取链一致（写 `qa.<子键>` 即热生效，无需改读点）。
应用方案后须显式 clear_param_cache()（缓存仅作兜底，避免 TTL 窗口内不热生效）。
"""
import time

from app import database as _db
from app.config import (
    ADAPTIVE_THRESHOLDS, BATCH_SIZE,
    RULE_AUTO_ENABLE_RATIO, RULE_AUTO_ENABLE_MIN_HIT,
    RULE_AUTO_DISABLE_RATIO, RULE_AUTO_DISABLE_MIN_HIT,
    NEW_RULE_THRESHOLD, LABEL_CANDIDATE_LIMIT,
    SEARCH_RERANK_TOP_N, SEARCH_VECTOR_TOP_K, SEARCH_VECTOR_L2_THRESHOLD, SEARCH_RRF_K,
    SEARCH_LEXICON_EXPAND,
    QA_CONFIG_DEFAULTS,
)

PARAM_GROUPS = [
    {"id": "classify", "label": "分类"},
    {"id": "search", "label": "检索"},
    {"id": "qa", "label": "问答"},
]

# classify.ai_confidence_threshold 内置默认（=config.AI_CONFIDENCE_THRESHOLD 值；
# 原实现为 batch_queue 字面 0.7，此处收敛为一个常量供 meta 与读取共用）
AI_CONF_THRESHOLD_DEFAULT = 0.7

_DIMS = ["dim1", "dim2", "dim3", "dim4", "dim5", "dim6"]
_DIM_LABELS = {"dim1": "层级", "dim2": "阶段", "dim3": "用途", "dim4": "专业", "dim5": "部位", "dim6": "材料/工艺"}
_DIM_HELP = "规则匹配得分低于该值时该维度转交 AI 分类；数值越低越少触发 AI（多依赖规则），越高越频繁交 AI。"


def _num(key, group, label, default, vmin, vmax, placeholder, help, dtype="float"):
    return {"key": key, "group": group, "type": dtype, "min": vmin, "max": vmax,
            "default": default, "label": label, "placeholder": placeholder, "help": help}


def _str(key, group, label, default, placeholder, help):
    return {"key": key, "group": group, "type": "str", "min": None, "max": None,
            "default": default, "label": label, "placeholder": placeholder, "help": help}


def _build_meta():
    meta = []
    # ---- classify ----
    for dim in _DIMS:
        meta.append(_num(
            f"classify.threshold.{dim}", "classify",
            f"AI 介入阈值 · {_DIM_LABELS[dim]}", float(ADAPTIVE_THRESHOLDS[dim]),
            0.0, 1.0, "0~1",
            f"{_DIM_HELP}（{dim} 默认 {ADAPTIVE_THRESHOLDS[dim]}）"))
    meta.append(_num(
        "classify.ai_confidence_threshold", "classify", "AI 采纳置信度", float(AI_CONF_THRESHOLD_DEFAULT),
        0.0, 1.0, "0~1",
        "AI 分类置信度 ≥ 该值才自动采纳并沉淀规则；低于该值进入复核队列待人工确认。"))
    meta.append(_num(
        "classify.batch_size", "classify", "AI 分类批量大小", float(BATCH_SIZE),
        1, 500, "1~500",
        "每次「运行 AI 分类」从队列取出的条文数；值越大单批请求越多，太小则要跑多批。", dtype="int"))
    meta.append(_num(
        "classify.rule_auto_enable_ratio", "classify", "规则自动启用正确率", float(RULE_AUTO_ENABLE_RATIO),
        0.0, 1.0, "0~1",
        "规则长期正确率 ≥ 该值且命中足够时自动启用为激活规则（无需人工确认）。"))
    meta.append(_num(
        "classify.rule_auto_enable_min_hit", "classify", "规则自动启用最少命中", float(RULE_AUTO_ENABLE_MIN_HIT),
        1, 100, "1~100",
        "规则自动启用的最少命中次数下限，配合上方正确率使用。", dtype="int"))
    meta.append(_num(
        "classify.rule_disable_ratio", "classify", "规则自动停用正确率", float(RULE_AUTO_DISABLE_RATIO),
        0.0, 1.0, "0~1",
        "规则正确率低于该值且命中足够时自动停用（防止劣质规则长期污染分类）。"))
    meta.append(_num(
        "classify.rule_disable_min_hit", "classify", "规则自动停用最少命中", float(RULE_AUTO_DISABLE_MIN_HIT),
        1, 100, "1~100",
        "规则自动停用的最少命中次数下限。", dtype="int"))
    meta.append(_num(
        "classify.new_rule_threshold", "classify", "新规则默认阈值", float(NEW_RULE_THRESHOLD),
        0.0, 1.0, "0~1",
        "沉淀的新规则默认匹配阈值（低于规则自身阈值不参与该维竞争）。"))
    meta.append(_num(
        "classify.label_candidate_limit", "classify", "标签候选上限", float(LABEL_CANDIDATE_LIMIT),
        1, 200, "1~200",
        "喂给 AI 分类 prompt 的既有标签候选数量上限；越大越约束口径但更耗 token。", dtype="int"))
    # ---- search ----
    meta.append(_num(
        "search.rerank_top_n", "search", "精排候选条数", float(SEARCH_RERANK_TOP_N),
        0, 200, "0~200",
        "RRF 混合召回后进入 CrossEncoder 精排的前 N 条；0 表示跳过精排（更快但排序粗）。", dtype="int"))
    meta.append(_num(
        "search.vector_top_k", "search", "向量召回条数", float(SEARCH_VECTOR_TOP_K),
        1, 200, "1~200",
        "语义向量检索每次取回的前 K 条；越大越可能召回但更慢。", dtype="int"))
    meta.append(_num(
        "search.vector_l2_threshold", "search", "向量距离阈值", float(SEARCH_VECTOR_L2_THRESHOLD),
        0.0, 2.0, "0~2",
        "L2 距离小于该值才算语义相关进入融合（越小越严格）；向量降级精排阈值同量纲。"))
    meta.append(_num(
        "search.rrf_k", "search", "RRF 融合常数", float(SEARCH_RRF_K),
        1, 200, "1~200",
        "倒数排名融合的分母常数；越大越偏向高排名项、弱化低排名项。", dtype="int"))
    meta.append(_num(
        "search.lexicon_expand", "search", "词库检索扩展", float(SEARCH_LEXICON_EXPAND),
        0, 1, "0~1",
        "1=检索把词库同义/别名等价词纳入 FTS（扩召回）；0=退回纯原词。规则归一化与易混淆提示不受影响。", dtype="int"))
    # ---- qa（键与 app/qa/config.py 一致：全键 = qa.<子键>）----
    meta.append(_num(
        "qa.retrieve.candidate_pool", "qa", "候选池大小",
        float(QA_CONFIG_DEFAULTS["retrieve.candidate_pool"]), 1, 200, "1~200",
        "RRF 混合召回进入问答的候选条文池大小；越大覆盖越全但精排/组装越慢。", dtype="int"))
    meta.append(_num(
        "qa.retrieve.top_ratio", "qa", "结果选取比例",
        float(QA_CONFIG_DEFAULTS["retrieve.top_ratio"]), 0.01, 1.0, "0.01~1",
        "按候选池比例动态决定进入上下文的结果条数；越高给模型越多的候选。"))
    meta.append(_num(
        "qa.retrieve.min_results", "qa", "最少结果保底",
        float(QA_CONFIG_DEFAULTS["retrieve.min_results"]), 1, 50, "1~50",
        "动态条数选取的保底下限（防止结果过少）。", dtype="int"))
    meta.append(_num(
        "qa.retrieve.max_results", "qa", "最多结果上限",
        float(QA_CONFIG_DEFAULTS["retrieve.max_results"]), 1, 100, "1~100",
        "动态条数选取的硬上限（防上下文过长）。", dtype="int"))
    meta.append(_num(
        "qa.retrieve.qa_min_candidates", "qa", "分类筛选放宽阈值",
        float(QA_CONFIG_DEFAULTS["retrieve.qa_min_candidates"]), 1, 30, "1~30",
        "带分类筛选后候选不足该值时放宽为全局检索。", dtype="int"))
    meta.append(_str(
        "qa.meta.status_allow", "qa", "生效规范状态白名单",
        QA_CONFIG_DEFAULTS["meta.status_allow"],
        "如：现行,修订中",
        "问答仅引用白名单内状态的规范；逗号分隔，默认只放行「现行」。"))
    meta.append(_num(
        "qa.rerank.min_score", "qa", "CE 精排丢弃线",
        float(QA_CONFIG_DEFAULTS["rerank.min_score"]), 0.0, 1.0, "0~1",
        "CrossEncoder 打分低于该值的候选被丢弃；越高越保守（宁缺毋滥）。"))
    meta.append(_num(
        "qa.rerank.high_threshold", "qa", "CE 高/次相关分界",
        float(QA_CONFIG_DEFAULTS["rerank.high_threshold"]), 0.0, 1.0, "0~1",
        "CrossEncoder 打分高于该值视为高相关进入完整正文，否则次相关仅摘要。"))
    meta.append(_num(
        "qa.vector.min_score", "qa", "向量精排丢弃线",
        float(QA_CONFIG_DEFAULTS["vector.min_score"]), -1.0, 1.0, "-1~1",
        "未启用 CrossEncoder（向量降级精排）时按余弦相关丢弃低分候选（阈值集独立于 CE）。"))
    meta.append(_num(
        "qa.vector.high_threshold", "qa", "向量高/次相关分界",
        float(QA_CONFIG_DEFAULTS["vector.high_threshold"]), -1.0, 1.0, "-1~1",
        "向量降级精排时的高/次相关分界。"))
    meta.append(_num(
        "qa.token.max_context_tokens", "qa", "上下文 token 预算",
        float(QA_CONFIG_DEFAULTS["token.max_context_tokens"]), 500, 20000, "500~20000",
        "喂给模型上下文的 token 硬预算；超出时优先丢弃次相关整条，不截断单条内部。", dtype="int"))
    meta.append(_num(
        "qa.token.summary_chars", "qa", "次相关摘要限长",
        float(QA_CONFIG_DEFAULTS["token.summary_chars"]), 50, 2000, "50~2000",
        "次相关条文仅提供前 N 字摘要进入上下文。", dtype="int"))
    return meta


PARAM_META = _build_meta()
_META_BY_KEY = {m["key"]: m for m in PARAM_META}

# 缓存：{path, ts, raw:{key:str}}；TTL 2s；path 用当前 DATABASE_PATH 作守卫（测试换库不串）
_cache = {"path": None, "ts": 0.0, "raw": {}}
_TTL = 2.0


def all_param_keys() -> list[str]:
    return [m["key"] for m in PARAM_META]


def all_param_keys_set() -> set[str]:
    return {m["key"] for m in PARAM_META}


def keys_by_group(group: str) -> list[str]:
    return [m["key"] for m in PARAM_META if m["group"] == group]


def _read_all():
    now = time.time()
    path = _db.DATABASE_PATH
    if _cache["path"] == path and (now - _cache["ts"]) < _TTL:
        return _cache["raw"]
    raw = {}
    keys = all_param_keys()
    if keys:
        ph = ",".join("?" * len(keys))
        with _db.get_db() as conn:
            for row in conn.execute(
                    f"SELECT key, value FROM settings WHERE key IN ({ph})", keys).fetchall():
                raw[row["key"]] = row["value"] or ""
    _cache["path"] = path
    _cache["ts"] = now
    _cache["raw"] = raw
    return raw


def clear_param_cache():
    _cache["path"] = None
    _cache["ts"] = 0.0
    _cache["raw"] = {}


def get_param_raw(key: str) -> str:
    return _read_all().get(key, "")


def _coerce(key: str, raw: str):
    """返回类型化默认值（供 getter 与无显式 default 时用）"""
    meta = _META_BY_KEY.get(key)
    if meta is None:
        return None
    try:
        if meta["type"] == "int":
            v = int(raw) if raw != "" else int(meta["default"])
        elif meta["type"] == "float":
            v = float(raw) if raw != "" else float(meta["default"])
        else:
            return raw if raw != "" else meta["default"]
    except (TypeError, ValueError):
        v = meta["default"]
    lo, hi = meta["min"], meta["max"]
    if lo is not None and hi is not None:
        v = max(lo, min(hi, v))
    return v


def get_param_float(key: str) -> float:
    v = _coerce(key, get_param_raw(key))
    return float(v) if v is not None else 0.0


def get_param_int(key: str) -> int:
    v = _coerce(key, get_param_raw(key))
    return int(v) if v is not None else 0


def get_param_str(key: str) -> str:
    v = _coerce(key, get_param_raw(key))
    return str(v) if v is not None else ""


def get_adaptive_thresholds() -> dict[str, float]:
    """组装 dim1~dim6 阈值 dict（DB 覆盖 + 内置默认），供 should_use_ai 运行时用"""
    return {dim: get_param_float(f"classify.threshold.{dim}") for dim in _DIMS}


def validate_value(key: str, raw: str):
    """值校验（类型 + [min,max] 闭区间）。返回 (ok, err_msg)。未知 key → (False, '')"""
    meta = _META_BY_KEY.get(key)
    if meta is None:
        return False, ""
    if meta["type"] == "str":
        return True, ""
    try:
        if meta["type"] == "int":
            v = int(str(raw).strip())
        else:
            v = float(str(raw).strip())
    except (TypeError, ValueError):
        return False, "必须为数字"
    if v < meta["min"] or v > meta["max"]:
        return False, "超出可调范围"
    return True, ""
