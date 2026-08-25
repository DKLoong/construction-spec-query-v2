# QA 模块优化设计文档（问答准确性优先）

> 日期：2026-08-24
> 状态：设计稿（待评审）
> 范围：`app/routes/qa_routes.py` 主流程重构 + 检索上下文组装增强 + Prompt 体系重构 + 埋点
> 核心原则：**回答准确性优先，在保障回复质量的前提下权衡 Token 开销；宁可舍弃部分候选条目，绝不截断条文内部内容。**

---

## 一、背景与目标

### 1.1 现状缺陷

现有 QA 链路（`app/routes/qa_routes.py`）为：

```
用户提问 → RRF混合召回(30候选) → CrossEncoder/bi-encoder精排取 top5 → 扁平上下文(每条截断200字符) → CLI/API 推理 → 返回
```

已存在基线：RRF（BM25+向量）混合检索 + CrossEncoder 重排序。

明确缺陷：

| # | 缺陷 | 现状代码位置 |
|---|------|-------------|
| 1 | 固定取 5 条，无相关性分级 | `_CONTEXT_MAX_RESULTS = 5`；`_rerank` 硬取 top_k |
| 2 | 200 字符强制截断，断章取义 | `_build_context` 中 `content[:max_chars]` |
| 3 | 无 `strip_html`，HTML 残留进入 LLM | `_build_context` 直接取 `r["content"]` |
| 4 | 缺少元数据过滤（废止/已替代规范） | 检索 SQL 无 `status` 条件 |
| 5 | 缺少候选噪声控制（分数阈值） | CrossEncoder 分数仅用于排序，不用于过滤 |
| 6 | 缺少上下文 Token 溢出兜底 | 无 Token 统计，无预算 |
| 7 | 缺少来源标记、强制/推荐、正文/条文说明区分 | 无 system prompt（CLI 后端）或弱 system prompt（API 后端） |
| 8 | 缺少请求级观测埋点 | 无日志统计各阶段数量 |

### 1.2 目标链路

```
用户提问
  → ① RRF 混合召回（保留全量候选池）
  → ② 元数据过滤（默认过滤废止/已替代，可开关）
  → ③ CrossEncoder 重排序打分（bi-encoder 降级）
  → ④ 分数阈值过滤（丢弃低分噪音）
  → ⑤ 动态条数选取（10% 向上取整 / 保底10 / 硬上限）
  → ⑥ 强弱相关分层（高相关全文+元信息 / 次相关摘要+元信息）
  → ⑦ 上下文组装 & Token 溢出兜底（按分数低→高丢弃整条，绝不截断单条内部）
  → ⑧ 大模型 Prompt 推理（RAG 综合问答 / 原文摘抄 两套 system prompt）
  → 【可选二期】输出后置校验（引用真实性 / 语义冲突 / 降级拦截）
```

### 1.3 设计范围

- **本期实现**：需求点 1-6、8；模块拆分；配置化。
- **二期预留**：需求点 7（冲突标记检测、输出后置校验）。
- **明确不做**：导入打标（AI 给整本规范打"现行/废止/修订中"标签）仅预留字段与过滤钩子，不做自动打标流程；摘要抽取为启发式，不做模型摘要。

---

## 二、模块结构设计

