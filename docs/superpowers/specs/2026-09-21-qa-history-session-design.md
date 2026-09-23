# AI 问答：历史会话管理 + 独立整页 + 筛选统一 设计文档

- 日期：2026-09-21
- 范围：AI 问答模块（`app/qa/`、`app/routes/qa_routes.py`、`static/components/qa.js`、`app/templates/partials/qa_page.html`、左栏筛选联动）
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
2. 界面从**弹窗改为独立整页**（`/qa`，与 `/specs`、`/rules`、`/lexicon` 同形），左栏分类树照常在位，复用筛选逻辑
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
| D11 | 流式输出**只对 API 后端**实现 | 当前 QA 走 DeepSeek API（实测）；CLI 后端不做伪流式，后续可能整体取消 |
| D12 | 模型降级的三项修正**并入本轮** | 第 3 级降级改按排名切分（修分层失效）+ 降级状态透出前端 + 挂健康检查 |
| D13 | 流式**不新增路由**，并入 `/qa/ask` 单一入口 | 独立路由会复制检索链路，已因此产生埋点缺失与全局变量竞态两个缺陷；单一入口下逻辑只有一份 |
| D14 | QA 走**整页导航**，筛选经 **URL** 携带 | htmx 局部替换的唯一收益（筛选携带）本就非需求，代价却是三项（改检索页 swap 目标 / Alpine-in-swap 无先例 / 后退键失效）；整页导航与 `/rules` 同形，筛选改由 URL 承担 |
| D15 | 流式**两态渲染**：生成期间转义纯文本，收尾才跑完整管线 | 「节流 + marked」的真正问题不是性能而是观感（半截 Markdown 反复重排）；纯文本只跳一次，且删掉节流器与未闭合公式整类风险 |

---

## 四、设计

### 4.1 信息架构与导航

两个**平级界面**，各自独立入口：

| 界面 | 页面形态 | 内容区 | 入口 |
|---|---|---|---|
| 检索页 | 首页（`/`） | 检索结果 | 输关键词回车 / 点分类树 |
| QA 页 | **独立整页**（`/qa`） | 对话区 + 输入框（贴底）｜会话管理（可向右折叠 ▸） | 点左栏「🤖 AI 问答」（携带当前筛选的链接） |

> QA 页与 `/specs`、`/rules`、`/lexicon` 同形：都是 `base.html` 渲染的独立页面，左栏分类树照常在位。
> 「问答区 | 会话管理」的落地方式见 §4.2——QA 页内部 flex 分两列，不新增 grid 列。

- QA 输入框的提问会在后端做 RAG 召回，但**不是检索页的那次检索**——不填搜索框、不切回结果页
- 不设「返回检索」：换界面即手动检索

### 4.2 页面形态与容器（**关键实现约束**）

**QA 是整页导航的独立页面**，与 `/specs`、`/rules`、`/lexicon` **完全同形**：

```python
@router.get("/qa")
async def qa_page(request: Request):
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/qa_page.html",
    })
```

- 左栏分类树照常在位（`base.html` 的 `left_content` 渲染）
- Alpine 走 DOMContentLoaded 初始化——本项目所有页面都是这个路径，**无需任何新机制**
- 入口按钮是普通链接（与 📚规范 / 📋规则 / 📖词库 同形）

**为何不沿用 htmx 局部替换**（评审决定 D14）：首版设计让 QA 走 `htmx.ajax` 换进 `.center-panel-v2`，
唯一理由是「把检索页已选的筛选带过去」，但该需求从未被提出，代价却是三项：

1. 需要在中栏引入 `#main-content` 稳定壳层，并改动**正在工作的** `search.js`/`tree.js` 的 swap 目标
   （牵动翻页、回顶、翻页建议三条逻辑）
2. QA 会成为本项目**首个「被 htmx 揳进中栏的 Alpine 组件」**——`.center-panel-v2` 是 `innerHTML` 替换目标
   （见 §2.2），而「Alpine 在 swap 场景下能否接管」在本项目**无既有先例可依**；失败时页面外观正常但完全无响应，无报错
