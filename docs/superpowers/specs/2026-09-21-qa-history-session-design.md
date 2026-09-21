# AI 问答：历史会话管理 + 界面右置 + 筛选统一 设计文档

- 日期：2026-09-21
- 范围：AI 问答模块（`app/qa/`、`app/routes/qa_routes.py`、`static/components/qa.js`、`app/templates/partials/qa_panel.html`、左栏筛选联动）
- 前置文档：`2026-08-24-qa-optimization-design.md`（本设计在其之上扩展，不改动其检索/精排链路）

---

## 一、背景与目标

当前 AI 问答是一个**弹窗**，且为**纯单轮**：

- 弹窗（`base.html:48-67`）宽 800px，按 `open-qa-modal` 事件显隐
- 对话记录只存在于浏览器内存（`qa.js:4` 的 `messages` 数组），刷新即失，无会话概念
- `send()` 只发送 `question`，不带任何历史（`qa.js:29-36`）
- 回答不落库：`qa_request_logs` 只有 question 和指标，**没有 answer**（`database.py:97-118`）

本次目标：

1. 增加**历史会话管理**（保存/切换/命名/删除/回看）
2. 界面从**弹窗改为中栏内右侧显示**，与检索结果、分类树同屏协同，复用筛选逻辑
3. 支持**多轮对话**，且 token 成本可控、上下文严格不跨会话

---

## 二、现状核实（设计依据，均经代码验证）

### 2.1 分类树对 QA 的控制现状：「三路里只通了一路」

| 控件 | 检索侧 | QA 侧 | 证据 |
|---|---|---|---|
| 分类维度 dim1~dim6 | ✅ | ✅ **已通** | `qa.js:26-35` 读 `$store.searchState.filters` 一并 POST |
| 仅现行 / 修订中 | ✅ store | ❌ **两套独立状态** | QA 面板自建复选框（`qa_panel.html:12-24` + `qa.js:9-10`），与 store 不通 |
| 包含前言·条文说明 | ✅ `includeNonClause` | ❌ **完全没接** | `qa.js` 不传该参数；后端仅在问题文本含「前言/条文说明」字样时隐式放行（`qa_routes.py:167`） |
| 启用 CE 精排 | ✅ | — 不适用 | QA 走独立精排链（`_rerank_scored`，CrossEncoder→向量→原序） |

### 2.2 实现约束：`.center-panel-v2` 是全站共用 htmx swap 目标

`search.js:117-120`、`tree.js:95-98` 均执行：

```js
htmx.ajax('GET', `/search?${params}`, { target: '.center-panel-v2', swap: 'innerHTML' })
```

而 `center_content` 在规范/规则/词库/维护/审核/导入各页都被复用（`import_routes.py:570` 等 9 处）。**任何放进 `.center-panel-v2` 的元素都会被每次检索整体替换掉。**

### 2.3 Token 基线（实测）

| 项 | token | 来源 |
|---|---|---|
| system prompt（rag 模式） | 242 | `build_system_prompt('rag')` 实测 484 字符 / 2 |
| 条文上下文 | ≤6000 | `qa.token.max_context_tokens` 默认值，DB 无覆盖 |
| 用户问题 | ~15 | |
| **单轮合计** | **≈6,300** | |

关键结论：**开销大头是「每轮重新检索的条文」，它不随轮数增长**。多轮的增量只来自历史对话文本。

---

## 三、本次敲定的决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | **精简多轮** | 历史只带最近 N 轮「问+答」纯文本，**绝不复用历史条文**（条文每轮重新检索） |
| D2 | 历史窗口 **N=6** | 走参数注册表，热生效可调 |
| D3 | 上下文**严格限于当前会话** | 严禁跨会话注入，避免污染 |
| D4 | 会话**惰性创建** | 进 QA 页为草稿态，首次发送才落库，避免堆空会话 |
| D5 | 会话名 = **首轮问题截断 20 字** | 不做 AI 摘要（多余一次 API 调用），可编辑 |
| D6 | 会话**全局共享，不绑用户** | 现有鉴权仅防恶意改库 |
| D7 | CE 精排改 **tooltip 说明**，不置灰 | 置灰会锁死检索侧功能，与"统一操作逻辑"初衷矛盾 |
| D8 | QA 页内左栏筛选**只更新状态、不发检索** | 否则点分类树会把用户弹回检索页 |
| D9 | 取消分类筛选的**静默放宽** | 改为显式提示 + 一键放宽 |
| D10 | **不设"返回检索"按钮** | 检索页仅在手动检索时触发 |

---

## 四、设计

### 4.1 信息架构与导航

两个**平级界面**，各自独立入口：