### 2.1 新增 / 修改文件总览

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/qa/__init__.py` | 新增 | 空包 |
| `app/qa/config.py` | 新增 | QA 运行期配置读取（DB settings 优先，config.py 常量兜底） |
| `app/qa/context.py` | 新增 | 上下文组装纯函数：Token 估算、摘要、元信息、分层、预算兜底 |
| `app/ai/prompts.py` | 新增 | 两套 system prompt 常量 + `build_system_prompt(mode)` |
| `app/routes/qa_routes.py` | 修改 | 编排新链路，承载埋点 |
| `app/models.py` | 修改 | `QaRequest` 增加 `mode` / `include_invalid` |
| `app/ai/cli_client.py` | 修改 | `ask()` 增加 `system_prompt` 参数；CLI 后端拼入 user prompt 指令区 |
| `app/ai/api_client.py` | 修改 | `ask()` 增加 `system_prompt` 参数；API 后端作为 system message |
| `app/search/sql_search.py` | 修改 | SELECT 增加 `s.status AS spec_status`（元数据过滤数据来源） |
| `app/search/hybrid_search.py` | 修改 | 向量回查 SELECT 增加 `s.status AS spec_status` |
| `app/templates/partials/qa_panel.html` | 修改 | 增加「原文摘抄」切换 |
| `static/components/qa.js` | 修改 | send() 携带 `mode` 字段 |
| `app/templates/partials/settings_dialog.html` | 修改 | AI 标签页增加「QA 检索参数」小节（阈值/条数/token 预算） |
| `app/config.py` | 修改 | 增加 QA 默认值常量（作为 DB settings 的兜底） |
| `tests/test_qa_routes.py` 等 | 修改/新增 | 详见测试要点 |

> 设计决策：**不**把 pipeline 全部塞进 `qa_routes.py`。理由：改造后编排+组装+埋点逻辑显著变长（>400 行），违背单一职责。拆出 `app/qa/context.py`（纯函数、可单测）与 `app/qa/config.py`（配置读取），`qa_routes.py` 只做编排。

### 2.2 分层职责

```
qa_routes.py         编排层：检索→过滤→重排→分层→组装→推理→埋点
app/qa/config.py     配置层：所有阈值读取（qa.* 键，DB 优先）
app/qa/context.py    纯函数层：无 IO，可独立单测
app/ai/prompts.py    Prompt 常量层
app/ai/cli_client.py / api_client.py   后端适配层
```

---

## 三、各需求点实现方案

### 需求点 1：元数据过滤

#### 现状确认

- `specifications` 表**存在** `status` 字段：`TEXT DEFAULT '现行'`（`app/database.py` SCHEMA_SQL 第 19 行）。
- 当前库内 2 条规范 `status` 全为 `'现行'`（查库确认），无废止/已替代数据。
- **不存在「适用范围」「发布部门」字段**；`dim3_usage`（工程类型）可作为适用范围的近似。
- 检索 SELECT 未带 `status`，候选 dict 无该字段。

#### 方案

**数据来源（最小侵入）**：在两条检索 SELECT 中各加一列 `s.status AS spec_status`：
- `app/search/sql_search.py` data_sql：`SELECT c.*, s.code as spec_code, s.title as spec_title` → 追加 `, s.status as spec_status`。
- `app/search/hybrid_search.py` 向量回查：同上追加。

> 对搜索结果页 `search_routes.py` 无影响（多余字段被忽略）。避免在 QA 层二次回查 DB。

**过滤逻辑**：`filter_by_metadata(candidates, include_invalid)` —— 保留 `status` 在「现行」集合内的候选；`status` 为空/未知默认放行（数据未打标时不误杀）；`include_invalid=True` 时跳过过滤。

**过滤字段**：生效状态（本期落地）+ 适用行业/发布部门（预留钩子）。因无字段，`filter_by_metadata` 接受可选白名单参数：

```python
def filter_by_metadata(
    candidates: list[dict],
    include_invalid: bool,
    status_allow: tuple[str, ...] = ("现行",),      # 生效状态白名单
    industry_allow: tuple[str, ...] = (),          # 适用行业白名单（当前无数据，空=不过滤）
    dept_allow: tuple[str, ...] = (),              # 发布部门白名单（当前无数据，空=不过滤）
) -> list[dict]:
    """RRF 后、CrossEncoder 前执行。保留命中白名单的候选。
    - include_invalid=True 时跳过全部过滤（用户明确指定查询废止规范）。
    - 候选缺 status 字段（未打标）时默认放行，避免误杀。
    """
    if include_invalid:
        return candidates
    out = []
    for c in candidates:
        st = c.get("spec_status") or ""
        if st and st not in status_allow:
            continue
        if industry_allow and (c.get("dim3_usage") or "") not in industry_allow:
            continue
        if dept_allow and (c.get("dim1_hierarchy") or "") not in dept_allow:
            continue
        out.append(c)
    return out
```

> 过滤状态集合 `status_allow` 本身也应配置化（`qa.meta.status_allow`，逗号分隔，默认 `现行`），避免魔法字符串。

**待办（不本期实现）**：导入确认后 AI 自动给整本规范打「现行/废止/修订中」标签。本期只落地过滤逻辑与数据通道（`spec_status` 进候选 dict），数据层面当前是 no-op。

---

### 需求点 2：分数阈值过滤

#### 方案

CrossEncoder 打分完成后（返回与候选对齐的 `scores: list[float]`），丢弃 `< qa.rerank.min_score` 的条目。**动态条数统计基数为过滤后的有效候选集**。

```python
def filter_by_score(
    ranked: list[tuple[dict, float]],
    min_score: float,
) -> list[tuple[dict, float]]:
    """丢弃低分噪音。返回按分数降序的 (候选, 分数) 列表。"""
    return [(c, s) for c, s in ranked if s >= min_score]
```

**降级模式适配**：CrossEncoder 不可用回退 bi-encoder 向量重排序时，分数为余弦相似度（量纲 -1~1，与 CrossEncoder 的 0.5~0.99 分布不同），使用**独立阈值集** `qa.vector.min_score`（默认 0.30）、`qa.vector.high_threshold`（默认 0.55）。见「决策记录」。

---

### 需求点 3：动态条数选取

#### 规则（按需求原文）

- 取排序靠前 10% 向上取整：`k = ceil(top_ratio * N)`；
- 不足 10 条保底 10：`k = max(k, min_results)`；
- 有效候选集 < 10 全取：`N < min_results → k = N`；
- 可配置最大条数硬上限：`k = min(k, max_results)`。

```python
def dynamic_select(
    effective_count: int,
    top_ratio: float = 0.10,
    min_results: int = 10,
    max_results: int = 12,
) -> int:
    """计算送入上下文的条数上限。effective_count 为阈值过滤后有效候选数。"""
    if effective_count <= 0:
        return 0
    if effective_count < min_results:
        return effective_count                    # 有效候选集不足 → 全取
    import math
    k = math.ceil(top_ratio * effective_count)
    k = max(k, min_results)                       # 保底
    return min(k, max_results)                    # 硬上限
