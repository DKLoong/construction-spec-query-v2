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
    """内部搜索查询参数（供 sql_search / vector_search 使用）"""
    keyword: Optional[str] = None
    dim1_hierarchy: Optional[str] = None
    dim1_nature: Optional[str] = None
    dim2_stage: Optional[str] = None
    dim3_usage: Optional[str] = None
    dim4_specialty: Optional[str] = None
    dim5_location: Optional[str] = None
    dim6_material: Optional[str] = None
    page: int = 1
    per_page: int = 20
    # 是否包含非条文（前言/条文说明等打标项）。默认 False → 检索层隐藏
    include_non_clause: bool = False
    # 是否启用 CrossEncoder 精排（用户按需开启，热切换）。默认 False → 纯 RRF
    ce_rerank: bool = False


class SearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ClauseResponse]


class QaRequest(BaseModel):
    question: str
    backend: str | None = None
    # 问答模式：rag 综合问答（默认） / verbatim 原文摘抄
    mode: str = "rag"
    # 是否包含废止/已替代规范（默认过滤，仅用户明确指定时放行）
    include_invalid: bool = False
    # 分类筛选联动：随问答请求携带当前分类树选中维度，收窄检索范围
    dim1_hierarchy: str | None = None
    dim1_nature: str | None = None
    dim2_stage: str | None = None
    dim3_usage: str | None = None
    dim4_specialty: str | None = None
    dim5_location: str | None = None
    dim6_material: str | None = None


class QAResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    cli_used: Optional[str] = None