| 界面 | 中栏 | 右栏 | 入口 |
|---|---|---|---|
| 检索页 | 检索结果 | — | 输关键词回车 / 点分类树 |
| QA 页 | 对话区 + 输入框（贴底） | 会话管理（可向右折叠 ▸） | 点左栏「🤖 AI 问答」 |

> 「右栏」的物理落地方式见 §4.2——由中栏内部 flex 分列实现，视觉与三栏等价，不新增 grid 列。

- QA 输入框的提问会在后端做 RAG 召回，但**不是检索页的那次检索**——不填搜索框、不切回结果页
- 不设「返回检索」：换界面即手动检索

### 4.2 容器与 htmx 契约（**关键实现约束**）

**问题**：QA 页要放中栏，而中栏 `.center-panel-v2` 是 htmx 的 `innerHTML` 替换目标（见 2.2），输入框会被每次检索冲掉。

**解法**：在中栏内引入稳定壳层。

```
<main class="center-panel-v2">          ← 栏壳，永不替换
  <div id="main-content">               ← 新增：唯一的 htmx swap 目标
      ...检索结果 或 QA 页...
  </div>
</main>
```

- `search.js` / `tree.js` 的 `target` 由 `.center-panel-v2` 改为 `#main-content`
- QA 入口：`htmx.ajax('GET', '/qa', { target: '#main-content', swap: 'innerHTML' })`
- `#main-content` 元素本身不被替换（innerHTML swap 只换子节点），因此其内部的输入框、滚动状态稳定
- 翻页的 `hx-target="#search-results" hx-swap="outerHTML"`（`result_content.html:39,44`）不受影响，`#search-results` 变为 `#main-content` 的子节点

**关于「三栏」的落地方式**：会话管理栏**不新增 grid 列**，而是在 `#main-content` 内部用 flex 分两列（对话区 | 会话管理）。

理由：视觉效果与约定的三栏完全等价——`20% | 80%×62.5% | 80%×37.5%` ≡ `20% | 50% | 30%`；但避免了改 `.app-layout` 的 grid、处理右栏内容残留、以及 `hide_tree`（审查页 `.full-width`）分支的连带影响。

> ⚠️ 此为实现路径与字面「三栏」的差异点，视觉等价。若你要求物理上独立成列，需额外处理 `.full-width` 分支与栏间状态清理。

### 4.3 筛选统一

| 控件 | 改为 | 生效时机 |
|---|---|---|
| 分类维度 dim1~6 | 不变（已通） | 下一轮 QA 检索 |
| 仅现行 / 修订中 | 删除 QA 面板内重复复选框，改读 `$store.searchState.statusCurrent/Revising`（面板内「⚠️ 未过滤非现行规范」的红字提示一并删除——左栏切全不勾时已有 toast 提示，`search.js:85-90`） | 下一轮 QA 检索 |
| 包含前言·条文说明 | 新增：`qa.js` 的 `send()` 携带 `include_non_clause` | 下一轮 QA 检索 |
| 问题文本含「前言/条文说明」 | 保留为兜底（`qa_routes.py:167` 不动） | 本轮 |
| CE 精排 | 加 `title` 提示：<br>「CE 精排仅作用于检索结果排序；AI 问答走独立精排链，不受此开关影响」 | — |

**D8 的实现**：`tree.js` 的 `selectFilter()`、`search.js` 的 `onStatusChange()` / `onCeChange()` 在触发 `dispatchSearch()`/`search()` 前先判断当前视图。

引入 `$store.searchState.view`（`'search'` | `'qa'`），由 `qaView` 组件生命周期维护：`init()` 置 `'qa'`，Alpine `destroy()` 置 `'search'`。切换界面时 htmx 替换 `#main-content` 的子节点会销毁 `qaView` 组件，`destroy()` 随之触发——**必须验证 `destroy()` 在 htmx swap 场景下确实被调用**，若未触发则退回用 `#qa-root` 的 DOM 存在性判断（`document.getElementById('qa-root')`）。

```js
selectFilter(dimension, value) {
    // ...更新 filters（QA 页与检索页都需要）
    this.$store.searchState.filters = next;
    if (this.$store.searchState.view === 'qa') return;  // QA 页：只更新状态
    this.dispatchSearch();
}
```

保留 `search()` 的回车触发——在 QA 页按回车搜索即切回检索页，这是符合直觉的显式动作。

### 4.4 会话数据模型

```sql
CREATE TABLE IF NOT EXISTS qa_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS qa_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES qa_sessions(id) ON DELETE CASCADE,
    role          TEXT NOT NULL,          -- 'user' | 'assistant'
    content       TEXT NOT NULL,
    sources_json  TEXT DEFAULT '[]',      -- 回看历史时重建参考条文链接
    confusable_json TEXT DEFAULT '[]',
    mode          TEXT DEFAULT 'rag',
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_qa_messages_session ON qa_messages(session_id, id);
```