```

示例（`top_ratio=0.10, min_results=10, max_results=12`）：

| 有效候选数 N | 计算 | 选取条数 |
|---|---|---|
| 6 | N<10 | 6（全取） |
| 30 | ceil(3)=3 → max(3,10)=10 | 10 |
| 80 | ceil(8)=8 → max(8,10)=10 | 10 |
| 200 | ceil(20)=20 → min(20,12)=12 | 12 |

**条数只是上限**，实际进入上下文的条数还受 Token 预算约束（需求点 5 兜底）与分层后高/次条数影响。

---

### 需求点 4：强弱相关分层

#### 分层规则

- **高相关**：`score >= qa.rerank.high_threshold` → 完整条文原文（`strip_html` 后）+ 结构化元信息。
- **次相关**：`qa.rerank.min_score <= score < qa.rerank.high_threshold` → 仅元信息 + 条文摘要。
- Prompt 明确告知 AI：次相关内容仅辅助、禁止作为主要依据。

```python
@dataclass
class RerankedItem:
    clause: dict          # 原始候选 dict（含 spec_code/spec_title/spec_status）
    score: float
    tier: str             # "high" | "low"
    content_clean: str    # strip_html 后全文（high 用）
    summary: str          # 启发式摘要（low 用，也供 verbatim 模式备选）
    meta: str             # 结构化元信息一行
    tokens_full: int      # content_clean + meta 的估算 token
    tokens_summary: int   # summary + meta 的估算 token
```

**元信息模板**（`build_meta`）：

```python
def build_meta(spec_code, spec_title, spec_status, clause_no, dim3_usage="") -> str:
    scope = dim3_usage or "适用范围未标注"
    return (
        f"【《{spec_code or '未知规范'}》{spec_title or ''}"
        f"｜{spec_status or '状态未标注'}｜条文 {clause_no or '?'}"
        f"｜适用范围：{scope}】"
    )
```

> 「适用范围」字段不存在，本期用 `dim3_usage`（工程类型）近似；从开篇条文（如 1.0.2「本规程适用于……」）抽取列为二期。

**分层函数**：

```python
def tier_items(
    ranked: list[tuple[dict, float]],
    high_threshold: float,
    min_score: float,
    summary_limit: int,
) -> tuple[list[RerankedItem], list[RerankedItem]]:
    """把过滤后候选分为 (高相关, 次相关)。默认按分数降序。"""
    high, low = [], []
    for c, s in ranked:
        if s < min_score:
            continue                      # 双保险：阈值过滤
        clean = strip_html(c.get("content") or "")
        meta = build_meta(
            c.get("spec_code"), c.get("spec_title"),
            c.get("spec_status"), c.get("clause_no"),
            c.get("dim3_usage"),
        )
        if s >= high_threshold:
            item = RerankedItem(
                clause=c, score=s, tier="high",
                content_clean=clean,
                summary=make_summary(clean, summary_limit),
                meta=meta,
                tokens_full=estimate_tokens(meta + clean),
                tokens_summary=estimate_tokens(meta + make_summary(clean, summary_limit)),
            )
            high.append(item)
        else:
            item = RerankedItem(
                clause=c, score=s, tier="low",
                content_clean=clean,
                summary=make_summary(clean, summary_limit),
                meta=meta,
                tokens_full=estimate_tokens(meta + clean),
                tokens_summary=estimate_tokens(meta + make_summary(clean, summary_limit)),
            )
            low.append(item)
    return high, low