3. htmx 方案下浏览器**后退按钮回不到检索页**（`htmx.ajax` 不推历史）

改用整页导航后上述三项全部消失；原本想靠 htmx 拿到的「筛选携带」改由 **URL** 承担，
顺带获得可分享链接与正常的前进后退：

| 场景 | 行为 |
|---|---|
| 检索页 → QA 页 | 入口 `href` 由 `buildQaUrl()` 按当前筛选拼出（`/qa?dim4_specialty=混凝土&status_filter=现行`） |
| QA 页载入 | `seedFiltersFromUrl()` 读 URL 回填共享 store |
| QA 页内改筛选 | `history.replaceState` 同步 URL（不新增历史记录） |
| 刷新 / 分享链接 | 筛选完整还原 |

**「问答区 | 会话管理」的落地方式**：会话管理栏**不新增 grid 列**，而是 QA 页内部用 flex 分两列。

理由：视觉上就是三栏（`20% | 50% | 30%`），但不需要改 `.app-layout` 的 grid，
也避开 `hide_tree`（审查页 `.full-width`）分支的连带影响。
### 4.3 筛选统一

| 控件 | 改为 | 生效时机 |
|---|---|---|
| 分类维度 dim1~6 | 不变（已通） | 下一轮 QA 检索 |
| 仅现行 / 修订中 | 删除 QA 面板内重复复选框，改读 `$store.searchState.statusCurrent/Revising`（面板内「⚠️ 未过滤非现行规范」的红字提示一并删除——左栏切全不勾时已有 toast 提示，`search.js:85-90`） | 下一轮 QA 检索 |
| 包含前言·条文说明 | 新增：`qa.js` 的 `send()` 携带 `include_non_clause` | 下一轮 QA 检索 |
| 问题文本含「前言/条文说明」 | 保留为兜底（`qa_routes.py:167` 不动） | 本轮 |
| CE 精排 | 加 `title` 提示：<br>「CE 精排仅作用于检索结果排序；AI 问答走独立精排链，不受此开关影响」 | — |

**D8 的实现**：`tree.js` 的 `selectFilter()`、`search.js` 的 `onStatusChange()` / `onCeChange()`
在触发 `dispatchSearch()`/`search()` 前先判断当前视图。

判据用**共享的 `isQaView()`**（定义在 `tree.js`，供两处调用）：

```js
// 以 DOM 存在性判断，不用 Alpine 生命周期钩子——
// destroy() 是否触发未被官方文档化，DOM 判据零风险且同样准确
function isQaView() {
    return !!document.getElementById('qa-root');
}
```

```js
selectFilter(dimension, value) {
    // ...更新 filters（QA 页与检索页都需要）
    this.$store.searchState.filters = next;
    if (isQaView()) {
        syncQaUrl();   // QA 页：只更新状态 + 同步 URL，不发起检索
        return;
    }
    this.dispatchSearch();
}
```

保留 `search()` 的回车触发——在 QA 页按回车搜索即切回检索页，这是符合直觉的显式动作
（与从 `/specs`、`/rules` 返回检索页的方式一致）。

**筛选状态的可见性与可追溯**（缺陷 2 的修补，见 D5）：
- 助手消息落库时记录**当轮实际生效的筛选**，回看历史时显示「筛选：专业=混凝土 · 状态=现行」
- 输入框上方常驻显示「本轮生效：…」；当左栏筛选与本轮生效不一致时追加
  「· 已修改，将在下一轮生效」——这是设计文档**场景 2**（AI 回复中切换筛选）的可见性保证，
  否则用户切了会以为立即生效

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
    filters_json  TEXT DEFAULT '{}',      -- 当轮实际生效的筛选（D5）
    mode          TEXT DEFAULT 'rag',
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_qa_messages_session ON qa_messages(session_id, id);
```

- 迁移方式沿用 `database.py` 既有模式（`CREATE TABLE IF NOT EXISTS` + 迁移段）
- **`qa_request_logs` 保持不动**，两表职责分离：前者是**请求级埋点**（调参用），后者是**会话内容**（用户可见）
- 删除会话时**在应用层显式删消息**。注意理由与首版所述相反：本项目在 `get_connection()` 里
  执行 `PRAGMA foreign_keys=ON`（`app/database.py:205`，`get_db()` 只是其调用方；自 `b1d08fe` 起即有），
  **级联删除实际生效**；显式删除保留为**防御性写法**——让行为不依赖那条 PRAGMA。
  `ON DELETE CASCADE` 写在 schema 里，并有测试断言外键当前为 ON（它会在 PRAGMA 被移除时失败，
  那正是级联静默失效的时刻）

### 4.5 多轮上下文（精简多轮）

在 `app/qa/context.py` 新增：

```python
def build_history(messages: list[dict], max_turns: int, token_budget: int) -> str:
    """取最近 max_turns 轮「问+答」纯文本拼为历史段；超预算逐轮丢弃最旧。

    不含任何历史条文上下文——条文每轮由 hybrid_search 重新召回。
    复用本文件既有的 estimate_tokens()，不另造 token 估算。
    """