- 迁移方式沿用 `database.py` 既有模式（`CREATE TABLE IF NOT EXISTS` + 迁移段）
- **`qa_request_logs` 保持不动**，两表职责分离：前者是**请求级埋点**（调参用），后者是**会话内容**（用户可见）
- 删除会话时级联删消息；`ON DELETE CASCADE` 需确认 `PRAGMA foreign_keys` 已开启，否则在应用层显式删

### 4.5 多轮上下文（精简多轮）

在 `app/qa/context.py` 新增：

```python
def build_history(messages: list[dict], max_turns: int) -> str:
    """取最近 max_turns 轮「问+答」纯文本拼为历史段。
    不含任何历史条文上下文——条文每轮由 hybrid_search 重新召回。
    """
```

Prompt 组装顺序（`qa_routes.py`）：

```
[system prompt]                     242 tok
[历史：最近 N 轮 Q+A 纯文本]        ~1,600 tok（N=6）
[本轮检索到的条文]                  ≤6,000 tok   ← 每轮重新检索
[本次问题]                          ~15 tok
────────────────────────────────────────────
合计                                ≈7,900 tok（+25%）
```

**system prompt 增补约束**（`app/ai/prompts.py`）：多轮模式下要求「引用的条文必须出自**本轮**提供的条文列表；历史对话中出现过的条文不得作为引用来源」。这是 D1 的配套护栏——不带历史条文，就必须禁止 AI 凭记忆引用。

token 上限：`build_history` 内按 `max_turns` 截断；若单轮答案异常长导致历史段自身超限，按「丢最旧一轮」逐轮丢弃至符合预算。**不截断单条答案内部**（与 `build_context` 的既有原则一致）。

### 4.6 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/qa/ask` | 增字段 `session_id: int \| None`（为 None 时创建新会话，标题取首轮问题前 20 字，并在响应中返回其 id）与 `relaxed: bool = False`（§4.7 的「放宽到全部规范」重发用） |
| POST | `/qa/sessions` | 显式新建（「＋ 新建会话」按钮用；但主要走惰性创建） |
| GET | `/qa/sessions` | 会话列表（id, title, updated_at, 消息数） |
| GET | `/qa/sessions/{id}` | 该会话全部消息（含 sources/confusable） |
| PATCH | `/qa/sessions/{id}` | 重命名 |
| DELETE | `/qa/sessions/{id}` | 删除（前端二次确认） |

`QAResponse` 增字段 `session_id: int`。

**严格不跨会话**：服务端按 `session_id` 取历史，找不到或不属于该会话则视为新会话——不做任何"猜上一条"的兜底。

### 4.7 兜底放宽改为显式（D9）

现状（`qa_routes.py:191-200`）：分类筛选后候选 < `qa.retrieve.qa_min_candidates`(3) 时**静默**改为全局检索。

改为：

- **不再静默放宽**
- 回答区顶部提示：「⚠️ 当前分类筛选下仅命中 N 条，回答可能不完整」+ 按钮「[放宽到全部规范]」
- 点按钮 → 以 `relaxed=true` 重发本问（不带分类维度），结果追加为新的助手消息

`QAResponse` 增 `filtered_out: int`（被筛选掉前的候选数）与 `effective_filters`（本轮实际生效的筛选，供 4.3 的可见性提示）。

### 4.8 必须保留的既有功能（**不得改动**）

| 功能 | 位置 | 处理 |
|---|---|---|
| AI 后端配置 | ⚙️ 按钮 `base.html:54-57` → `open-settings{tab:'ai'}` | 随 QA 面板迁移，按钮保留在 QA 面板头部 |
| 原文摘抄 / 综合问答模式切换 | `qa_panel.html:4-10`、`qa.js:7,12-14` | 原样保留，仅随面板迁移 |
| 易混淆术语提示 | `qa_panel.html:31-41` | 原样保留 |
| KaTeX / marked / DOMPurify 渲染 | `qa.js:74-115` | 原样保留 |
| 参考条文链接 + 废止警示 | `qa.js:92-113` | 原样保留 |
| 条文详情弹窗 | `clause-modal.js`，`view-clause` 事件 | **完全不动**（本次明确不改回复结果的超链接弹窗方式） |
| QA 埋点 | `_emit_trace` → `qa_request_logs` | 保留，增 `session_id` 列便于按会话分析 |

### 4.9 参数注册表

在 `app/params/registry.py` 的 qa 段追加一条（group `"qa"`，自动渲染进「维护 → 参数设置 → 问答」）：