```

---

### 需求点 5：Token 溢出兜底

#### 估算方式

- **主方案：字符数近似**，公式 `estimate_tokens(text) = ceil(len(text) / chars_per_token)`。
- `chars_per_token` 可配置（`qa.token.chars_per_token`，默认 **2**）。
  - 理由：规范条文以中文为主，主流中文 LLM（DeepSeek/GLM/豆包）平均约 1 汉字 ≈ 0.5~1 token，取 `len/2` 偏保守（比需求给出的 `len/4` 更稳妥，避免超限被截断）。英文场景 `len/4` 偏激进，中英混排下 `len/2` 是合理折中。
- **预留 tokenizer 扩展**：`estimate_tokens` 是纯函数，若后续接入 tiktoken / 后端 tokenizer 可替换实现，签名不变。

```python
def estimate_tokens(text: str, chars_per_token: int = 2) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / chars_per_token)
```

#### 预算与兜底策略

- 上下文预算：`qa.token.max_context_tokens`（默认 **6000**）。该预算只约束组装出的 context 字符串（system prompt 与问题模板另计，占模型窗口富余充足：6k 上下文 + 2k 输出远小于 32k/128k 窗口）。
- **组装规则（贪心从高分到低分，超限丢弃整条，绝不截断单条内部文本）**：

```python
def build_context(
    high: list[RerankedItem],
    low: list[RerankedItem],
    verbatim: bool,
    budget: int,
) -> tuple[str, int, int]:
    """组装上下文。返回 (context_str, context_tokens, dropped_count)。

    - 高相关在前、次相关在后，各区间内已按分数降序排列。
    - verbatim=True：次相关条目也提供全文（摘抄模式需要原文）。
    - 单条 token > budget/2 视为超长条文，直接丢弃（避免一条挤占全部预算）。
    - 贪心从高分到低分装入；超预算丢弃整条，继续尝试更短条目
      —— 等价于「按分数低→高丢弃整条」。绝不截断单条内部文本。
    """
    items: list[tuple[RerankedItem, str, int]] = []
    for it in high + low:
        if it.tier == "high" or verbatim:
            text, tok = f"{it.meta}\n{it.content_clean}", it.tokens_full
        else:
            text, tok = f"{it.meta}\n{it.summary}", it.tokens_summary
        items.append((it, text, tok))

    used = 0
    dropped = 0
    picked: list[tuple[RerankedItem, str]] = []
    for it, text, tok in items:
        if tok > budget // 2:               # 超长单条：丢弃
            dropped += 1
            continue
        if used + tok > budget:             # 预算放不下：丢弃该条
            dropped += 1
            continue
        picked.append((it, text))
        used += tok

    # 渲染：高/次相关分两段，段首加提示标记
    sections = []
    high_picked = [t for it, t in picked if it.tier == "high"]
    low_picked = [t for it, t in picked if it.tier == "low"]
    if high_picked:
        sections.append("── 高相关条文（可作为直接依据）──\n" + "\n\n".join(high_picked))
    if low_picked:
        sections.append("── 次相关条文（仅供参考，不可作为主要依据）──\n" + "\n\n".join(low_picked))
    return "\n\n".join(sections), used, dropped
```

渲染段（简化）：

```
── 高相关条文（可作为直接依据）──
<meta>高条全文
...
── 次相关条文（仅供参考，不可作为主要依据）──
<meta>摘要
...
```

> 说明：组装按「高相关在前，次相关在后；各区间按分数降序」贪心填充，等价于「分数低→高丢弃整条」。高相关优先占预算，次相关摘要利用剩余预算。

**边界策略**：
- 高相关全部装不下 → 只保留最高分若干条；若一条都装不下，返回空上下文 + 标记 `context_empty=True`，Prompt 告知 AI「当前无可用条文素材，请如实说明」，禁止编造。
- 次相关摘要本身 token 很小，通常在预算内。

---

### 需求点 6：Prompt 体系重构

#### 新增 `app/ai/prompts.py`

两套 system prompt 常量：

```python
"""QA system prompt 常量"""

SYSTEM_PROMPT_RAG = """你是建设工程规范智能问答助手。请严格依据提供的规范条文上下文回答用户问题。

输出要求：
1. 归纳总结，结构化输出（分点/分条），语言精炼准确。
2. 每条结论必须标注来源，格式：【《规范编号》条文X】（如【《JGJ107-2016》3.1.2】）。
3. 明确区分「强制性」与「推荐性」条文；若上下文未标注，不要臆断。
4. 明确区分「正文」与「条文说明」；若引用条文说明，须注明。
5. 上下文中标注为「次相关/仅供参考」的内容只能作为辅助，禁止作为主要依据。
6. 若上下文素材不足以回答，请如实说明"当前未检索到相关条文"，禁止编造规范内容。
7. 全程使用中文。"""

SYSTEM_PROMPT_VERBATIM = """你是建设工程规范条文摘抄助手。用户要求原文摘抄。

输出要求：
1. 禁止归纳、总结或改写。仅从上下文中摘抄相关条文原文，可拼接多条。
2. 每条摘抄必须标注来源：【《规范编号》条文X】。
3. 严格按原文输出，保留条文编号与表述；不增删内容。
4. 若上下文中无相关条文，请如实说明"未检索到相关条文"，禁止编造。
5. 全程使用中文。"""

PROMPT_MODES = {
    "rag": SYSTEM_PROMPT_RAG,
    "verbatim": SYSTEM_PROMPT_VERBATIM,
}


def build_system_prompt(mode: str) -> str:
    """按模式返回 system prompt；未知模式回退 RAG。"""
    return PROMPT_MODES.get(mode, SYSTEM_PROMPT_RAG)
```

#### 后端兼容（CLI vs API）

统一扩展 `ask(prompt, context, system_prompt="", work_dir=None)`：

- **API 后端**（`api_client.py`）：`system_prompt` 作为 `system` role message；`user` message = `context + "\n\n---\n\n请基于以上上下文回答：" + prompt`。`system_prompt` 为空时回退现有默认 system prompt（向后兼容）。
- **CLI 后端**（`ClaudeCodeCLI` / `CodexCLI`，无 system message 通道）：把 system prompt 作为「指令区」拼入 user prompt 首部：

```python
parts = []
if system_prompt:
    parts.append(f"[系统指令]\n{system_prompt}")
if context:
    parts.append(f"[参考上下文]\n{context}")