```

Prompt 组装顺序（`qa_routes.py`）：

```
[system prompt]                     242 tok
[历史：最近 N 轮 Q+A 纯文本]        ≤800 tok    ← qa.token.max_history_tokens（独立预算）
[本轮检索到的条文]                  ≤6,000 tok  ← qa.token.max_context_tokens（每轮重新检索）
[本次问题]                          ~15 tok
────────────────────────────────────────────
合计上界                            ≈7,060 tok
```

**system prompt 增补约束**（`app/ai/prompts.py`）：多轮模式下要求「引用的条文必须出自**本轮**提供的条文列表；历史对话中出现过的条文不得作为引用来源」。这是 D1 的配套护栏——不带历史条文，就必须禁止 AI 凭记忆引用。

token 上限：`build_history` 内按 `max_turns` 截断；若单轮答案异常长导致历史段自身超限，按「丢最旧一轮」逐轮丢弃至符合预算。**不截断单条答案内部**（与 `build_context` 的既有原则一致）。

### 4.6 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/qa/ask` | **单一入口，两种响应形态**。增字段 `session_id: int \| None`（为 None 时创建新会话，标题取首轮问题前 20 字，并在响应中返回其 id）、`relaxed: bool = False`（§4.7 的「放宽分类筛选」重发用）、`stream: bool = False`（`true` → `text/event-stream`；默认 `False` → 原 JSON，既有契约不变） |
| GET | `/qa/sessions` | 会话列表（id, title, updated_at, 消息数） |
| GET | `/qa/sessions/{id}` | 该会话全部消息（含 sources/confusable） |
| PATCH | `/qa/sessions/{id}` | 重命名 |
| DELETE | `/qa/sessions/{id}` | 删除（前端二次确认） |
| GET | `/qa/sessions/{id}/export` | 导出 Markdown（§4.12） |
| GET | `/qa/search` | 跨会话搜消息，`LIKE` 参数化（§4.12） |

`QAResponse` 增字段：`session_id: int`、`rerank_used: str`（§4.13 缺口 2 的状态标记）、`filtered_out: int`、`effective_filters: dict`。
`QaRequest` 增字段：`session_id: int | None`、`relaxed: bool = False`。

**严格不跨会话**：服务端按 `session_id` 取历史，找不到或不属于该会话则视为新会话——不做任何"猜上一条"的兜底。

### 4.7 兜底放宽改为显式（D9）

现状（`qa_routes.py:191-200`）：分类筛选后候选 < `qa.retrieve.qa_min_candidates`(3) 时**静默**改为全局检索。

改为：

- **不再静默放宽**
- 回答区顶部提示：「⚠️ 当前分类筛选下候选不足（全局命中 N 条）」+ 按钮「[放宽分类筛选]」
- 点按钮 → 以 `relaxed=true` 重发本问（**只清分类维度**；状态过滤与前言设置仍生效——
  按钮文案因此只说「分类筛选」而不说「全部规范」），结果追加为新的助手消息

`QAResponse` 增两个字段：

- `filtered_out: int` —— **不带分类维度**重新检索时的全局命中数（0 表示未触发诊断检索）。
  注意它**不是**「被筛掉的条数」，而是「放宽后能拿到多少」——这正是提示文案里那个 N。
