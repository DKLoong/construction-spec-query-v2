from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class SpecCreate(BaseModel):
    code: str
    title: str
    short_name: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim1_sys_level: Optional[str] = None
    dim1_spec_type: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim3_construction: Optional[str] = None
    dim3_scale: Optional[str] = None
    source_path: Optional[str] = None
    output_dir: Optional[str] = None


class SpecResponse(BaseModel):
    id: int
    code: str
    title: str
    short_name: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim1_sys_level: Optional[str] = None
    dim1_spec_type: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim3_construction: Optional[str] = None
    dim3_scale: Optional[str] = None
    status: str = "现行"
    source_path: Optional[str] = None
    output_dir: Optional[str] = None
    clause_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SpecUpdate(BaseModel):
    code: Optional[str] = None
    title: Optional[str] = None
    short_name: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim1_sys_level: Optional[str] = None
    dim1_spec_type: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim3_construction: Optional[str] = None
    dim3_scale: Optional[str] = None
    status: Optional[str] = None


class ClauseCreate(BaseModel):
    spec_id: int
    clause_no: str
    title: Optional[str] = None
    content: str
    parent_clause: Optional[int] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    clause_is_non: Optional[int] = 0


class ClauseResponse(BaseModel):
    id: int
    spec_id: int
    clause_no: str
    title: Optional[str] = None
    content: str
    parent_clause: Optional[int] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    ai_classified: int = 0
    needs_review: int = 0
    clause_is_non: Optional[int] = 0
    created_at: Optional[str] = None


class ClauseUpdate(BaseModel):
    clause_no: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    needs_review: Optional[int] = None


class ClassificationRuleCreate(BaseModel):
    dimension: str
    sub_field: Optional[str] = None
    pattern: str
    match_type: str = "keyword"
    priority: int = 0
    threshold: float = 0.6


class ClassificationRuleResponse(BaseModel):
    id: int
    dimension: str
    sub_field: Optional[str] = None
    pattern: str
    match_type: str
    priority: int
    threshold: float
    hit_count: int
    confirmed: int
    is_active: int
    created_at: Optional[str] = None


class ClassificationQueueItem(BaseModel):
    id: int
    clause_id: int
    dimension: str
    keyword_score: Optional[float] = None
    batch_id: Optional[str] = None
    ai_label: Optional[str] = None
    ai_confidence: Optional[float] = None
    status: str = "pending"


class UserCreate(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    is_active: int
    created_at: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


class ImportResult(BaseModel):
    spec_id: int
    spec_code: str
    spec_title: str
    clause_count: int
    classified_count: int
    pending_ai_count: int


class SearchRequest(BaseModel):
    keyword: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    page: int = 1
    page_size: int = 20


class SearchQuery(BaseModel):
    """内部搜索查询参数（供 sql_search / vector_search 使用）

    7 个维度字段为 list[str]：同维多值语义 = OR（(col LIKE ? OR col LIKE ? ...)）。
    """
    keyword: Optional[str] = None
    dim1_hierarchy: list[str] = []
    dim1_industry: list[str] = []
    dim1_nature: list[str] = []
    dim2_stage: list[str] = []
    dim3_usage: list[str] = []
    dim4_specialty: list[str] = []
    dim5_location: list[str] = []
    dim6_material: list[str] = []
    page: int = 1
    per_page: int = 20
    # 是否包含非条文（前言/条文说明等打标项）。默认 False → 检索层隐藏
    include_non_clause: bool = False
    # 是否启用 CrossEncoder 精排（用户按需开启，热切换）。默认 False → 纯 RRF
    ce_rerank: bool = False
    # 状态过滤白名单（如 ['现行','修订中']）。空列表 = 不过滤（含废止/被替代）。
    status_filter: list[str] = []


class SearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ClauseResponse]


# 维度筛选的请求字段名（dim1 含三个子维度，共 8 个字段）。
# 检索条件构造与「当轮生效筛选」记录都从这里派生，避免同一个列表
# 在 _prepare_qa_context / _effective_filters 里各写一遍而漏改。
QA_DIM_FIELDS: tuple[str, ...] = (
    "dim1_hierarchy", "dim1_industry", "dim1_nature",
    "dim2_stage", "dim3_usage", "dim4_specialty",
    "dim5_location", "dim6_material",
)


class QaRequest(BaseModel):
    question: str
    # 会话 id：None → 惰性新建会话；指向不存在的会话 → 同样视为新建（不跨会话取历史）
    session_id: Optional[int] = None
    # 「放宽分类筛选」重发标记：True 时**只**忽略分类维度重新检索，
    # 状态过滤与前言设置仍生效（见设计文档 §4.7）
    relaxed: bool = False
    backend: str | None = None
    # 问答模式：rag 综合问答（默认） / verbatim 原文摘抄
    mode: str = "rag"
    # 是否包含废止/已替代规范（默认过滤，仅用户明确指定时放行）
    include_invalid: bool = False
    # 状态过滤：逗号分隔白名单（如 '现行' / '现行,修订中'）。
    # None（缺参，旧客户端不携带）→ settings 默认白名单 + include_invalid（旧语义）；
    # ""（显式全不勾）→ 放行非现行；非空 → 覆盖默认白名单。
    status_filter: Optional[str] = None
    # 分类筛选联动：随问答请求携带当前分类树选中维度，收窄检索范围
    # 同维多选（与检索页一致）：list[str]，多值语义 = OR
    dim1_hierarchy: list[str] = []
    dim1_industry: list[str] = []
    dim1_nature: list[str] = []
    dim2_stage: list[str] = []
    dim3_usage: list[str] = []
    dim4_specialty: list[str] = []
    dim5_location: list[str] = []
    dim6_material: list[str] = []


class QAResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    cli_used: Optional[str] = None
    # 易混淆术语命中（用户问题原文同现 term_a/term_b）；仅提示，不做任何改写
    confusable_hits: list[dict] = []
    # 实际生效的精排级别（crossencoder / vector / none）；前端据此提示降级
    rerank_used: str = ""
    # 本次问答所属会话 id（惰性创建时为新 id）
    session_id: int = 0
    # 分类筛选候选不足时的全局命中数（>0 表示被筛选挡住，前端提示可放宽）
    filtered_out: int = 0
    # 本轮实际生效的筛选（前端展示「当前生效筛选」；T15 起随助手消息落库追溯）
    effective_filters: dict = {}


class QaSessionRenameRequest(BaseModel):
    """会话重命名请求。"""
    title: str