parts.append(f"[用户问题]\n{prompt}")
full_prompt = "\n\n---\n\n".join(parts)
```

> CLI 用 `--print` 一次性传入，无独立 system role；指令区方案是业界常见近似。API 后端效果更可靠。

#### 摘抄模式

- 前端开关 → `QaRequest.mode = "verbatim"` → `build_system_prompt("verbatim")` + `build_context(..., verbatim=True)`（次相关也尽量给全文，摘抄才有原文可抄）。
- 摘抄模式下动态条数与 Token 预算仍生效（防止超限），只是丢弃更激进。

---

### 需求点 7：可选高阶（二期预留）

本期**只预留接口**，不实现：

1. **条文冲突标记检测**：同一规范或跨规范同条文不同要求（如新旧规范修订冲突），需对 content 做相似条文聚类，输出冲突提示。预留 `app/qa/conflict.py`（空函数签名）。
2. **输出后置校验**：LLM 输出后，抽取 `【《X》条文Y】` 引用，与送入的上下文比对（真实性校验）；对归纳结果做语义一致性粗检；异常时降级拦截（返回原始摘抄或提示）。预留 `app/qa/validate.py`。

---

### 需求点 8：埋点观测

#### 数据结构

```python
@dataclass
class QATrace:
    question: str            # 截断 100 字符
    mode: str                # rag / verbatim
    backend: str             # claude / codex / deepseek / ...
    include_invalid: bool
    rrf_total: int           # hybrid_search 返回 total（RRF 全量）
    pool_size: int           # 进入过滤的候选池大小
    after_meta: int          # 元数据过滤后
    after_threshold: int     # 分数阈值过滤后（有效候选）
    select_target: int       # dynamic_select 目标条数
    high_count: int          # 高相关条数
    low_count: int           # 次相关条数
    context_tokens: int      # 组装后实际 token
    budget: int              # token 预算
    dropped_overflow: int    # 溢出丢弃条数
    context_empty: bool      # 是否空上下文
    rerank_used: str         # crossencoder / vector / none
    duration_ms: int
```

#### 存放与格式

- **本期：结构化日志**（沿用现有 logging）。每次请求结束输出一行 JSON：

```python
logger.info("[QA_TRACE] %s", json.dumps(trace.to_dict(), ensure_ascii=False))
```

- 示例：

```
[QA_TRACE] {"question":"JGJ107 接头抗拉强度要求","mode":"rag","backend":"deepseek",
"include_invalid":false,"rrf_total":126,"pool_size":30,"after_meta":30,
"after_threshold":18,"select_target":10,"high_count":3,"low_count":7,
"context_tokens":3210,"budget":6000,"dropped_overflow":0,"context_empty":false,
"rerank_used":"crossencoder","duration_ms":1450}
```

- **二期可选**：落库 `qa_request_logs` 表（含上述字段），供管理后台分位数分析调参。本期不建表。

---

## 四、配置项清单（全部可配置，禁止魔法数字）

### 4.1 存放策略

- **默认值**：`app/config.py` 新增 `QA_CONFIG_DEFAULTS: dict` 常量（静态兜底）。
- **运行时可覆盖**：DB `settings` 表，键前缀 `qa.*`（复用现有 `get_setting` / `/settings` PUT 通道）。
- **读取封装**：`app/qa/config.py` 的 `get_qa_float / get_qa_int / get_qa_bool / get_qa_str`，先读 DB，空则回退 config 默认值。

```python
# app/qa/config.py
def _get(key: str) -> str:
    from app.ocr.paddle_api import get_setting
    return get_setting(key)

def get_qa_float(key: str, default: float) -> float:
    v = _get(f"qa.{key}")
    try:
        return float(v) if v != "" else default
    except ValueError:
        return default

def get_qa_int(key: str, default: int) -> int:
    v = _get(f"qa.{key}")
    try:
        return int(v) if v != "" else default
    except ValueError:
        return default

def get_qa_bool(key: str, default: bool) -> bool:
    v = _get(f"qa.{key}")
    if v == "":
        return default
    return v.lower() in ("1", "true", "yes", "on")