- `effective_filters: dict` —— 本轮实际生效的筛选（分类维度 + 状态 + 前言放行），
  供 §4.3 的可见性提示与随消息落库的追溯（D5）。

### 4.8 必须保留的既有功能（**不得改动**）

| 功能 | 位置 | 处理 |
|---|---|---|
| AI 后端配置 | ⚙️ 按钮 `base.html:54-57` → `open-settings{tab:'ai'}` | 随 QA 面板迁移，按钮保留在 QA 面板头部 |
| 原文摘抄 / 综合问答模式切换 | `qa_panel.html:4-10`、`qa.js:7,12-14` | 原样保留，仅随面板迁移 |
| 易混淆术语提示 | `qa_panel.html:31-41` | 原样保留 |
| KaTeX / marked / DOMPurify 渲染 | `qa.js:74-115` | 原样保留 |
| 参考条文链接 + 废止警示 | `qa.js:92-113` | 原样保留 |
| 条文详情弹窗 | `clause-modal.js`，`view-clause` 事件 | **完全不动**（本次明确不改回复结果的超链接弹窗方式） |
| QA 埋点 | `_emit_trace` → `qa_request_logs` | 保留不动。**注意：该表无 `session_id` 列**，因此埋点与会话无法关联（按会话分析不可用）——本次不做，已记入 YAGNI |

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

### 4.10 会话切换与续聊

**核心交互**：点击会话管理栏的某一项 → 该会话全部消息载入中栏问答区 → 用户输入新指令 → **追加到该会话并带入上下文续聊**（不是新建会话）。

状态机：

| 动作 | `currentSessionId` | 中栏显示 | 下次发送的行为 |
|---|---|---|---|
| 点左栏「🤖 AI 问答」 | `null`（草稿态） | 空对话区 | **创建**新会话 |
| 点「＋ 新建会话」 | `null`（草稿态） | 清空 | **创建**新会话 |
| **点会话列表某一项** | **该会话 id** | **载入该会话全部消息** | **追加到该会话，带上下文续聊** |
| 草稿态首次发送 | 新 id | — | 创建 + 落库 |

**配套三个必须实现的细节：**

1. **`updated_at` 每次追加消息时刷新**——否则会话列表的"最近活跃"排序不动，用户会觉得列表是死的。
2. **载入的历史消息必须能重建参考条文链接**——`GET /qa/sessions/{id}` 需返回每条消息的 `sources` / `confusable`（这正是 `qa_messages.sources_json` / `confusable_json` 的用途）。前端 `messages` 数组的字段结构与表字段一一对应，直接映射即可，无需变形。
3. **「显示全部，注入只取最近 N 轮」**——载入时中栏显示该会话**全部**消息，但进模型上下文的只有最近 `history.max_turns` 轮。**这点必须显式实现并注释**，否则会被误解为"只能看 6 轮"或"全部 100 轮都塞进 prompt"。

会话列表当前项需高亮；载入完成后滚动到底部。

### 4.11 流式输出

**诊断**：`ai.backend.qa = 'deepseek'`（DB 实测），QA 走 `APIBackend`。而 `api_client.py:47` 的调用是：

```python
resp = await client.post(f"{self.base_url}/chat/completions",
                         json={"model": ..., "messages": ..., "max_tokens": 2048}, ...)
```

**无 `stream: True`，全量等待返回。** 生成 500 字答案的 8~20 秒里屏幕全黑——这就是"卡顿观感"的根因。

**服务端**：`POST /qa/ask` 增加 `stream: bool = False`，**不新增路由**。

> **为什么是单一入口**（评审决定 D13）：独立路由 `/qa/ask/stream` 必须复制整条检索链路
> （检索→过滤→精排→选条→分层→组装，约 70 行）。首版设计已因此产生两个真实缺陷：
> ① 流式版漏了 `_emit_trace`——走流式的问答（主路径）在日志 Tab 中完全不可观测；
> ② 在 `done` 事件处**跨 `await` 读模块级全局** `_last_rerank_used`——并发请求互相覆盖。
> 合并为单一入口后，检索准备抽为 `_prepare_qa_context()` 由两种形态共用，
> 逻辑只有一份，两处缺陷自然消失，也不需要「按后端类型选路由」这条隐含分发逻辑。
> 既有 `tests/test_qa_routes.py` 全部不带 `stream` → 默认 `False` → 行为不变，零改动。