```python
meta.append(_num(
    "qa.history.max_turns", "qa", "多轮历史窗口",
    float(QA_CONFIG_DEFAULTS["history.max_turns"]), 0, 50, "0~50",
    "注入模型的历史对话轮数上限；0 = 不带历史（纯单轮）。"
    "历史只含问答文本，不含条文上下文。", dtype="int"))
```

同步：`app/config.py` 的 `QA_CONFIG_DEFAULTS` 加 `"history.max_turns": 6`；`app/qa/config.py` 无需改动（读取链已是通用的）。

---

## 五、测试策略（TDD，覆盖三类场景）

**正常场景**
- 新建会话 → 首轮发送 → 会话落库，标题 = 问题前 20 字
- 同一 `session_id` 连续两轮 → 第二轮 prompt 的 history 段含第一轮 Q+A
- 会话列表返回按 `updated_at` 倒序
- 重命名 / 删除会话生效，删除后消息级联清除

**边界场景**
- `history.max_turns = 0` → history 段为空，行为等同单轮
- 历史轮数 > N → 只保留最近 N 轮
- 单轮答案极长导致历史段超预算 → 逐轮丢弃最旧，不截断单条内部
- 首轮问题极短（<20 字）→ 标题取全文
- `session_id` 指向不存在的会话 → 视为新会话，不报错、不跨会话取历史

**异常场景**
- 分类筛选候选不足 → 返回 `filtered_out` 提示，**不再静默放宽**
- LLM 调用失败 → 失败消息是否入库（设计：**不入库**，仅返回错误提示，避免污染会话历史）
- 并发两轮请求同一会话 → 消息按 id 顺序落库，不覆盖

**回归**
- `tests/test_qa_routes.py`、`test_qa_context.py`、`test_qa_status_filter.py` 全部保持通过
- 检索侧 `test_search_*` 不受 swap 目标改动影响

---

## 六、明确不做（YAGNI）

- ❌ 会话内搜索 / 导出（markdown / HTML）
- ❌ AI 摘要生成标题（首轮问题截断已足够，且零成本）
- ❌ 折叠状态、滚动位置的服务端持久化
- ❌ 会话绑定用户（D6：全局共享）
- ❌ 引入 Pi / 其他外部 agent 接管会话（评估见 §7）

---

## 七、附：为何不引入 Pi agent 接管（评估留档）

本机已装 `pi` v0.85.1，确实自带会话管理（`--session-id` / `--continue` / `--resume` / `--session-dir`）与多轮能力。评估结论：**现阶段不采用**。

1. **RAG 上下文所有权无法让渡**：条文必须由我们每轮注入（Pi 的历史里没有新检索的条文）。一旦注入，条文就永久沉淀进 Pi 的会话文件——第 5 轮时上下文里躺着前 4 轮的条文，token 呈平方级增长，且旧条文可能与新问题矛盾。
2. **规避爆炸的唯一办法是每轮 `--no-session` 开新会话**——那 Pi 的会话能力就完全没被使用，退化成"换一个 CLI 二进制"。
3. **并未省掉开发量**：Pi 无 web 可用的会话列表接口（`--resume` 是交互式选择器），要做会话列表 UI 仍需解析 `~/.pi/agent/sessions/` 下的私有 jsonl 格式——数据源从"我们的表"变成"猜别人的文件格式"，且随版本漂移。
4. **埋点链路会断**：问答历史移出我们的库，`qa_request_logs` + 日志 Tab 的调参价值丢失；也与开发铁律 1.2/1.3（超时、分级日志、资源自管）有摩擦。

保留未来评估：若后续要做多智能体、让模型调工具跑代码，再重新评估。

---

## 八、风险与开放项

| 风险 | 说明 | 处置 |
|---|---|---|
| swap 目标改动的影响面 | `search.js`/`tree.js` 改 target 后，检索页所有入口需同步；`afterSettle` 回顶逻辑依赖 `#search-results` 存在 | 回归测试覆盖检索页翻页/换词/筛选三条路径 |
| `ON DELETE CASCADE` | 依赖 `PRAGMA foreign_keys=ON`，SQLite 默认关闭 | 实现时确认；未开启则应用层显式删消息 |
| 多轮下的引用幻觉 | AI 可能引用历史里出现过、但本轮未提供的条文 | prompt 硬约束（4.5）+ 前端只对 `sources` 内的条文转超链接（`qa.js:92-113` 已具备该保护） |
| QA 页左栏筛选"静默不同步" | 用户切了筛选但看不到影响 | 输入框附近常驻小字显示本轮生效筛选（`effective_filters`） |
| 首轮问题过短导致会话名无信息量 | 如"那检验批呢" | 可接受；配合可重命名 |