```

### 4.2 阈值清单

| 配置键 | 含义 | 默认值 | 用途 |
|--------|------|--------|------|
| `qa.retrieve.candidate_pool` | RRF 候选池大小 | 30 | 替代 `_CANDIDATE_POOL_SIZE` |
| `qa.retrieve.top_ratio` | 动态条数 10% | 0.10 | 需求点 3 |
| `qa.retrieve.min_results` | 保底条数 | 10 | 需求点 3 |
| `qa.retrieve.max_results` | 硬上限条数 | 12 | 需求点 3 |
| `qa.retrieve.qa_min_candidates` | 分类筛选放宽阈值 | 3 | 替代 `_QA_MIN_CANDIDATES` |
| `qa.meta.status_allow` | 生效状态白名单 | `现行` | 需求点 1 |
| `qa.rerank.min_score` | 低相关丢弃线（CrossEncoder） | 0.50 | 需求点 2 |
| `qa.rerank.high_threshold` | 高/次分界（CrossEncoder） | 0.80 | 需求点 4 |
| `qa.vector.min_score` | 低相关丢弃线（降级向量） | 0.30 | 需求点 2 降级 |
| `qa.vector.high_threshold` | 高/次分界（降级向量） | 0.55 | 需求点 4 降级 |
| `qa.token.max_context_tokens` | 上下文 token 预算 | 6000 | 需求点 5 |
| `qa.token.chars_per_token` | 估算系数（字符/token） | 2 | 需求点 5 |
| `qa.token.summary_chars` | 次相关摘要限长 | 200 | 需求点 4 |

> 阈值调参机制：依赖埋点日志收集的 `after_threshold` / `high_count` / `low_count` / 分数分布，定期用 SQL/脚本分析分位数，手动调整 DB settings 值即可热生效（每次请求实时读 DB）。

---

## 五、决策记录（10 个待决策点）

### D1：specifications 是否有 status 字段，过滤逻辑怎么落

**结论**：**有**。`status TEXT DEFAULT '现行'` 已存在（`database.py`），当前全为「现行」。过滤逻辑落地为「数据进候选 dict + 过滤函数」两步：检索 SELECT 追加 `s.status AS spec_status`（`sql_search.py` + `hybrid_search.py` 向量回查），`filter_by_metadata` 消费该字段。当前数据层面是 no-op，但逻辑与数据通道就绪，待导入打标接入后自动生效。**适用范围/发布部门无字段**，过滤仅预留白名单钩子。

### D2：「适用范围」字段是否存在，怎么处理

**结论**：**不存在**。本期用 `dim3_usage`（工程类型）近似，写入元信息 `适用范围：{dim3_usage}`；缺失时标「适用范围未标注」。从规范开篇条文（如 1.0.2「本规程适用于……」）抽取列为二期优化。

### D3：次相关摘要生成策略

**结论**：**启发式规则**（不做模型摘要，零成本可测）：
1. `strip_html(content)` 去标记；
2. 取首个非空段（`split("\n")[0]`，若 <20 字符取第二段）；
3. 限长 `summary_chars`（200）在句子边界截断（回退到最后一个 `。；！？：`）；
4. 复用 `search_routes._safe_summary` 的 LaTeX 闭合逻辑（`$` 成对，防前端/LLM 解析错乱）；
5. 条文有 `title` 时前缀 `[title] `。

### D4：Token 估算方式与兜底阈值

**结论**：字符近似 `ceil(len/2)`（`chars_per_token=2`，可配置）。理由：中文条文为主，`len/4` 偏激进易超限。预留纯函数签名，后续可换 tokenizer。兜底预算默认 **6000**；单条 > 预算/2 直接丢弃；超预算按分数低→高丢整条；高相关优先占预算，次相关摘要吃剩余。

### D5：CrossEncoder 阈值初始值 + 调参机制

**结论**：
- 低相关丢弃线 `min_score` 默认 **0.50**（bge-reranker-base 实测分布 0.5~0.99，0.5 以下视为噪音）。
- 高/次分界 `high_threshold` 默认 **0.80**。
- 调参机制：埋点日志收集分数分布 → 脚本分析分位数 → 改 DB settings 热生效。

### D6：动态条数硬上限

**结论**：**12**。理由：现有 5 条过少；12 条 + 分层 + Token 预算兜底，能覆盖多角度且不会撑爆上下文（高相关全文 12 条最坏约 6k token 内）。保底 10、比率 10% 均为配置项。

### D7：配置化存放

**结论**：**DB settings 表（`qa.*` 键）为主 + config.py 常量兜底**。理由：项目已有 settings 表、`get_setting` helper、`/settings` PUT 通道、前端设置弹窗；已有 `ai.backend.qa`/`ai.backend.classify` 分键先例。所有阈值运行时可调、热生效。迁移方案：新增 `app/qa/config.py` 读取层，默认值收进 `QA_CONFIG_DEFAULTS`，前端设置弹窗 AI 标签页加「QA 检索参数」小节写 `qa.*` 键。

### D8：CLI vs API 后端 system prompt 拼接

**结论**：统一 `ask(prompt, context, system_prompt="", work_dir=None)` 签名。API 后端把 `system_prompt` 放 system role message（空则回退现有默认，向后兼容）；CLI 后端无 system channel，拼入 user prompt 首部作为 `[系统指令]` 段。调用方（qa_routes）按 `mode` 取 `build_system_prompt(mode)` 传入。

### D9：原文摘抄模式

**结论**：前端 qa_panel 加「原文摘抄」toggle → `qa.js` `send()` 携带 `mode: "verbatim" | "rag"` → `QaRequest.mode`（Pydantic 默认 `"rag"`）→ 后端 `build_system_prompt(mode)` + `build_context(..., verbatim=True)`（次相关也给全文，供摘抄）。RAG 模式为默认。

### D10：埋点存放

**结论**：本期结构化 JSON 日志（`[QA_TRACE]` 前缀，`logger.info`），字段清单见需求点 8。二期可选落库 `qa_request_logs` 表供分位数分析。明确不本期建表。

---

## 六、qa_routes.py 编排改造（核心流程伪代码）

```python
@router.post("/qa/ask")
async def qa_ask(request: Request, body: QaRequest):
    from app.qa.context import (
        filter_by_metadata, filter_by_score, dynamic_select,
        tier_items, build_context,
    )
    from app.qa.config import (
        get_qa_float, get_qa_int, get_qa_str,
    )
    from app.ai.prompts import build_system_prompt
    trace = QATrace(question=body.question.strip()[:100], mode=body.mode,
                    backend=body.backend or "", include_invalid=body.include_invalid)

    question = body.question.strip()
    if not question:
        return JSONResponse({"detail": "问题不能为空"}, status_code=400)

    # ① 检索（候选池大小走配置）
    pool = get_qa_int("retrieve.candidate_pool", 30)
    ... hybrid_search(sq) → candidates, total
    trace.rrf_total = total
    trace.pool_size = len(candidates)

    # ② 元数据过滤（RRF 后、CrossEncoder 前）
    status_allow = tuple(
        s.strip() for s in get_qa_str("meta.status_allow", "现行").split(",") if s.strip()
    )
    candidates = filter_by_metadata(
        candidates, body.include_invalid, status_allow=status_allow,
    )
    trace.after_meta = len(candidates)

    # ③+④ 精排打分 + 阈值过滤（降级链保留，分数阈值分 CrossEncoder/vector 两套）
    ranked = _rerank_scored(question, candidates)     # [(c, score), ...] 按分降序
    trace.rerank_used = _last_rerank_used             # 记录实际用哪种精排
    if trace.rerank_used == "crossencoder":
        min_score = get_qa_float("rerank.min_score", 0.50)
        high_thr = get_qa_float("rerank.high_threshold", 0.80)
    else:                                             # vector 降级：分数量纲不同，用独立阈值集
        min_score = get_qa_float("vector.min_score", 0.30)
        high_thr = get_qa_float("vector.high_threshold", 0.55)
    ranked = filter_by_score(ranked, min_score)
    trace.after_threshold = len(ranked)

    # ⑤ 动态条数（条数上限走配置）
    k = dynamic_select(
        len(ranked),
        top_ratio=get_qa_float("retrieve.top_ratio", 0.10),
        min_results=get_qa_int("retrieve.min_results", 10),
        max_results=get_qa_int("retrieve.max_results", 12),
    )
    trace.select_target = k
    ranked = ranked[:k]

    # ⑥ 分层
    summary_limit = get_qa_int("token.summary_chars", 200)
    high, low = tier_items(ranked, high_thr, min_score, summary_limit)
    trace.high_count, trace.low_count = len(high), len(low)

    # ⑦ Token 兜底组装
    budget = get_qa_int("token.max_context_tokens", 6000)
    context_str, used_tok, dropped = build_context(high, low, body.mode == "verbatim", budget)
    trace.context_tokens, trace.budget = used_tok, budget
    trace.dropped_overflow = dropped
    trace.context_empty = not context_str.strip()

    # ⑧ 后端 + system prompt
    backend = get_backend(body.backend)
    system_prompt = build_system_prompt(body.mode)
    resp = await backend.ask(prompt=question, context=context_str,
                             system_prompt=system_prompt, work_dir=WORKSPACE_DIR)

    ...（错误处理、sources 提取同现状，sources 只取高相关 + 次相关中被采用的条目）
    trace.duration_ms = ...
    logger.info("[QA_TRACE] %s", json.dumps(trace.to_dict(), ensure_ascii=False))
    return QAResponse(answer=answer, sources=sources, cli_used=cli_used)