| SSE 事件 | 载荷 | 用途 |
|---|---|---|
| `event: stage` | `{stage: "retrieving" \| "reranking" \| "generating"}` | 覆盖流式之前的死时间 |
| `event: delta` | `{text: "..."}` | 增量文本 |
| `event: done` | `{session_id, sources, confusable_hits, rerank_used, filtered_out}` | 收尾元数据 |
| `event: error` | `{message}` | 错误 |

- `APIBackend.ask_stream()`：`client.stream("POST", ...)` + `aiter_lines()` 解析 `data:` 行，遇 `[DONE]` 结束
- **CLI 后端不做真流式**（见 D11）。但它**不需要前端另走一条路径**：单一入口下，`CLIBackend.ask_stream` 的默认实现就是「调 `ask()` 后一次性 yield 一个 delta + done」。SSE 里一次性吐出，前端渲染路径完全一致，`stage` 事件仍会在等待期间给出「生成中…」反馈。既无「假装流式」的误导，也无需维护第二条渲染路径。

**前端**：`fetch` + `ReadableStream` 读取 SSE。**不使用 `EventSource`**——它只支持 GET，而我们需要 POST body（问题 + 筛选 + session_id）。

**⚠️ 渲染两态（本章最关键的技术约束）**

现有完整管线是 `marked → DOMPurify → KaTeX + 孤上标修复 + 字面 \n→<br>`（`qa.js:74-115`）。
生成期间**不能**让增量文本经过这条管线——会同时炸两件事：

1. **性能**——一次回答几十上百个 delta，每次全量重渲染代价随文本长度平方增长
2. **正确性**——公式写到一半（如 `$$E = mc^`）未闭合，KaTeX 会渲染失败甚至抛错

**分两态处理（评审决定 D15）：**

| 阶段 | 渲染策略 |
|---|---|
| 生成期间 | **转义纯文本**，不做任何 Markdown 解析；换行由 `.qa-answer.streaming` 的 `white-space: pre-wrap` 呈现 |
| 收到 `done` 后 | 跑**完整**管线（含 KaTeX）+ 参考条文超链接改写（`qa.js:92-113`）|

**为什么不用「节流 + marked」**：首版设计是「每 200ms 用 marked+DOMPurify 重渲染一次、不跑 KaTeX」。
真正的问题不在性能（实测不跑 KaTeX 时纯 Markdown 全量重渲染约 3ms/次，可接受），而在**观感**：
半截 Markdown（未闭合的 `**`、刚开的表格、刚开的公式）会让解析器反复重排，文字来回跳。
纯文本方案只跳一次（生成中 → 收尾排版），且顺带删掉了节流器、`md-render.renderStreaming`、
以及「KaTeX 遇到未闭合公式」整类风险。代价是生成期间没有 Markdown 排版，而那几秒本来就在等待。

**分阶段进度提示**：`stage` 事件驱动 QA 回答区顶部的一行状态文本：

```
🔍 检索中…  →  📊 已召回 30 条，精排中…  →  ✍️ 生成中…
```

流式救不了"第一个字吐出来之前"的那几秒——检索（`hybrid_search`）+ **CE 精排（跑模型，通常 1~3 秒）** 都发生在模型响应之前。这段死时间靠 `stage` 事件覆盖。项目在检索侧已有 `showRadar()` 的成熟经验（`search.js:19-38`）可借鉴。

**降级兜底**：SSE 建连或读取失败 → 自动回退到现有 `/qa/ask` 非流式路径，功能不丢（用户看到的是"等一会儿出全部内容"，而非报错）。

> **D11**：本轮流式只对 API 后端实现。CLI 后端（`claude` / `codex`）维持现状非流式。理由：CLI 启动本身有固定开销，流式救不了；且 CLI 后端后续可能整体取消（3 个调用点：`classifier_ai.py:20`、`import_routes.py:113`、`qa_routes.py:264`，其中 `APIBackend.classify_batch_sync` 已实现，功能上可覆盖）。

### 4.12 导出与搜索

**导出（Markdown）**

`GET /qa/sessions/{id}/export` → `Response(media_type="text/markdown")` + `Content-Disposition: attachment; filename=...`。

内容：会话名、创建/最后活跃时间、逐轮问答正文、每轮的参考条文清单。约 30 行，**零新依赖**。

**只做 Markdown，不做 HTML**：项目已有完整 md 渲染管线，导出的 md 可直接丢回系统渲染；HTML 导出等于把渲染结果静态化，多一份维护面而不增加能力。

**搜索（跨会话搜消息）**

位于**会话管理栏顶部**的搜索框（`qa_sessions` 列表上方）。

```sql
SELECT m.id, m.session_id, m.role, m.content, s.title
FROM qa_messages m JOIN qa_sessions s ON s.id = m.session_id
WHERE m.content LIKE ?          -- '%kw%'
ORDER BY m.session_id DESC, m.id DESC
LIMIT 100
```

- **用 `LIKE`，不上 FTS**。项目现有 FTS 套路（`database.py:51,224`）是「独立 fts5 表 + jieba 预分词」。但 `qa_messages` 是**小表**（个人/团队使用，几千到几万行量级），全表扫完全够；且中文子串匹配对"找出我说过的那句话"**比分词更精确**。上 FTS 需额外维护索引同步（含级联删除），复杂度远超收益。
- **不做「仅当前会话」勾选框**（会话内通常只有几轮，价值低）。
- 命中项显示：消息摘要 + 所属会话名；点击 → 切换到该会话并**滚动定位 + 高亮**该条消息（需消息 id 作为 DOM 锚点）。
- `LIKE` 参数必须走**参数化查询**，禁止字符串拼接（开发铁律 1.1）。

### 4.13 模型降级与可观测性

**现有降级链（QA 侧，`qa_routes.py:30-67`）——设计是完备的：**

| 级别 | 触发条件 | 得分来源 | 使用的阈值集 |
|---|---|---|---|
| 1 | CrossEncoder 可用 | 模型输出 0~1 | `qa.rerank.*`（0.50 / 0.80） |
| 2 | CE 不可用、embedding 可用 | 余弦相似度 -1~1 | `qa.vector.*`（0.30 / 0.55） |
| 3 | **两者都不可用** | **全部 = 1.0** | 走 else → `qa.vector.*` |

模型加载（`reranker.py` / `embedding.py` 同一套三态哨兵：`None` 未尝试 / 实例 / `False` 失败后不再重试）：优先本地 `models/BAAI/bge-*`，否则 HF 缓存且 **`local_files_only=True`——永不联网下载**。

**阈值联动是已有的好设计**：降级时自动切换阈值集，不会出现"拿 CE 的 0.50 去卡余弦相似度"。

**但有三个缺口：**

**🔴 缺口 1（会真出问题）：第 3 级降级时强/弱分层彻底失效**

```python
_last_rerank_used = "none"
return [(c, 1.0) for c in candidates]      # 全部 1.0
```

`tier_items`（`context.py:196`）判据是 `s = 1.0 >= high_threshold(0.55)` → **所有候选全部判为 high（强相关）** → 全部**全文**进上下文，`token.summary_chars` 摘要压缩完全不生效 → token 预算迅速耗尽，`dropped_overflow` 暴增。用户症状是"回答质量莫名变差 / 大量条文被丢弃"，日志里只有一行 warning。**这是无模型分享场景下必然踩到的。**

**修法（**不走阈值路径，改按排名**）**

> ⚠️ 不能简单"透传 RRF 原始分数"——已核实 `rrf.py:41` 的 `scores` **只用于排序，未写入返回的 dict**（候选项上只有 `_source`）。且 RRF 分数量纲极小（k=60 时双路第 1 名 ≈ 0.033），拿它比 `qa.vector.*` 的 0.30/0.55 阈值会**全部误杀**。