```

> 关键改造点：
> 1. `_rerank` 由「返回 top_k 列表」改为 `_rerank_scored`：返回带分数的全部候选（降序），阈值过滤与条数选取移到编排层。保留现有降级链（CrossEncoder → bi-encoder → 原始顺序）与「候选 ≤ 1 直接返回」的短路（无需打分，天然不分层）。`_last_rerank_used` 记录实际生效的精排器，供埋点与阈值选型。
>
> ```python
> def _rerank_scored(question: str, candidates: list[dict]) -> list[tuple[dict, float]]:
>     """精排打分，返回 (候选, 分数) 按分数降序。
>     - 候选 ≤ 1：直接返回 [(c, 1.0)]（不打分，不分层）。
>     - CrossEncoder 可用：分数 = 模型输出（0~1）。
>     - 降级 bi-encoder 向量：分数 = 余弦相似度（-1~1），阈值用 qa.vector.* 独立集。
>     - 全程记录 _last_rerank_used = "crossencoder" / "vector" / "none"。
>     """
> ```
>
> 2. `_build_context` 整体替换为 `app/qa/context.py` 的 `build_context`（strip_html、分层、预算兜底）。
> 3. `sources` 提取改为「实际进入上下文的高/次相关条目」，保证引用与上下文一致（避免引用被 token 兜底丢弃的条文）。

---

## 七、测试要点

### 7.1 纯函数单测（新增 `tests/test_qa_context.py`）

| 测试 | 覆盖 |
|------|------|
| `test_estimate_tokens` | 空文本=0；中文 `len/2` 取整；`chars_per_token` 参数生效 |
| `test_make_summary` | 首段提取；短首段取次段；超限句子边界截断；LaTeX `$` 闭合；HTML 已剥离 |
| `test_build_meta` | 全字段齐全；缺字段兜底（未知规范/未标注） |
| `test_filter_by_metadata` | 现行保留；废止丢弃；`include_invalid=True` 全保留；缺 status 放行；白名单过滤 |
| `test_filter_by_score` | 低于 min_score 丢弃；边界值保留（>=）；空列表 |
| `test_dynamic_select` | N<10 全取；N=30→10；N=80→10；N=200→12；N=0→0；ratio/min/max 各参数生效 |
| `test_tier_items` | 分数 >= high 进高相关；[min, high) 进次相关；< min 丢弃 |
| `test_build_context` | 高相关优先；次相关仅摘要；verbatim 模式次相关给全文；超预算丢低分；超长单条丢弃；预算充足全保留；返回 dropped 计数 |
| `test_build_context_never_truncates_content` | **核心验收**：高相关条文 content 任何情况下不出现中间截断（预算内全文完整） |

### 7.2 Prompt 单测（新增 `tests/test_prompts.py`）

| 测试 | 覆盖 |
|------|------|
| `test_build_system_prompt_rag` | rag 返回含「标注来源」「禁止编造」 |
| `test_build_system_prompt_verbatim` | verbatim 返回含「禁止归纳」「摘抄」 |
| `test_build_system_prompt_unknown` | 未知 mode 回退 rag |

### 7.3 后端兼容测试（扩展 `tests/test_cli_client.py` / `test_api_client.py`）

| 测试 | 覆盖 |
|------|------|
| `test_claude_ask_includes_system_prompt` | CLI 后端 system_prompt 出现在传给 `_run_cli` 的 prompt 中 |
| `test_api_ask_uses_system_role` | API 后端 system_prompt 出现在 messages[0].content |
| `test_api_ask_no_system_prompt_fallback` | system_prompt 为空回退默认 system |

### 7.4 HTTP 集成测试（扩展 `tests/test_qa_routes.py`）

| 测试 | 覆盖 |
|------|------|
| `test_qa_mode_verbatim_passes` | mode=verbatim 时后端收到摘抄 system prompt（mock 捕获） |
| `test_qa_include_invalid_true_skips_meta_filter` | include_invalid=True 时废止条目不被过滤 |
| `test_qa_trace_log_emitted` | caplog 捕获 `[QA_TRACE]`，字段齐全 |
| 现有 16 个用例适配 | `_rerank` 签名变化、`_build_context` 移除、sources 逻辑调整 |

### 7.5 全量回归

```bash
D:/Python/python.exe -m pytest tests/ -v   # 目标 234+ 全绿
```

---

## 八、风险与验证方法

| 风险 | 等级 | 缓解与验证 |
|------|------|-----------|
| CrossEncoder 阈值（0.50/0.80）未经真实数据校准，可能误杀或漏放 | 中 | 埋点收集分数分布 → 分位数分析 → DB 调参热生效；阈值默认值保守（0.50 丢弃线较低，宁多勿少） |
| 全文进入上下文撑爆 Token | 低 | 预算 6000 + 单条上限 + 降级丢弃；`test_build_context_never_truncates_content` 保障不截断 |
| CLI 后端无 system role，指令区效果打折扣 | 中 | 文档明示 API 后端效果更可靠；CLI 用户可切 API 后端；指令区用 `[系统指令]` 显式分隔增强遵循度 |
| 次相关摘要启发式可能切到不完整句子 | 低 | 句子边界截断 + LaTeX 闭合；测试覆盖 |
| 元数据过滤当前为 no-op，接入打标前无实际过滤效果 | 低 | 属预期；数据通道（`spec_status`）先就绪，逻辑可测 |
| `_rerank` 签名变化影响现有测试/调用 | 中 | 保留降级链与短路语义；逐个适配现有 16 个 QA 用例 |
| 配置误改（如 max_results 为 0）导致上下文为空 | 低 | `get_qa_int` 数值下限校验（max(0, val)）；`dynamic_select` 对 0 有分支 |

---

## 九、需要用户拍板的点

1. **CrossEncoder 阈值初始值**：低分丢弃线 0.50、高/次分界 0.80 —— 是否接受，或希望基于埋点数据先跑一段再定。
2. **动态条数硬上限 12 / 保底 10 / 比率 10%** 是否合适。
3. **Token 预算默认 6000**、估算系数 `len/2`（不是需求示例的 `/4`）——是否接受更保守估算。
4. **降级（bi-encoder 向量）独立阈值集**（0.30 / 0.55）是否接受，或降级时跳过阈值过滤只用条数上限。
5. **「适用范围」本期用 `dim3_usage` 近似**、开篇条文抽取放二期 —— 是否接受。
6. **配置存放采用「DB settings（qa.*）+ config.py 默认值兜底」** 组合方案是否认可；设置弹窗是否本期就加 UI，还是仅后端配置读取。
7. **埋点本期只落结构化日志**（不建表），落库表放二期 —— 是否认可。
8. **原文摘抄模式**是否做成前端 toggle（默认 RAG），而非常驻。