第 3 级降级的本质是：**只有关键词召回 + RRF 排名，没有任何绝对相关度语义**——第 30 名的条文未必不相关。此时用阈值过滤本身就是错的。正确做法：

1. **跳过 `filter_by_score`**（RRF 分数无绝对意义，不设丢弃线）
2. 按 `dynamic_select` 取前 k 条（k 仍基于候选数，`min_results`/`max_results` 生效）
3. **按 RRF 排名切分强弱**：前 1/3 → `high`（全文），后 2/3 → `low`（摘要）。排名保留了"相对更相关"的语义
4. token 预算兜底照常生效（超预算丢整条，不截断单条内部）

附带（可选，便于可观测）：在 `rrf_fusion` 里把分数写进 dict（`d["_rrf_score"] = scores[cid]`，1 行），供调试与埋点查看，但**不作为阈值依据**。

**🟠 缺口 2：降级对用户完全不可见**

`_last_rerank_used` 只落 `qa_request_logs` 与日志，前端看不到。分享后对方没装模型，用户会以为"这系统检索质量就这样"。

**修法**（值已算出，成本十几行）：加进 `QAResponse`，QA 回答区显示状态标记：

```
⚡ CE 精排    /    ≈ 向量精排    /    ⚠️ 无精排（未装模型，按关键词排序）
/    （空串 = 后端未上报 → 不显示任何标记）
```

> ⚠️ **必须区分四态而非三态**：`QAResponse.rerank_used` 的缺省值是**空串**，
> 那是「后端未上报」这一契约外状态，**不等于 `RERANK_NONE`**。若前端写成
> `if CE … else if vector … else → 无精排`，会在字段缺失时**谎报降级**。
> 前端须显式判三态、对未上报态返回空标记（Task 2 实现过程中发现）。

**🟠 缺口 3：不能只考虑精排——向量召回同样依赖模型**

`hybrid_search` 的**向量召回本身依赖 embedding 模型**（`hybrid_search.py:96-100`）：

- 只缺 CrossEncoder → 降到第 2 级，质量尚可，**可接受**
- **两个模型都缺 → 向量召回一并失效**，`hybrid_search` 退化为纯 LIKE/FTS → 检索质量**断崖式下降**

这两件事**独立发生、各自只 warning**，叠加后果无人提示。

**修法**：挂进项目已有的 `app/maintenance/health_check.py`，在维护页「健康检查」明确报告两个模型各自是否就绪、缺失的后果、安装指引。

> **D12**：本三项修正**并入本轮**。缺口 1 不修，分享出去必然踩；缺口 2/3 成本极低，但决定"对方拿到系统后知不知道自己缺东西"。模型的**安装期可选化**（安装时提示、可选跳过）属封装方案范畴，本轮不做，留待封装时统一设计。

---

## 五、测试策略（TDD，覆盖三类场景）

**正常场景**
- 新建会话 → 首轮发送 → 会话落库，标题 = 问题前 20 字
- 同一 `session_id` 连续两轮 → 第二轮 prompt 的 history 段含第一轮 Q+A
- 会话列表返回按 `updated_at` 倒序
- **点历史会话 → 载入全部消息 → 继续提问仍追加到该会话**（§4.10）
- **载入的历史消息带 `sources`，参考条文链接可重建**
- 重命名 / 删除会话生效，删除后消息级联清除
- 导出 Markdown 内容含会话名、逐轮问答、参考条文
- 跨会话搜索命中，返回所属会话名与消息 id

**边界场景**
- `history.max_turns = 0` → history 段为空，行为等同单轮
- 历史轮数 > N → 只保留最近 N 轮
- **载入 20 轮会话 → 中栏显示全部 20 轮，但 prompt 只含最近 6 轮**
- 单轮答案极长导致历史段超预算 → 逐轮丢弃最旧，不截断单条内部
- 首轮问题极短（<20 字）→ 标题取全文
- `session_id` 指向不存在的会话 → 视为新会话，不报错、不跨会话取历史
- 搜索关键词含 `%` / `_` → **LIKE 通配符需转义**，不得退化为全表命中
- 搜索无命中 → 空结果提示，不报错

**异常场景**
- 分类筛选候选不足 → 返回 `filtered_out` 提示，**不再静默放宽**
- LLM 调用失败 → **不入库**，仅返回错误提示，避免污染会话历史
- 并发两轮请求同一会话 → 消息按 id 顺序落库，不覆盖
- **SSE 流中断** → 已生成的部分内容保留显示，并回退提示（不丢已渲染文本）
- **第 3 级模型降级（无 CE 且无 embedding）** → 跳过阈值过滤、按排名切分强弱，**上下文不出现"全部全文"**（缺口 1 的回归守卫；测试用 monkeypatch 让 `get_reranker()`/`get_model()` 返回 `None` 来构造该状态）

**回归**
- `tests/test_qa_routes.py`、`test_qa_context.py`、`test_qa_status_filter.py` 全部保持通过
- 检索侧 `test_search_*` 不受 swap 目标改动影响
- `/qa/ask` 非流式路径行为不变（流式是新增独立路由，不得影响它）
- 阈值联动不回退：`rerank_used == "crossencoder"` 用 `qa.rerank.*`，否则用 `qa.vector.*`

---

## 六、明确不做（YAGNI）

- ❌ **HTML 导出**（只做 Markdown，理由见 §4.12）
- ❌ **`qa_request_logs` 增 `session_id` 列**（埋点与会话的关联分析）。会话内容已在 `qa_messages`；
  按会话分析检索质量确有价值，但本轮不加列，避免动既有埋点表结构（见 §4.8 的说明）
- ❌ **给 `qa_messages` 建 FTS 索引**（小表用 LIKE 足够，理由见 §4.12）
- ❌ **「仅当前会话」搜索勾选框**（会话内通常只几轮，价值低）
- ✅ CLI 后端走 `CLIBackend.ask_stream` 的默认实现（一次性 delta + done）——**这不是「伪流式」而是单一入口下的自然结果**：前端渲染路径完全一致，`stage` 事件仍给进度反馈（见 §4.11）
- ❌ **模型安装期可选化**（D12：属封装方案范畴，留待封装时统一设计）
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
| `ON DELETE CASCADE` | 依赖 `PRAGMA foreign_keys=ON`。**已实测本项目开着**（`app/database.py:205`），故级联生效 | 应用层仍显式删消息作为防御；测试断言外键为 ON 以在 PRAGMA 被移除时报警 |
| 多轮下的引用幻觉 | AI 可能引用历史里出现过、但本轮未提供的条文 | prompt 硬约束（4.5）+ 前端只对 `sources` 内的条文转超链接（`qa.js:92-113` 已具备该保护） |
| QA 页左栏筛选"静默不同步" | 用户切了筛选但看不到影响 | 输入框附近常驻小字显示本轮生效筛选（`effective_filters`） |
| 首轮问题过短导致会话名无信息量 | 如"那检验批呢" | 可接受；配合可重命名 |
| **流式结束时的渲染跳变被误当 bug** | 公式由源码变为排版结果是**有意设计**（§4.11） | 实现处必须注释说明；测试断言以 `done` 后的最终 HTML 为准 |
| **SSE 与反向代理 buffering** | 若将来部署在 nginx 之后，`text/event-stream` 会被缓冲，表现为"不流式" | 现在直连 uvicorn 无此问题；封装/部署方案中需写明 `proxy_buffering off` |
| **LIKE 通配符未转义** | 搜索词含 `%`/`_` 会退化为全表命中，且违反铁律 1.1 的参数化要求 | 参数化 + 显式转义，测试覆盖 |
| **第 3 级降级路径难以自然触发** | 本机模型已装，正常跑不到该分支 | 必须用 monkeypatch 构造，否则该分支永远未被测试覆盖——**而分享场景下它恰恰是常态** |
| 第 3 级降级是**行为变更** | 由"阈值过滤 + 全 high"改为"跳过阈值 + 排名切分"，会改变无模型环境下的答案构成 | 属 D12 修正目标；仅影响降级分支，第 1/2 级路径不受影响 |
