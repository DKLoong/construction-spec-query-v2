# QA 前端：容器契约 + 界面右置 + 筛选统一 + 会话 UI + 流式渲染 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AI 问答从弹窗改为独立的整页（左侧分类树照常在位），接入会话列表与续聊，让分类树筛选对检索页与 QA 页同时生效，并把流式输出渲染到界面。

**Architecture:** QA 是**整页导航**的独立页面（`GET /qa` → `base.html`，与 `/rules`、`/lexicon` 同形），内部用 flex 分两列承载「对话区 | 会话管理（可折叠）」。筛选经 **URL** 跨页携带（整页导航会重置 Alpine store），筛选统一靠一个共享的 DOM 判据 `isQaView()`：在 QA 页时左栏筛选只更新状态并同步 URL、不发检索，从而让「下一轮生效」成立。流式渲染**两态**：生成期间显示**转义纯文本**（`pre-wrap` 保留换行），`done` 后一次性跑完整管线（marked + DOMPurify + KaTeX）。

**Tech Stack:** FastAPI + Jinja2 · htmx · Alpine.js · marked / DOMPurify / KaTeX · Playwright（验证）

**Spec:** `docs/superpowers/specs/2026-09-21-qa-history-session-design.md`
**前置计划:** `docs/superpowers/plans/2026-09-21-qa-backend-history-degradation.md`（**必须先完成并合入**——本计划依赖 `/qa/sessions*`、`/qa/search`、`/qa/ask` 的 `stream` 开关、`rerank_used` 字段）

## Global Constraints

- 前端渲染用户生成内容**必须**转义，防范 XSS（开发铁律 1.1）；本计划中凡插入 DB/用户可控字符串，先 `esc()` 或用 `x-text`（禁用 `x-html`）
- 网络请求必须捕获指定异常，禁止裸 `catch(e) {}` 吞掉错误（开发铁律 1.2）；允许带 `console.error` 的兜底捕获
- 循环内禁止发起网络请求（开发铁律 1.3）
- 业务常量集中管理，禁止魔法数字散落（开发铁律 1.3）
- 改静态文件/模板后：浏览器必须 **Ctrl+F5 强刷**，且 `base.html` 里 `<script src="...?v=N">` 的 `N` **必须递增**（项目 CLAUDE.md 三·5）
- **每次 Task 完成后追加一次静态资源版本号**（`app.css?v=N` / `qa.js?v=N` 等），否则用户拿到旧缓存
  > ⚠️ **派发该 Task 时，必须把 `app/templates/base.html` 一并列入允许改动文件**——版本号在它里面。
  > 本计划已两次因此欠账（T1-R1 改了 `search.js`、T2-R1 改了 `app.css`，两轮都因 `base.html` 不在
  > 允许清单内而未能递增 `?v=N`），事后由控制器补提交。**改静态资源的 Task，其允许清单必含 `base.html`。**
- 服务重启严格按项目 CLAUDE.md 三·1~4 执行（Windows 下 `--reload` 是双进程，必须全杀）
- **UI 验证用隔离实例**：副本库 + 独立端口 + 临时管理员，不污染 dev 库与 8000 端口
- 提交信息格式 `type: 描述`，单 Task 单提交

## 前置：隔离验证实例（每个 UI Task 都用到）

不要在生产 dev 库上验证 UI 改动。按下面步骤起一个一次性实例：

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_qa.db                      # 副本库，验证完删除
export DATABASE_PATH="$PWD/data/_probe_qa.db"                 # ← 只走环境变量，见下注
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

> **库路径只走环境变量，禁止改源码常量**：`app/config.py:16` 已是
> `DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "spec_query.db"))`，
> `app/database.py:4` 从它导入 ⇒ 上面那条 `export` 就够（实测有效）。
> **首版计划写的「或临时改常量」是陷阱**：改源码去指向副本库，既有**误提交**风险（一次 `git add .` 就带上），
> 又有**还原遗漏**风险（忘了改回就指向测试库）——两个风险都比它省下的那点麻烦大。
> 另注：`uvicorn` **不加 `--reload`**（单进程；避免 Windows 下 reloader+worker 双进程导致"改了没生效"，见项目 CLAUDE.md 三）。
> **Task 全部完成后删除** `data/_probe_qa.db` 与临时脚本（开发铁律七·3：清理临时文件）。

Playwright 探针脚本统一用项目 CLAUDE.md 二·1 的同步 API：

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:8123")
    # 断言...
    browser.close()
```

### 前置 A：**每个探针都必须先登录**（不做的话 RED 是假红）

全站受 `AuthMiddleware` 保护（`app/main.py:110-129`）：除 `/login`、`/static`、`/health` 外，
无 `access_token` cookie 一律 `302 → /login`。**探针不登录时，`page.goto` 之后看到的是登录页**——
`page.click("text=🤖 AI问答")` 会因为元素不存在而失败，**代码一行未改也会红** ⇒ RED 失去意义
（T1 施工时实际踩到：首跑 RED 的失败原因是登录页，不是「`/qa` 尚不存在」）。

⇒ **runner 必须先在 `page` 上登录**，再跑各用例（**不要**在每个用例函数体里各写一遍）：

```python
import os

def login(pg):
    """探针前置：登录隔离实例（站点受 AuthMiddleware 保护）。
    默认取 create_admin 的默认账号（见 scripts/create_admin.py 的 CLI 默认值），
    可用环境变量覆盖——**不要**把口令散写进各用例。"""
    pg.goto(f"{BASE}/login")
    pg.fill("input[name=username]", os.getenv("PROBE_USER", "admin"))
    pg.fill("input[name=password]", os.getenv("PROBE_PASS", "admin123"))
    pg.press("input[name=password]", "Enter")
    pg.wait_for_selector(".left-panel", timeout=10000)      # 登录后才有页壳
```

### 前置 B：**点分类树条目必须先展开 `<details>`**（否则 30s 超时）

分类项位于**默认折叠**的 `<details>` 内（`app/templates/partials/tree_panel.html:126-149`，无 `open` 属性、
无自动展开逻辑）⇒ `page.click(".tree-label")` 会因 **element is not visible** 超时（实测 call log 明确如此）。
且分类树是 `await fetch('/tree/all')` **异步渲染**的，与 `#qa-root` 可交互之间存在竞态。

⇒ 统一用两个 helper（同样放 runner 层，各用例共用）：

```python
def click_first_tree_label(pg):
    """展开第一个折叠分类组，再点其中的条目（details 未展开时 .tree-label 不可见）。"""
    pg.locator("details").first.click()                      # 展开
    pg.locator(".tree-label").first.click()

def wait_tree_filter_seeded(pg):
    """等分类树渲染完（异步 fetch）。注意用 state='attached'：
    折叠 details 内的元素等 visible 会必然超时。"""
    pg.wait_for_selector(".tree-label", state="attached", timeout=10000)
```

> **后续 Task（T2~T5）追加探针时**：凡需点击分类树条目，用 `click_first_tree_label(pg)`；
> **不要**在用例里直接写 `page.click(".tree-label >> nth=0")`（在折叠的 `<details>` 内，
> 必然 30s 超时；首版 8 条探针里有 5 条踩了这个坑）。
>
> ⚠️ **两个 helper 的语义与顺序（2026-09-23 更正，首版写反过）**：
> - `click_first_tree_label(pg)`：**展开**第一个 `<details>` 并点其第一个条目——用在**全新页面**（尚无选中项）。
> - `wait_tree_filter_seeded(pg)`：等 **`.tree-label.active`（已选中节点）** 出现——它的用途是**核实
>   「URL 回填 / 刚点的那一下」确实记进了 store**，**不是**点击前的门禁。
>   在全新页面（无任何选中项）上先调它会**必然 10s 超时**（T4 施工时实测踩到）。
> - **正确顺序：需要确认选择生效时 → 先 `click_first_tree_label` 再 `wait_tree_filter_seeded`**。

### 前置 C：需要「AI 有回答」的 Task（T4/T5）用 **mock LLM**，不要用真实模型

T4/T5 的探针要断回答区渲染、续聊、流式渐进，**必须**有稳定可预期的回答。用真实模型既耗配额、
内容又不可控。控制器已备好 mock（**临时工具，不入仓**）：`%TEMP%/qa_mock_llm.py`，
监听 `127.0.0.1:8199`，`POST /v1/chat/completions` 非流式返回一次性 JSON、流式逐帧 SSE
（帧间 sleep，故意留出可观测的"生成中"窗口）。

配置**只改隔离库副本**（QA 经 `body.backend` → `get_backend(None)` → settings 的 `ai.backend`；
**不存在** `ai.backend.qa` 这个键）：

```sql
-- 在 data/_probe_qa.db 上执行（不要动 dev 库）
INSERT OR REPLACE INTO settings (key, value) VALUES
  ('ai.backend',         'custom'),
  ('ai.custom.base_url', 'http://127.0.0.1:8199/v1'),
  ('ai.custom.api_key',  'mock'),
  ('ai.custom.model',    'mock-model');
```

mock 的固定回答含**加粗 Markdown** 与 **《GB 50204》8.2.1** 条文引用 ⇒ 顺带可验证渲染管线与超链接改写。

> ⚠️ **探针与 mock 强绑定（有意为之）**：T4-R1 起，共享助手 `_qa_ask` 要求回答正文**必须含 mock 的确定性标记
> `GB 50204`**（否则判红）——这是为堵「失败泡文本非空 ⇒ 假绿」那个洞而特意加的一层（`MOCK_ANSWER_MARKER` 常量）。
> ⇒ **不要拿真实模型跑 t1~t5**：真实回答不含该标记，会成片假红。
> 反之，若确实要用真实后端验一次观感，请手工在浏览器里走一遍，而不是跑探针。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/routes/qa_routes.py` | `GET /qa` 整页路由（T1）+ JSON 接口 | 修改 |
| `app/templates/partials/qa_page.html` | QA 页（对话区 + 会话管理） | **新建** |
| `app/templates/partials/qa_panel.html` | 旧弹窗内容 | **删除**（T1 后无引用） |
| `app/templates/base.html` | 页壳、脚本标签；移除 QA 弹窗 overlay | 修改 |
| `app/templates/partials/tree_panel.html` | 左栏（QA 入口、CE tooltip） | 修改 |
| `static/components/qa.js` | QA 组件（会话、筛选、URL 携带、流式渲染） | 重写 |
| `static/components/tree.js` | 分类树（`view` 判断 / `isQaView`） | 修改 |
| `static/components/search.js` | 搜索框（`view` 判断） | 修改 |
| `static/components/md-render.js` | 统一渲染管线 | **不改**（流式期间走纯文本，收尾才用完整管线） |
| `static/app.css` | QA 页布局与折叠 | 修改 |
| `scripts/probe_qa_ui.py` | Playwright 行为回归套件（34 条，**保留**——2026-09-24 用户裁定） | **新建** |

> **本计划不改** `search.js`/`tree.js` 的 htmx swap 目标，`result_list.html` 与 `#search-results` 语义完全不动——
> 这是选择整页导航换来的收益（检索页零结构改动）。

---

## Task 1: `/qa` 整页路由 + 入口链接 + 筛选进 URL

**Files:**
- Modify: `app/routes/qa_routes.py`（新增 `GET /qa`）
- Create: `app/templates/partials/qa_page.html`（本 Task 先放骨架，T2 填布局）
- Modify: `app/templates/partials/tree_panel.html:115-117`（入口按钮 → 链接）
- Modify: `static/components/qa.js`（`buildQaUrl()` / `seedFiltersFromUrl()` / `syncQaUrl()`）
- Modify: `static/components/tree.js`（`selectFilter` 触发 URL 同步）
- Modify: `static/components/search.js`（状态/CE 变更触发 URL 同步）
- Modify: `app/templates/base.html`（移除 QA 弹窗 overlay 与 `openQA`/`closeQA`；三个脚本版本号递增）
- Modify: `app/qa/sessions.py`（**仅 1 条注释**：删掉指向已删模板的行号引用，零业务逻辑）
- Test: `scripts/probe_qa_ui.py`

> ⚠️ 首版 Files **漏列**了 `base.html` 与 `app/qa/sessions.py`，而 Step 9 的 `git add` 里两者都在
> ⇒ 与 T14 同类（Files 漏列 = 漏检）。已补。

**Interfaces:**
- Produces: 路由 `GET /qa` → `base.html` + `left_content=partials/tree_panel.html` + `center_content=partials/qa_page.html`（与 `/rules`、`/lexicon` 完全同形）
- Produces: 全局函数 `buildQaUrl() -> str`（由共享 store 生成 `/qa?...`）、`seedFiltersFromUrl()`、`syncQaUrl()`（三者均定义在 `qa.js`，供 `tree.js`/`search.js` 延迟调用——项目已有此模式，参见 `search.js` 的 `resetCeSuggest()` 被 `tree.js` 调用）
- 行为契约：QA 页是**整页导航**，Alpine 走 DOMContentLoaded 初始化；筛选经 URL 携带（store 在整页导航时必然重置）

> **为什么是整页导航**（评审决定 D4）：
> 首版设计让 QA 走 htmx 局部替换，唯一理由是「把检索页已选的筛选带过去」——但该需求从未被提出，
> 代价却是：① 需要 `#main-content` 稳定壳层并改动**正在工作的** `search.js`/`tree.js` 的 swap 目标
> （牵动翻页、回顶、翻页建议三条逻辑）；② QA 成为本项目首个「被 htmx 揳进中栏的 Alpine 组件」，
> 而 Alpine 在 swap 场景下能否接管**无既有先例可依**；③ htmx 方案下浏览器后退按钮回不到检索页。
> 改用整页导航后：与 `/specs`、`/rules`、`/lexicon` 同形（本项目已验证过无数次），
> 上述三项代价全部消失，而「筛选携带」改由 URL 承担——顺带得到可分享链接与正常的前进后退。

- [ ] **Step 1: 写失败探针**

```python
# scripts/probe_qa_ui.py
"""QA 界面改造的一次性验证探针（验证完删除）。

用法：先起隔离实例（本计划「前置」节），再
  D:/Python/python.exe scripts/probe_qa_ui.py t1
"""
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"


def t1_qa_entry_is_a_page_link(page):
    """正常场景：QA 是整页导航（URL 变为 /qa），不是弹窗也不是局部替换。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    assert page.url.endswith("/qa") or "/qa?" in page.url, \
        f"未导航到 /qa，当前 URL：{page.url}"
    assert page.locator("#qa-modal-overlay").count() == 0, "旧弹窗应已移除"


def t1_left_panel_present_on_qa_page(page):
    """正常场景：QA 页的左栏分类树照常在位（整页导航下由 base.html 渲染）。

    ⚠️ 选择器**必须限定在左栏内**（`.left-panel input[type=search]`）：QA 页在 T2 之后
    会多出**会话管理栏的搜索框**，全局 `input[type=search]` 计数变 2 ⇒ 本用例在 T2 后必红，
    而完成标准要求 t1~t5 全绿。全局计数是个"随别的 Task 变化"的隐式耦合，不能留。
    """
    page.goto(f"{BASE}/qa")
    assert page.locator(".left-panel input[type=search]").count() == 1, "左栏搜索框缺失"
    assert page.locator(".tree-container").count() == 1, "左栏分类树缺失"


def t1_filters_carry_into_qa_via_url(page):
    """正常场景（D4 的核心）：检索页已选筛选经 URL 带入 QA 页。

    整页导航会重置 Alpine store，筛选必须靠 URL 携带才不丢。
    """
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    wait_tree_filter_seeded(page); click_first_tree_label(page)          # 选中一个分类树条目
    page.wait_for_timeout(500)
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    # ⚠️ 不要写成 `assert "?" in page.url`——那是**恒真**的：`buildQaUrl()` 总会带上
    # `status_filter`（store 默认"仅现行"），所以 query 串必然非空，删掉整个维度回填它照样绿。
    # 断言必须落到**分类维度键**上（下面这条 + 紧随的 active 计数共同钉住"筛选真的带过来了"）
    assert "dim" in page.url, f"QA URL 未携带分类维度参数：{page.url}"
    active = page.locator(".tree-label.active").count()
    assert active >= 1, "QA 页未回填筛选（分类树无选中项）"


def t1_qa_page_filters_survive_reload(page):
    """边界场景：QA 页刷新后筛选仍在（URL 持久化的意义所在）。"""
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    wait_tree_filter_seeded(page); click_first_tree_label(page)
    page.wait_for_timeout(500)
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    n_before = page.locator(".tree-label.active").count()
    page.reload()
    page.wait_for_selector("#qa-root", timeout=10000)
    assert page.locator(".tree-label.active").count() == n_before, \
        "刷新后筛选丢失——URL 回填未生效"


def t1_search_page_unaffected(page):
    """异常场景（回归）：检索页三条路径不受影响。

    本 Task **不改** search.js/tree.js 的 htmx swap 目标——这是选择整页导航
    换来的好处之一：检索页零改动。
    """
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    wait_tree_filter_seeded(page); click_first_tree_label(page)          # 分类树立即重搜（检索页语义）
    page.wait_for_selector("#search-results", timeout=10000)
    page.evaluate("document.querySelector('.center-panel-v2').scrollTop = 500")
    page.fill("input[type=search]", "钢筋")
    page.press("input[type=search]", "Enter")
    page.wait_for_timeout(1200)
    assert page.evaluate(
        "document.querySelector('.center-panel-v2').scrollTop") == 0, \
        "换词搜索后未回顶（检索页回顶逻辑被改坏）"


def t1_tree_click_in_qa_page_does_not_navigate(page):
    """正常场景（D8 核心）：QA 页内点分类树不跳回检索页。

    这是「切换筛选只影响下一轮问答检索」成立的前提——若仍触发检索，
    用户会被弹回检索结果页。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    wait_tree_filter_seeded(page); click_first_tree_label(page)
    page.wait_for_timeout(800)
    assert page.locator("#qa-root").count() == 1, \
        "点分类树后 QA 页消失了——说明仍触发了检索"
    assert "/qa" in page.url, f"被弹回检索页：{page.url}"


def t1_tree_click_in_search_page_still_searches(page):
    """异常场景（回归）：检索页内点分类树仍要立即重搜。"""
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    wait_tree_filter_seeded(page); click_first_tree_label(page)
    page.wait_for_selector("#search-results", timeout=10000)
    assert page.locator("#qa-root").count() == 0
    assert page.locator("#search-results").count() == 1


# ⚠️ 键名 = 该 Task 的编号本身（"t1".."t5"）。后续 Task 追加用例时**必须同时把函数名加进对应键**，
#    否则 `probe_qa_ui.py tN` 会 KeyError。本计划首版此处只登记了 "t1"，且 T3~T5 的调用键名整体错位一位
#    （T3 调 t4、T4 调 t5、T5 调 t6），已修——**调用键与 Task 编号必须一致**。
def t1_qa_enter_search_returns_to_search_page(page):
    """正常场景（用户明确期望）：QA 页按回车搜索 = 回到检索页。

    没有这条探针的话，「QA 页搜索后地址栏仍停在 /qa、内容却已是检索结果」这个半途状态
    不会被任何东西抓到。删掉 `search()` 里那句 `history.pushState(null, '', '/')` → 本用例必红。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".left-panel input[type=search]", "混凝土")
    page.press(".left-panel input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    assert page.locator("#qa-root").count() == 0, "搜索后 QA 界面应已被检索结果替换"
    assert "/qa" not in page.url, f"地址栏未回落到检索页（应不再是 /qa）：{page.url}"


CASES = {"t1": [t1_qa_entry_is_a_page_link,
                t1_left_panel_present_on_qa_page,
                t1_filters_carry_into_qa_via_url,
                t1_qa_page_filters_survive_reload,
                t1_search_page_unaffected,
                t1_tree_click_in_qa_page_does_not_navigate,
                t1_tree_click_in_search_page_still_searches,
                t1_qa_enter_search_returns_to_search_page]}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "t1"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1600, "height": 900})
        n = 0
        for fn in CASES[which]:
            fn(pg)
            print(f"PASS {fn.__name__}")
            n += 1
        # 自报条数：各 Task 的 Expected 一律照这一行核对，**不要在计划里手写死条数**
        # （首版手写的「3 行 / 5 行 PASS」与 CASES 实际登记数不符，是同一类漂移）
        print(f"== {which}: {n}/{len(CASES[which])} passed ==")
        browser.close()
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t1`
Expected: FAIL — `/qa` 尚未存在（按钮仍是 `$dispatch('open-qa-modal')`，弹窗已无接收方）

- [ ] **Step 3: 实现路由与骨架模板**

在 `app/routes/qa_routes.py` 新增（放在会话管理接口之前）：

```python
@router.get("/qa")
async def qa_page(request: Request):
    """QA 页（整页导航，与 /rules、/lexicon 同形）。

    选整页而非 htmx 局部替换的取舍见设计文档 D4：左栏分类树照常在位，
    Alpine 走 DOMContentLoaded 初始化（与项目其它页面一致，无需额外机制）。
    """
    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/qa_page.html",
    })
```

创建 `app/templates/partials/qa_page.html` 骨架（T2 填入完整布局）：

```html
<!-- AI 问答页（整页导航，与规范/规则/词库同形）
     布局在 T2 填入；此处先给探针所需的最小结构 -->
<div id="qa-root" x-data="qaView()" class="qa-root">
    <div class="qa-thread">
        <div class="qa-thread-header">
            <span>🤖 AI 智能问答</span>
            <button type="button" class="outline" id="qa-session-toggle"
                    style="font-size:0.75rem;padding:0.1rem 0.4rem;margin:0"
                    @click="sessionsCollapsed = !sessionsCollapsed"
                    x-text="sessionsCollapsed ? '◂ 会话' : '会话 ▸'">会话 ▸</button>
        </div>
        <div class="qa-messages" x-ref="msgBox"></div>
        <div class="qa-composer">
            <textarea x-model="input" rows="3" style="flex:1"></textarea>
            <button type="button" @click="send()">发送</button>
        </div>
    </div>
    <aside class="qa-sessions" x-show="!sessionsCollapsed" x-cloak>会话管理</aside>
</div>
```

- [ ] **Step 4: 入口按钮改为链接**

`app/templates/partials/tree_panel.html`，把 QA 按钮改为：

```html
        <!-- QA 页是整页导航（与 📚规范 / 📋规则 / 📖词库 同形）。
             href 由 buildQaUrl() 在点击时按当前筛选拼出，使 QA 页继承检索条件 -->
        <button type="button" class="outline" style="width:100%;font-size:0.8rem"
                onclick="window.location.href = buildQaUrl()">
            🤖 AI问答
        </button>
```

同时删除 `base.html` 的 QA 弹窗相关：`@open-qa-modal="openQA()"` 属性、`openQA()` / `closeQA()` 方法、
整个 `<div id="qa-modal-overlay"> … </div>` 块，以及 **`partials/qa_panel.html` 文件本身**（删除前确认无残留引用）：

```bash
grep -rn "qa_panel.html" app/ static/ | grep -v "__pycache__"
```
Expected: **仅剩注释级引用**——实测 `app/qa/sessions.py:302` 的一句注释里提到 `qa_panel.html:49`（记的是当时
的行号出处）。这是**文档性引用**、不影响运行；但删掉模板后它就悬空了，故**本 Task 顺手把该注释改成
可自解释的表述**（例如「QA 面板底部原有的参考条文行」），而不是留一个指向已删文件的路径。
除该注释外不应再有输出；若还有其它引用，先改掉再删。

**保留** `#clause-modal-overlay`（条文详情弹窗，本次明确不改）与 `partials/settings_dialog.html`。

> ⚠️ **「无残留」的判据必须写对**（首版给错了）：`settings_dialog.html:6` **复用了 CSS 类**
> `class="qa-modal-overlay"`，而该文件要保留 ⇒ 对 `qa-modal-overlay` 的 grep **必然有输出**
> （3 行：`settings_dialog.html:6`、`app.css:66`、`app.css:215`）。正确的判据按**事件名/方法名/id** 查：
> ```bash
> grep -rn 'open-qa-modal\|openQA\|id="qa-modal-overlay"' app/ static/ | grep -v "__pycache__"
> ```
> Expected: **无输出**（这三样才是真正被删除的东西；CSS 类名与 `#qa-modal-overlay` 是两回事）。
> **顺带**：`static/app.css:215` 的注释「必须高于 QA 弹窗」已过时（该类现仅服务设置弹窗）
> ——该文件不在 T1 的 Files 内，故**由 T2 一并订正**（T2 本来就改 `app.css`）。

- [ ] **Step 5: 实现 URL 筛选携带**

在 `static/components/qa.js` 顶部（模块级，供 `tree.js`/`search.js` 调用）新增：

```js
// ── QA 页 URL 筛选携带（D4）──
// 整页导航会重置 Alpine store，筛选靠 URL 携带才不丢；顺带得到可分享链接。

// 由共享 store 生成 QA 页 URL；无筛选时退化为裸 /qa
function buildQaUrl() {
    const ss = window.Alpine && window.Alpine.store && Alpine.store('searchState');
    if (!ss) return '/qa';
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(ss.filters || {})) {
        if (Array.isArray(v)) v.forEach(x => p.append(k, x));
        else p.append(k, v);
    }
    const sf = ss.buildStatusFilter();
    if (sf) p.append('status_filter', sf);
    if (ss.includeNonClause) p.append('include_non_clause', '1');
    const qs = p.toString();
    return qs ? `/qa?${qs}` : '/qa';
}

// 在 QA 页内同步 URL（replaceState：不污染历史栈，避免每次勾选都多一条记录）
function syncQaUrl() {
    if (typeof isQaView === 'function' && isQaView()) {
        history.replaceState(null, '', buildQaUrl());
    }
}
```

在 `qaView` 组件中新增 `seedFiltersFromUrl()` 并在 `init()` 首行调用：

> ⚠️ **T1 阶段不得调用 `loadSessions()`**——它属于 **T4**（本 Agent 在 T1 只建骨架与 URL 机制）。
> 首版计划此处把 `await this.loadSessions()` 也写进了 `init()`，而 T1 的 `qaView` 里没有该方法
> ⇒ QA 页 Alpine 初始化**立刻抛 TypeError**，T1 的探针一条都跑不到断言。
> T4 实现 `loadSessions()` 时再把这一行加进 `init()`（T4 的说明里已明写必须保留 `seedFiltersFromUrl()`）。

```js
        async init() {
            this.seedFiltersFromUrl();   // T1：URL → store 回填
            // await this.loadSessions();  ← T4 引入 loadSessions() 后在此启用
            this.scrollToBottom();
        },

        // 从 URL 回填共享筛选状态。维度用 getAll（同维多选）。
        seedFiltersFromUrl() {
            const ss = this.$store.searchState;
            const p = new URLSearchParams(window.location.search);
            const filters = {};
            for (const k of QA_DIM_KEYS) {
                const vals = p.getAll(k).filter(Boolean);
                if (vals.length) filters[k] = vals;
            }
            ss.filters = filters;
            const sf = p.get('status_filter');
            if (sf !== null) {
                // 与 buildStatusFilter() 的取值域对称：'现行' / '现行,修订中' / '修订中'
                ss.statusCurrent = sf.includes('现行');
                ss.statusRevising = sf.includes('修订中');
            }
            ss.includeNonClause = p.get('include_non_clause') === '1';
        },
```

并在 `qa.js` 顶部新增维度键常量（与后端 `dim1_hierarchy…dim6_material` 一一对应，禁止散落字面量）：

```js
// 维度参数名。**必须与后端 app/models.py 的 QA_DIM_FIELDS 保持一致**
// （前端无法跨语言复用该常量，只能镜像；后端有 test_qa_dim_fields_matches_request_model
//  盯着常量与 QaRequest 的一致性，此处靠代码评审与 URL 回填探针发现漂移。
//  T1 的 t1_filters_carry_into_qa_via_url 会在维度名漂移时失败。）
const QA_DIM_KEYS = ['dim1_hierarchy', 'dim1_industry', 'dim1_nature',
                     'dim2_stage', 'dim3_usage', 'dim4_specialty',
                     'dim5_location', 'dim6_material'];
```

- [ ] **Step 6: 接上筛选变更时的 URL 同步**

`static/components/tree.js`：新增 `isQaView()` 判据，并在 `selectFilter()` 中据此分流。

```js
// 当前是否处于 QA 视图：以 DOM 存在性判断。
// 不用 Alpine 生命周期钩子——htmx/DOM 替换场景下 destroy() 是否触发未被官方文档化，
// 用 DOM 判据零风险且同样准确（项目其它地方也按 DOM 状态判断）。
function isQaView() {
    return !!document.getElementById('qa-root');
}
```

```js
        selectFilter(dimension, value) {
            const next = { ...this.$store.searchState.filters };
            const arr = Array.isArray(next[dimension]) ? next[dimension].slice() : [];
            const idx = arr.indexOf(value);
            if (idx >= 0) arr.splice(idx, 1); else arr.push(value);
            if (arr.length) next[dimension] = arr; else delete next[dimension];
            this.$store.searchState.filters = next;
            // QA 页：筛选只更新共享状态（影响下一轮问答检索）+ 同步 URL，
            // 不发起检索——否则点分类树会把用户弹回检索页（设计文档 D8）
            if (isQaView()) {
                if (typeof syncQaUrl === 'function') syncQaUrl();
                return;
            }
            this.dispatchSearch();
        },
```

`static/components/search.js` 的 `onStatusChange()` / `onCeChange()` 同样在早期返回前调用 `syncQaUrl()`：

```js
        onCeChange() {
            if (this.ceRerank) {
                showSearchToast('CE精排已开启，请耐心等待搜索结果');
            }
            if (isQaView()) { syncQaUrl(); return; }
            this.search();
        },

        onStatusChange() {
            if (!this.statusCurrent && !this.statusRevising) {
                showSearchToast('注意：当前展示结果未过滤非现行规范', 4000);
            }
            if (isQaView()) { syncQaUrl(); return; }
            this.search();
        },
```

> `isQaView()` 已在上一步定义于 `tree.js`（单一实现，供 `tree.js`/`search.js`/`qa.js` 共用）。
> **不要**在 `qa.js` 里另写一份 DOM 判据——那会让两处判据将来可能漂移。

> ⚠️ **还差一处：QA 页按回车搜索的落点**（本计划首版漏了，会留下自相矛盾的半途状态）。
> 左侧搜索框的 Enter 走 `search()`，它做的是 `htmx.ajax('GET', '/search?...', {target: '.center-panel-v2'})`
> ——**片段换入**。于是在 QA 页按回车时：QA 界面被替换成检索结果，但**地址栏仍停在 `/qa`**
> ⇒ 刷新或后退又变回 QA 页，两边对不上。
> **为什么不能直接"导航到检索页"**：`GET /search` 返回的是**片段**（`partials/result_list.html`，
> 见 `app/routes/search_routes.py:120`），`GET /` 也不接受关键词（`app/main.py:165` 只渲染欢迎页）
> ⇒ 本项目**没有**「整页导航到检索结果」这条路径，htmx 换入是唯一形态。
> **定案（与用户对「回车即展示搜索结果」的期望一致）**：让 QA 页的回车**表现得与检索页完全一致**——
> 照常 htmx 换入结果，**并**把地址栏回落到 `/`：
>
> ```js
> // search.js 的 search()：htmx.ajax(...) 之后追加——
> // 若刚才在 QA 页，换入结果后把地址栏推回检索页，避免"内容已是检索页、URL 还是 /qa"
> if (typeof isQaView === 'function' && isQaView()) history.pushState(null, '', '/');
> ```
>
> 换入后 `#qa-root` 随 `.center-panel-v2` 一起消失 ⇒ 之后 `isQaView()` 自然为 false，
> 左栏筛选项点击回到「检索页语义」⇒ 与"在检索页搜索完"的状态**逐字等价**。
> **探针**：`t1_qa_enter_search_returns_to_search_page`（见 Step 1 的探针清单）

- [ ] **Step 7: 提升静态资源版本号并重启**

`base.html`：`qa.js?v=18` → `?v=19`；`tree.js?v=6` → `?v=7`；`search.js?v=9` → `?v=10`。

按项目 CLAUDE.md 三·1~4 重启 8123 实例（全杀残留进程）。

- [ ] **Step 8: 运行探针确认通过**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t1`
Expected: 全部 `PASS` —— **条数由脚本自报，不要在计划里写死**（首版手写的「3 行 / 5 行」与 CASES 实际登记数不符，且 T3~T5 的 Run 键名整体错位了一位，已修）。脚本会打印本次运行的用例名与 PASS/FAIL 计数，照它核对。

- [ ] **Step 9: 提交**

```bash
git add app/routes/qa_routes.py app/templates/partials/qa_page.html \
        app/templates/partials/tree_panel.html app/templates/base.html \
        app/qa/sessions.py \
        static/components/qa.js static/components/tree.js static/components/search.js \
        scripts/probe_qa_ui.py
git rm app/templates/partials/qa_panel.html
git commit -m "feat: QA 改为整页导航（/qa），筛选经 URL 携带"
```

---

## Task 2: QA 页布局与折叠

**Files:**
- Modify: `app/templates/partials/qa_page.html`（把 T1 的骨架换成完整布局）
- Modify: `static/app.css`（QA 页布局与折叠；**并顺手订正 `:215` 的过时注释**——它写「必须高于 QA 弹窗
  （`.qa-modal-overlay` z-index:9999)」，而该 CSS 类在 T1 删掉旧弹窗后**仅服务设置弹窗**（`settings_dialog.html:6`
  复用同名类），已不指涉 QA 弹窗。T1 施工时发现但 `app.css` 不在其 Files 内，故移交本 Task）
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `GET /qa` 路由与骨架模板（T1）
- Produces: DOM 结构 `#qa-root > (.qa-thread > (.qa-thread-header, .qa-messages, .qa-effective-filters, .qa-composer), .qa-sessions)`，其中 `.qa-sessions` 可折叠
- 不变量：`.qa-messages` 是消息滚动容器、`.qa-composer` 贴其底部；`.qa-sessions` 折叠**不得**影响 `.qa-composer` 位置

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`（同时把新用例登记进 `CASES["t2"]`）：

```python
def t2_layout_structure_present(page):
    """正常场景：完整布局就位（对话区 / 消息区 / 输入区 / 会话管理四块）。"""
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    for sel in (".qa-thread", ".qa-messages", ".qa-composer", ".qa-sessions"):
        assert page.locator(sel).count() == 1, f"缺少 {sel}"


def t2_sessions_panel_collapses_without_moving_composer(page):
    """边界场景（核心）：折叠会话管理栏后，输入框纵向不动、对话区横向延伸。

    两条缺一不可：
      - 纵向不动：输入框贴在对话栏底部，不能随会话管理折叠被带走；
      - 横向延伸：折叠让出的空间要回流给对话区（含消息区与输入框），
        填满空白而不是留一条空缺。

    几何已用最小复现实测确认（1600x900 视口下 842 → 1219，右边缘 +377）。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    before = {s: page.locator(s).bounding_box()
              for s in (".qa-composer", ".qa-messages", ".qa-thread")}
    page.click("#qa-session-toggle")
    page.wait_for_timeout(300)
    after = {s: page.locator(s).bounding_box()
             for s in (".qa-composer", ".qa-messages", ".qa-thread")}
    assert all(before.values()) and all(after.values())

    assert abs(before[".qa-composer"]["y"] - after[".qa-composer"]["y"]) < 2, \
        f"折叠后输入框纵向位移 {after['.qa-composer']['y'] - before['.qa-composer']['y']}px，应保持不动"

    for sel, name in ((".qa-messages", "对话消息区"),
                      (".qa-composer", "输入框"),
                      (".qa-thread", "对话栏")):
        assert after[sel]["width"] > before[sel]["width"], \
            f"折叠后{name}应变宽以填充空白：{before[sel]['width']} -> {after[sel]['width']}"

    # 会话管理栏必须真的收起（否则上面的变宽可能是布局重叠造成的假象）
    assert page.locator(".qa-sessions").is_hidden(), "折叠后会话管理栏应不可见"


def t2_qa_page_not_shown_on_other_pages(page):
    """异常场景（回归）：其它页面不得冒出 QA 界面。"""
    for path in ("/specs", "/rules", "/lexicon"):
        page.goto(f"{BASE}{path}")
        assert page.locator("#qa-root").count() == 0, f"{path} 不该出现 QA 界面"
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t2`
Expected: FAIL —— **但不要拿"骨架缺结构"当 RED 证据**：`.qa-thread` / `.qa-messages` / `.qa-composer` / `.qa-sessions`
这些选择器 **T1 的骨架就已经有了**（见 T1 Step 3），所以 `t2_layout_structure_present` 这类用例在 T1 建好后就通过。
本步应以**折叠/宽度类**用例（依赖 T2 才落地的 `sessionsCollapsed` 数据字段与 `.qa-sessions` 宽度规则）
作为失败证据；若跑出来"全部通过"，说明这些用例没有真正验收 T2 的交付物，**必须先改用例再继续**。

- [ ] **Step 3: 实现 QA 页模板**

> ⚠️ **先补 `sessionsCollapsed` 数据字段**：模板里的 `@click="sessionsCollapsed = !sessionsCollapsed"`
> 与 `x-show="!sessionsCollapsed"` 依赖它。该字段**首版计划只在 T4 重写的组件里声明**，而 T2 的折叠探针
> （`t2_sessions_panel_collapses_without_moving_composer`）在 T2 就要它生效。
> ⇒ 本步在 `static/components/qa.js` 的 `qaView` 数据块里**新增 `sessionsCollapsed: false`**
> （T4 重写组件时保留它）。不要依赖"Alpine 会对新增键做响应式跟踪"这类未在本项目验证过的行为——
> 声明式地给出初值，折叠的初始态与绑定才都是确定的。
>
> 另注：`.qa-sessions` 的 `x-cloak` 需要 `[x-cloak]{display:none}` 样式在 CSS 里生效（Step 4 一并确认），
> 否则首帧会闪一下会话栏。

把 T1 建立的骨架 `app/templates/partials/qa_page.html` 换成完整布局：

```html
<!-- AI 问答页（整页导航，与规范/规则/词库同形）
     布局：对话区（主，输入框贴底） | 会话管理（右，可向右折叠）
     注意：会话管理折叠只收走自己一列，输入框位置不受影响 -->
<div id="qa-root" x-data="qaView()" class="qa-root">
    <div class="qa-thread">
        <div class="qa-thread-header">
            <span>🤖 AI 智能问答</span>
            <div style="display:flex;gap:0.5rem;align-items:center">
                <span class="qa-rerank-badge" x-show="rerankUsed" x-text="rerankBadge()"
                      :title="rerankTip()" style="font-size:0.72rem;color:var(--pico-muted-color)"></span>
                <button style="background:none;border:none;cursor:pointer;font-size:1rem;padding:0;line-height:1;margin:0"
                        @click="$dispatch('open-settings', {detail:{tab:'ai'}})"
                        title="AI 设置" aria-label="AI 设置">⚙️</button>
                <button type="button" class="outline" id="qa-session-toggle"
                        style="font-size:0.75rem;padding:0.1rem 0.4rem;margin:0"
                        @click="sessionsCollapsed = !sessionsCollapsed"
                        x-text="sessionsCollapsed ? '◂ 会话' : '会话 ▸'"
                        title="展开/折叠会话管理"></button>
            </div>
        </div>

        <div class="qa-messages" x-ref="msgBox">
            <template x-for="(msg, i) in messages" :key="msg.uid || i">
                <div class="qa-msg" :class="msg.role === 'user' ? 'qa-user' : 'qa-bot'">
                    <template x-if="msg.role === 'assistant'">
                        <div>
                            <!-- 易混淆术语提示（x-text 渲染，杜绝 HTML 注入）；仅提示、不改写答案 -->
                            <template x-if="msg.confusable && msg.confusable.length">
                                <div class="qa-confusable">
                                    <strong>⚠️ 术语区分提示</strong>
                                    <template x-for="h in msg.confusable" :key="h.a + '|' + h.b">
                                        <div style="margin-top:0.15rem">
                                            <strong x-text="h.a"></strong> 与 <strong x-text="h.b"></strong>：
                                            <span x-text="h.distinguish"></span>
                                        </div>
                                    </template>
                                </div>
                            </template>
                            <!-- 生成期间 msg.html 是转义纯文本（.streaming 的 pre-wrap 保留换行）；
                                 收到 done 后由 renderMarkdown 换成完整管线结果 -->
                            <div class="qa-answer" :class="{ streaming: msg.streaming }"
                                 x-html="msg.html"></div>
                            <!-- 当轮实际生效筛选（D5）：回看历史时据此还原
                                 「这条答案是在什么筛选下产生的」 -->
                            <template x-if="msg.filtersText">
                                <div class="qa-msg-filters" x-text="'筛选：' + msg.filtersText"></div>
                            </template>
                            <template x-if="msg.filteredOut > 0">
                                <div class="qa-relax-hint">
                                    <span x-text="'⚠️ 当前分类筛选下候选不足（全局命中 ' + msg.filteredOut + ' 条）'"></span>
                                    <!-- 文案只说「分类筛选」：relaxed 只清分类维度，
                                         状态过滤与前言设置仍然生效（评审 D13） -->
                                    <button type="button" class="outline"
                                            style="font-size:0.75rem;padding:0.1rem 0.4rem;margin:0 0 0 0.4rem"
                                            @click="relax()">放宽分类筛选</button>
                                </div>
                            </template>
                            <template x-if="msg.sources && msg.sources.length">
                                <div class="qa-sources">
                                    <small style="color:var(--pico-muted-color);line-height:1.6">📎 参考条文：</small>
                                    <template x-for="s in msg.sources" :key="s.code + '|' + s.clause_no">
                                        <a href="javascript:void(0)" @click.prevent="openClause(s.clause_id)"
                                           x-text="'《' + s.code + '》' + s.clause_no"
                                           class="qa-source-link"></a>
                                    </template>
                                </div>
                            </template>
                        </div>
                    </template>
                    <template x-if="msg.role === 'user'">
                        <div x-text="msg.content"></div>
                    </template>
                </div>
            </template>
            <!-- 阶段进度：覆盖流式之前的死时间（检索/精排发生在首字节之前） -->
            <div class="qa-stage" x-show="loading" x-text="stageText"></div>
        </div>

        <!-- 本轮生效筛选（D5）：场景 2（回复中切换）只能影响下一轮，
             这条常驻小字让「这次没生效」不再静默 -->
        <div class="qa-effective-filters" x-show="effectiveFiltersText">
            <span x-text="'本轮生效：' + effectiveFiltersText"></span>
            <span x-show="filtersChanged()" class="qa-filters-pending"
                  x-text="'· 已修改，将在下一轮生效'"></span>
        </div>
        <div class="qa-composer">
            <!-- 问答模式切换（**必须保留**：设计文档 §4.8「必须保留的既有功能」+
                 用户明确约束「原文摘抄/综合问答模式切换别搞废了，只调整 UI」）。
                 位置从旧弹窗的顶部挪到输入框上方——它与「发送」同属一次提问的输入设置。
                 ⚠️ 勾选框的 inline `width:1rem;height:1rem` **不能省**：本项目 pico 主题下
                 经 `appearance:none` 重绘的 checkbox 若落到 `width:auto` 会塌缩成 4px（已踩过的坑）。
                 文案与旧面板逐字一致，便于对照「功能未被删」。
                 切换时机：与「本轮生效」同理，只影响**下一轮**提问（send() 时读 mode）。 -->
            <label class="qa-mode-switch"
                   style="display:flex;align-items:center;gap:0.25rem;font-size:0.75rem;margin:0 0 0.35rem 0;cursor:pointer;width:fit-content">
                <input type="checkbox" style="width:1rem;height:1rem;margin:0"
                       :checked="mode === 'verbatim'" @change="toggleMode()">
                原文摘抄模式（禁止归纳，仅摘抄标注来源）
                <small x-text="mode === 'verbatim' ? '🔤 原文摘抄' : '📖 综合问答'"
                       style="color:var(--pico-muted-color)"></small>
            </label>
            <textarea x-model="input" @keydown.enter.prevent="send()"
                      placeholder="输入问题并按 Enter 发送..." rows="3"></textarea>
            <div class="qa-composer-btns">
                <button type="button" @click="send()" :disabled="loading || !input.trim()">发送</button>
                <button type="button" class="outline" @click="newSession()"
                        :disabled="loading">＋ 新会话</button>
            </div>
        </div>
    </div>

    <!-- 会话管理栏：可向右折叠，折叠后对话区自动变宽 -->
    <aside class="qa-sessions" x-show="!sessionsCollapsed" x-cloak>
        <div class="qa-sessions-search">
            <input type="search" x-model="searchKeyword" @keyup.enter="runSearch()"
                   placeholder="搜索历史消息…" style="font-size:0.8rem">
        </div>
        <div class="qa-sessions-list">
            <template x-if="searchHits.length">
                <div class="qa-search-hits">
                    <template x-for="h in searchHits" :key="'hit' + h.id">
                        <div class="qa-hit" @click="jumpToHit(h)">
                            <div class="qa-hit-title" x-text="h.session_title"></div>
                            <div class="qa-hit-body" x-text="h.content.slice(0, 40)"></div>
                        </div>
                    </template>
                </div>
            </template>
            <template x-for="s in sessions" :key="s.id">
                <div class="qa-session-item" :class="{ active: s.id === currentSessionId }"
                     @click="openSession(s.id)">
                    <span class="qa-session-title" x-text="s.title"></span>
                    <span class="qa-session-ops">
                        <button type="button" title="导出 Markdown" @click.stop="exportSession(s)">⬇</button>
                        <button type="button" title="重命名" @click.stop="renameSession(s)">✎</button>
                        <button type="button" title="删除" @click.stop="deleteSession(s)">🗑</button>
                    </span>
                </div>
            </template>
            <div x-show="!sessions.length" class="qa-sessions-empty">暂无历史会话</div>
        </div>
    </aside>
</div>
```

- [ ] **Step 4: 实现样式**

在 `static/app.css` 追加：

```css
/* ── QA 页：对话区（主） | 会话管理（右，可折叠） ──
   flex 分列：视觉上就是三栏（20% | 50% | 30%），但不需要改 .app-layout 的 grid。 */

/* QA 页需要确定高度，否则 .qa-root 的 height:100% 解析不到父级高度而塌陷
   （内部滚动区与「输入框贴底」都依赖它）。
   用 :has() 把该规则限定在 QA 页，避免影响检索/规范/规则等其它页面的滚动行为。 */
.center-panel-v2:has(.qa-root) { height: 100%; }

.qa-root { display: flex; gap: 0.75rem; height: 100%; min-height: 0; }
.qa-thread { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.qa-thread-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; font-weight: 600; }
.qa-messages {
    flex: 1 1 auto; min-height: 0; overflow-y: auto;
    padding: 0.5rem; border: 1px solid var(--pico-muted-border-color);
    border-radius: 6px; background: var(--pico-card-background-color);
}
.qa-composer { margin-top: 0.5rem; display: flex; gap: 0.25rem; align-items: flex-start; }
.qa-composer textarea { flex: 1 1 auto; font-size: 0.85rem; margin: 0; }
.qa-composer-btns { display: flex; flex-direction: column; gap: 0.25rem; }
.qa-composer-btns button { font-size: 0.75rem; padding: 0.25rem 0.5rem; white-space: nowrap; margin: 0; }
/* 会话管理栏：固定宽度，折叠时整体隐藏（不挤压输入框所在列） */
.qa-sessions { flex: 0 0 22rem; max-width: 22rem; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--pico-muted-border-color); padding-left: 0.75rem; }
.qa-sessions-search { margin-bottom: 0.4rem; }
.qa-sessions-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; font-size: 0.82rem; }
.qa-session-item { display: flex; justify-content: space-between; align-items: center; gap: 0.3rem; padding: 0.3rem 0.4rem; border-radius: 4px; cursor: pointer; }
.qa-session-item:hover { background: var(--pico-card-sectioning-background-color, #f3f4f5); }
.qa-session-item.active { background: var(--pico-primary-background); color: var(--pico-primary-inverse); }
.qa-session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qa-session-ops button { background: none; border: none; cursor: pointer; padding: 0 0.15rem; font-size: 0.8rem; margin: 0; line-height: 1; }
.qa-sessions-empty { color: var(--pico-muted-color); padding: 0.5rem 0.4rem; font-size: 0.8rem; }
.qa-stage { color: var(--pico-muted-color); font-size: 0.8rem; padding: 0.3rem 0.1rem; }
.qa-msg-filters { margin-top: 0.25rem; font-size: 0.72rem; color: var(--pico-muted-color); }
/* 生成期间：转义纯文本 + 保留换行（不经过任何 Markdown 解析器） */
.qa-answer.streaming { white-space: pre-wrap; word-break: break-word; }
.qa-effective-filters { margin-top: 0.35rem; font-size: 0.72rem; color: var(--pico-muted-color); }
.qa-filters-pending { color: #a06500; }
.qa-confusable { margin-bottom: 0.4rem; padding: 0.4rem 0.6rem; border: 1px solid #e0a800; border-radius: 6px; background: #fff8e1; font-size: 0.78rem; }
.qa-relax-hint { margin-top: 0.4rem; padding: 0.35rem 0.6rem; border: 1px solid #e0a800; border-radius: 6px; background: #fff8e1; font-size: 0.78rem; }
.qa-sources { margin-top: 0.4rem; padding-top: 0.3rem; border-top: 1px dashed var(--pico-muted-border-color); display: flex; flex-wrap: wrap; gap: 0.3rem; }
.qa-source-link { font-size: 0.75rem; color: var(--pico-primary); text-decoration: underline; white-space: nowrap; }
.qa-search-hits { border-bottom: 1px dashed var(--pico-muted-border-color); margin-bottom: 0.3rem; padding-bottom: 0.3rem; }
.qa-hit { padding: 0.25rem 0.4rem; border-radius: 4px; cursor: pointer; }
.qa-hit:hover { background: var(--pico-card-sectioning-background-color, #f3f4f5); }
.qa-hit-title { font-weight: 600; font-size: 0.78rem; }
.qa-hit-body { color: var(--pico-muted-color); font-size: 0.75rem; }
```

- [ ] **Step 5: 提升版本号并重启**

`base.html` 两处：

- `app.css?v=19` → `?v=20`（本 Task 改 `app.css`）
- **`search.js?v=10` → `?v=11`**（**T1 修复轮的欠账**：T1-R1 改了 `static/components/search.js`
  （两处裸调用补 `isQaView` 的 `typeof` 守卫），但当时 `base.html` 不在该轮的允许文件清单内，
  故按项目 CLAUDE.md 三·5 欠下的版本号递增，由本 Task 一并补上。）

按项目 CLAUDE.md 三·1~4 重启实例（全杀残留进程）。

- [ ] **Step 6: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t2`
Expected: 全部 `PASS` —— **条数由脚本自报，不要在计划里写死**（首版手写的「3 行 / 5 行」与 CASES 实际登记数不符，且 T3~T5 的 Run 键名整体错位了一位，已修）。脚本会打印本次运行的用例名与 PASS/FAIL 计数，照它核对。

> 若 `t2` 因 `qaView()` 尚未实现而报 `Alpine Expression Error`，属预期——T3 重写组件。
> 此时先确认 `.qa-thread`、`.qa-messages`、`.qa-composer`、`.qa-sessions`、`#qa-session-toggle`
> 都已渲染出来即可。

- [ ] **Step 7: 提交**

```bash
git add app/templates/partials/qa_page.html static/app.css app/templates/base.html \
        scripts/probe_qa_ui.py
git commit -m "feat: QA 页布局（对话区 + 可折叠会话管理）"
```

---

## Task 3: 筛选统一（QA 读 store）+ CE tooltip

**Files:**
- Modify: `app/templates/partials/tree_panel.html:14-18`（CE tooltip）
- Modify: `static/components/qa.js`
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `$store.searchState`（`tree.js`）
- Produces: `qaView().buildRequestBody(question)` —— 请求体携带 `status_filter`（由 store 组合）与 `include_non_clause`
- 行为契约：QA 面板内**没有**独立的状态过滤复选框；状态/前言筛选一律来自左栏共享 store

> 模板侧无需改动：T2 的全新 `qa_page.html` 从一开始就没有重复的状态复选框
> （旧的 `qa_panel.html` 已在 T1 删除）。本 Task 只补 CE tooltip 与 `qa.js` 的取数逻辑。

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`（登记进 `CASES["t3"]`）：

```python
def t3_qa_page_has_no_duplicate_status_checkboxes(page):
    """**回归守卫（本组允许全绿）**：QA 页内不再有独立的状态过滤复选框（统一到左栏）。

    ⚠️ 这条**不是**本 Task 的 RED 证据，也不可能红：T2 的全新 `qa_page.html` 从一开始
    就没有这个控件（旧的 `qa_panel.html` 已在 T1 删除）⇒ 它在 T3 开工前就是 PASS，
    是一条「空守卫」。它守的是**将来**——别把重复的状态控件再加回来（例如有人为了
    QA 页顺手，又在会话栏上方补一个「仅现行」）。
    本 Task 真正会失败的是下面那条请求体探针。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    in_qa = page.locator("#qa-root").get_by_text("仅现行").count()
    assert in_qa == 0, "QA 面板内不应再有「仅现行」复选框，应统一读左栏"


def t3_left_panel_status_unchecked_sends_empty_status_filter(page):
    """正常场景（**本 Task 的 RED 证据**）：左栏取消「仅现行」后，QA 请求体的
    `status_filter` 必须是**空串**（显式全不勾 = 放行非现行，与检索侧语义一致）。

    为什么必须由这条来当 RED：`qa.js` 里原本有一份**自己的** `buildStatusFilter()`，
    而它的本地字段 `statusCurrent: true` 恒返回 `'现行'` ⇒ 左栏怎么改，问答请求都带 `'现行'`。
    只把注释改成「改为读 store」而不删本地实现，本用例必红。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)

    captured = {}

    def _on_request(req):
        if req.method == "POST" and req.url.endswith("/qa/ask"):
            captured["body"] = req.post_data_json

    page.on("request", _on_request)
    # 左栏「仅现行」默认勾选；取消它 ⇒ 两个状态框都不勾 ⇒ buildStatusFilter() 返回 null
    page.locator(".left-panel label:has-text('仅现行') input[type=checkbox]").uncheck()
    page.wait_for_timeout(300)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)

    body = captured.get("body") or {}
    assert "status_filter" in body, f"请求体未携带 status_filter：{body}"
    assert body["status_filter"] == "", \
        f"左栏已取消「仅现行」，status_filter 应为空串，实得 {body['status_filter']!r}" \
        "——说明请求体仍在用 qa.js 本地那份恒返回 '现行' 的 buildStatusFilter()"


def t3_ce_rerank_has_tooltip(page):
    """正常场景：CE 精排复选框有说明性 tooltip（防误解，不锁功能）。"""
    page.goto(f"{BASE}/")
    label = page.locator("label:has-text('启用 CE 精排')")
    title = label.get_attribute("title") or ""
    assert "问答" in title or "AI" in title, f"CE 复选框缺少问答相关说明: {title!r}"


def t3_ce_rerank_not_disabled_in_qa_page(page):
    """异常场景（防回退）：QA 页内 CE 复选框不得被置灰。

    历史上的方案是「QA 界面里 CE 置灰」，但那会锁死检索侧的 CE 功能
    （QA 常驻后没有「进入 QA 界面」这个时刻了）。改用 tooltip 说明。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    box = page.locator(".left-panel input[type=checkbox]").nth(1)
    assert not box.is_disabled(), "CE 精排复选框在 QA 页被置灰了，应仅用 tooltip 说明"


def t3_pending_filter_hint_appears_after_change(page):
    """正常场景：「· 已修改，将在下一轮生效」提示必须真的出现。

    为什么本 Task 就要有这条：`filtersChanged()` 依赖 `describeFilters()` 与
    `effectiveFiltersText`——两者若留到 T4 才定义，T3/T4 阶段这个提示**恒不出现**
    （`filtersChanged()` 第一行 `if (!this.effectiveFiltersText) return false;` 直接短路），
    静默失效且无任何报警。所以本 Task 就把这两个（纯展示逻辑）定义好，并用这条钉住。

    （T4 的 `t4_pending_filter_change_is_visible` 是同一断言的回归版：T3 这条钉「提前定义」，
     T4 那条钉「重写组件后没弄丢」。两条并存，不是重复。）
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.wait_for_timeout(800)
    assert page.locator(".qa-effective-filters").is_visible(), \
        "输入框上方未显示本轮生效筛选（effectiveFiltersText 未写入）"
    assert page.locator(".qa-filters-pending").is_hidden(), \
        "尚未改动筛选时不应出现「将在下一轮生效」提示"
    page.locator(".left-panel label:has-text('仅现行') input[type=checkbox]").uncheck()
    page.wait_for_timeout(300)
    assert page.locator(".qa-filters-pending").is_visible(), \
        "改了筛选却没提示「将在下一轮生效」——describeFilters/effectiveFiltersText 未能比对（静默失效）"


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t4）。
# 插入位置：T1 的 `CASES = {...}` 字面量之后、`if __name__ == "__main__":` **之前**
# （放在其后则在 `__main__` 块执行时这些名字还不存在 → NameError）。
CASES["t3"] = [t3_qa_page_has_no_duplicate_status_checkboxes,
               t3_ce_rerank_has_tooltip,
               t3_ce_rerank_not_disabled_in_qa_page,
               t3_left_panel_status_unchecked_sends_empty_status_filter,
               t3_pending_filter_hint_appears_after_change]
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t3`
Expected: **部分 FAIL —— 本组不是整体 RED，别拿「守卫用例绿了」当没验收**：
- `t3_qa_page_has_no_duplicate_status_checkboxes` 是**回归守卫，允许（且预期）全绿**——它守的是
  「以后别把重复的状态控件加回来」，不是本 Task 的交付物；
- 真正的 RED 证据是 `t3_left_panel_status_unchecked_sends_empty_status_filter`（左栏取消勾选后
  `status_filter` 仍恒为 `'现行'`）与 `t3_pending_filter_hint_appears_after_change`（提示不出现）。
- 若这两条也绿，说明取数并未真的改读 store（或提示逻辑没落地），**先改用例再继续**（同 T2 Step 2 的纪律）。

- [ ] **Step 3: 实现**

`app/templates/partials/tree_panel.html`，给 CE 复选框所在 label 加 `title`。

**同时**给「包含前言·条文说明」的 label 也加 `title`——说明文本兜底的存在，
避免用户以为取消勾选就彻底不放行（见下方说明）：

```html
        <!-- CE 精排热切换：勾选/取消立即重搜（onCeChange 弹提示 + 触发重搜），结果实时按新开关排序。
             tooltip 说明其作用域，避免用户误以为 AI 问答也受此开关影响 -->
        <label style="display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;line-height:1"
               title="CE 精排仅作用于检索结果排序；AI 问答走独立精排链（CrossEncoder → 向量 → 原序降级），不受此开关影响">
            <input type="checkbox" x-model="ceRerank" @change="onCeChange()"
                   style="width:0.875rem;height:0.875rem;flex:none;margin:0;padding:0">
            <span style="font-size:0.8rem;line-height:0.875rem;white-space:nowrap">启用 CE 精排</span>
        </label>
```

「包含前言·条文说明」的 label 同样加 `title`：

```html
        <!-- 勾选立即重搜：放行前言/条文说明等打标非条文（后端 include_non_clause=1）。
             tooltip 说明文本兜底的存在——用户取消勾选后，若问句里出现
             「前言」「条文说明」字样仍会放行打标非条文（后端兜底条款） -->
        <label style="display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;line-height:1"
               title="勾选后放行前言/条文说明等打标非条文。注意：即使不勾选，若提问中含「前言」或「条文说明」字样，系统仍会包含这类内容">
            <input type="checkbox" x-model="includeNonClause" @change="search()"
                   style="width:0.875rem;height:0.875rem;flex:none;margin:0;padding:0">
            <span style="font-size:0.8rem;line-height:0.875rem;white-space:nowrap">包含前言·条文说明</span>
        </label>
```

> **为什么不改行为**（评审 D12②）：文本兜底是设计阶段明确选定的——问题里出现「前言/条文说明」
> 说明用户正是在问这部分内容，此时不自动放行会让问答直接落空。改为 tooltip 说明，
> 让「显式取消勾选 ≠ 完全排除」这件事可见，而不是让用户以为关掉了。

`static/components/qa.js`，把本地状态与组合逻辑改为读 store。

> ⚠️ **必须删掉旧状态（只「改为读 store」是不够的，漏了这一步功能静默失效）**：
> `qa.js` 里原有的这三样要**删除**：
> 1. 数据字段 `statusCurrent: true`、`statusRevising: false`；
> 2. 方法 `buildStatusFilter()`——那份**恒返回 `'现行'`** 的本地实现（`static/components/qa.js:59-63`）；
> 3. 方法 `onStatusChange() {}`——空壳，左栏（`search.js`）已由共享 store 承担。
>
> 不删的后果：旧的 `send()`（`static/components/qa.js:34`）仍调**本地**那份 `buildStatusFilter()`，
> 左栏的状态改动**对 QA 完全不生效**（在 T5 之前一直取恒值 `'现行'`），
> 而上面那条「QA 页内没有重复复选框」的守卫探针**并不覆盖**这一点（它只看 DOM）。
> 删除后 `send()` 的取数必须改走 `buildRequestBody()` —— 这正是
> `t3_left_panel_status_unchecked_sends_empty_status_filter` 钉住的东西。

```js
        // 本轮实际生效的筛选（D5）。**本 Task 就要有**：filtersChanged() 第一行读它，
        // 留到 T4 才定义会让「将在下一轮生效」提示在整个 T3/T4 阶段恒不出现（静默失效）。
        effectiveFiltersText: '',

        // 状态过滤改为读左栏共享 store（统一操作逻辑）：
        // QA 面板内不再有独立复选框，「仅现行 / 修订中」一律由左栏控制
        get statusFilter() {
            return this.$store.searchState.buildStatusFilter();
        },

        // 把筛选 dict 渲染成一行可读文本；空对象返回空串（调用处据此隐藏）。
        // 同时服务两处：历史消息的「筛选：…」与输入框上方的「本轮生效：…」。
        //
        // ⚠️ 本方法**定义在 T3**（纯展示逻辑，无任何依赖，提前定义不影响任何东西）；
        //    T4 重写组件时**必须原样保留**，删掉它会连带让 filtersChanged() 与
        //    历史消息的「筛选：…」一起失效（本 Task 的 t3_pending_filter_hint_appears_after_change
        //    与 T4 的 t4_filters_recorded_and_shown 都会红）。
        describeFilters(f) {
            const LABELS = {
                dim1_hierarchy: '层级', dim1_industry: '行业', dim1_nature: '性质',
                dim2_stage: '阶段', dim3_usage: '用途', dim4_specialty: '专业',
                dim5_location: '地区', dim6_material: '材料',
                status_filter: '状态', include_non_clause: '含前言说明',
            };
            if (!f || !Object.keys(f).length) return '';
            return Object.entries(f).map(([k, v]) => {
                const name = LABELS[k] || k;
                if (v === true) return name;                       // 布尔开关只显示名字
                if (v === false || v === '' || v == null) return '';  // 未启用/未选不显示
                const val = Array.isArray(v) ? v.join('/') : String(v);
                return val ? `${name}=${val}` : '';
            }).filter(Boolean).join(' · ');
        },

        // 收集本轮筛选（**唯一来源**）：分类维度 + 状态 + 前言放行。
        // buildRequestBody 与 filtersChanged 共用同一份，避免两处各拼一套而漂移。
        collectFilters() {
            const ss = this.$store.searchState;
            const out = { ...ss.filters };
            // buildStatusFilter() 全不勾返回 null → 传空串（不过滤非现行），与既有语义一致
            const sf = this.statusFilter;
            out.status_filter = (sf === null || sf === undefined) ? '' : sf;
            if (ss.includeNonClause) out.include_non_clause = true;
            return out;
        },

        buildRequestBody(question) {
            const body = { question, mode: this.mode, ...this.collectFilters() };
            if (this.currentSessionId) body.session_id = this.currentSessionId;
            return body;
        },

        // 筛选已改但尚未生效（设计文档场景 2 的可见性）：
        // 比较「当前选中」与「本轮实际生效」，不一致就提示——否则用户切了以为生效了。
        filtersChanged() {
            if (!this.effectiveFiltersText) return false;
            return this.describeFilters(this.collectFilters()) !== this.effectiveFiltersText;
        },
```

**本 Task 对既有 `send()` 的最小改动**（T4 会用完整版重写它，这里只改取数与提示写入两处）：

```js
        async send() {
            // …前面不变…
            const resp = await fetch('/qa/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // ① 取数改走 buildRequestBody()（读 store；不再调已删除的本地 buildStatusFilter()）
                body: JSON.stringify(this.buildRequestBody(q)),
            });
            const data = await resp.json();
            // ② 写入本轮生效筛选：否则 .qa-filters-pending 恒不出现
            //    （后端 /qa/ask 的 JSON 响应已带 effective_filters，键与 _effective_filters 一致）
            this.effectiveFiltersText = this.describeFilters(data.effective_filters);
            // …后面不变（渲染与会话刷新由 T4 重写）…
        },
```

- [ ] **Step 4: 提升版本号并重启**

> ⚠️ **更正（2026-09-23）**：本 Task **确实要改 `base.html`**——它改了 `static/components/qa.js`
> ⇒ 按 Global Constraints「改静态资源的 Task 必须把 `base.html` 列入允许清单」，
> 请 `qa.js?v=20 → ?v=21`（当前值；T1 bump 到 19、控制器热修 bump 到 20）。下面这段「不改 base.html」的旧说法保留供对照。

**本 Task 不改 `base.html`**——首版计划写的「把 `tree.js` 的版本号从 v=8 bump 到 v=9」已删：实际当前是 `?v=6`，
**T1 已把它 bump 到 `?v=7`**，而且**本 Task 根本不改 `tree.js`**（改的是 `qa.js` 与 `tree_panel.html`）。
`qa.js` 的版本号在 **T4 统一处理**（记 `?v=19`）；本 Task 阶段若要单跑 UI 验证，按 Global Constraints
用 Ctrl+F5 强刷即可，不必为此单独 bump 一次。

- [ ] **Step 5: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t3`
Expected: 全部 `PASS` —— **条数由脚本自报，不要在计划里写死**（首版手写的「3 行 / 5 行」与 CASES 实际登记数不符，且 T3~T5 的 Run 键名整体错位了一位，已修）。脚本会打印本次运行的用例名与 PASS/FAIL 计数，照它核对。

- [ ] **Step 6: 提交**

```bash
git add app/templates/partials/tree_panel.html static/components/qa.js \
        scripts/probe_qa_ui.py
git commit -m "feat: QA 筛选统一读共享 store + CE 精排加 tooltip 说明"
```

> `base.html` **不在**本次 `git add` 里：本 Task 不改它（见 Step 4）。

---

## Task 4: 会话列表、切换载入与续聊

> 🔌 **本 Task 的探针需要「AI 有回答」**（会话列表要出现、续聊要追加消息）⇒ **必须按计划开头的「前置 C」配置 mock LLM**
> （`%TEMP%/qa_mock_llm.py` 监听 8123 之外的 8199；副本库写 `ai.backend='custom'` + `ai.custom.base_url/api_key/model`）。
> 用真实模型既耗配额、内容又不可控。**注意**：`task-brief` 只抽取 Task 段落，开头的前置节不会出现在简报里，
> 故本指针必须留在这里。

**Files:**
- Modify: `static/components/qa.js`
- Modify: `app/templates/base.html`（本 Task 改 `qa.js` ⇒ `qa.js?v=21 → ?v=22`；Global Constraints 要求此时必须把 base.html 列入清单）
- Modify: `app/templates/partials/tree_panel.html`（**仅加一个稳定 id**：给「启用 CE 精排」复选框加 `id="ce-rerank-toggle"`，
  并把探针 `t3_ce_rerank_not_disabled_in_qa_page` 里的 `.left-panel input[type=checkbox]` + `nth(1)` 换成该 id
  ——`nth(1)` 依赖复选框的**排列顺序**，将来在 CE 之前插入任何复选框都会让它**静默失效**，由 T3 实现者上报）
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `GET /qa/sessions`、`GET /qa/sessions/{id}`、`POST /qa/ask`（后端计划 T8~T10）
- Produces（`qaView` 组件状态）：
  - `messages: Array<{uid, role, content, html, sources, confusable, filteredOut}>`
  - `sessions: Array<{id, title, updated_at, msg_count}>`
  - `currentSessionId: number | null`（`null` = 草稿态）
  - `sessionsCollapsed: boolean`
- 行为契约：`currentSessionId === null` 时首次发送创建新会话；点历史会话后 `currentSessionId` 置为该 id，后续发送**追加到该会话**

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`（登记进 `CASES["t4"]`）：

```python
def t4_ask_creates_session_and_lists_it(page):
    """正常场景：提问后会话出现在列表，且标题为首轮问题截断。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    q = "混凝土强度等级如何评定"
    page.fill(".qa-composer textarea", q)
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.wait_for_timeout(800)
    titles = page.locator(".qa-session-title").all_inner_texts()
    assert any(q[:20] in t for t in titles), f"会话列表未见新会话: {titles}"


def t4_history_shows_full_conversation(page):
    """正常场景：点历史会话 → 中栏载入该会话全部消息。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "第一个问题")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.click("text=＋ 新会话")          # 清空到草稿态
    page.wait_for_timeout(300)
    assert page.locator(".qa-msg").count() == 0, "新会话应清空对话区"
    page.click(".qa-session-item >> nth=0")   # 点回历史会话
    page.wait_for_timeout(800)
    assert page.locator(".qa-msg").count() >= 2, "载入历史后应出现问答两条"


def t4_mode_switch_reaches_request_body(page):
    """正常场景（§4.8「必须保留的既有功能」）：模式切换必须真的进入请求体。

    为什么必须有这条：模式切换控件在本计划首版的新模板里**被漏掉了**，而当时**没有任何探针会报警**
    （完成标准却写着"模式切换正常"）⇒ 功能静默消失。本探针把「控件存在 + 切换生效 + 值到达请求体」
    三件事一次性钉住：删控件 → `page.check` 找不到元素即红；`toggleMode` 不写 mode / `buildRequestBody`
    不带 mode → 断言红。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)

    captured = {}

    def _on_request(req):
        if req.method == "POST" and req.url.endswith("/qa/ask"):
            captured["body"] = req.post_data_json

    page.on("request", _on_request)
    page.check(".qa-mode-switch input[type=checkbox]")     # 切到「原文摘抄」
    page.wait_for_timeout(200)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)

    assert captured.get("body", {}).get("mode") == "verbatim", \
        f"模式切换未进入请求体（mode 应为 verbatim）: {captured.get('body')}"


def t4_continue_in_history_session_appends(page):
    """异常场景（核心）：在历史会话里继续提问，追加到同一会话而非新建。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "第一个问题")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.wait_for_timeout(800)
    n_before = len(page.locator(".qa-session-item").all())
    page.fill(".qa-composer textarea", "继续追问")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_timeout(2000)
    n_after = len(page.locator(".qa-session-item").all())
    assert n_after == n_before, "在历史会话中追问不应新建会话"
    assert page.locator(".qa-msg").count() >= 4, "追问后应有四条消息"


def t4_filters_recorded_and_shown(page):
    """正常场景（D5）：答案下方显示当轮筛选，输入框上方显示本轮生效筛选，
    且**载入历史会话后答案下方的筛选仍在**（这才真正走落库→回填那条路径）。

    ⚠️ 只断言「当前这条答案下有 `.qa-msg-filters`」是不够的：那来自本轮内存里的消息对象，
    即便 `filters_json` **完全没落库**、`GET /qa/sessions/{id}` 的 `filters` 恒为空，
    它也照样通过 ⇒ 恰漏掉本用例声称覆盖的 D5 落库路径。故末尾补一段
    「新会话 → 点回历史会话 → 再断言」，把回填路径钉死。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    click_first_tree_label(page)                  # 勾一个分类树筛选（全新页面：先点、再等，顺序见「前置 B」）
    page.wait_for_timeout(300)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.wait_for_timeout(800)
    assert page.locator(".qa-msg-filters").count() >= 1, \
        "答案下方未显示当轮筛选（D5 落库/展示未生效）"
    assert page.locator(".qa-effective-filters").count() == 1, \
        "输入框上方未显示本轮生效筛选"

    # ── 落库回填路径（D5 的真正判据）──
    # 清空到草稿态再点回该历史会话：消息改为从 GET /qa/sessions/{id} 重新映射，
    # 此处的 `.qa-msg-filters` 只能来自 `qa_messages.filters_json`
    page.click("text=＋ 新会话")
    page.wait_for_timeout(300)
    assert page.locator(".qa-msg").count() == 0, "新会话应清空对话区"
    page.click(".qa-session-item >> nth=0")
    page.wait_for_timeout(800)
    assert page.locator(".qa-msg-filters").count() >= 1, \
        "载入历史会话后答案下方的当轮筛选消失——filters 未随消息落库或未回填（D5 路径坏）"


def t4_pending_filter_change_is_visible(page):
    """异常场景（设计文档场景 2 的可见性）：改动筛选后提示将在下一轮生效。

    没有这条提示时，用户在回复中/回复后切换筛选会以为立即生效——静默失配。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-bot", timeout=60000)
    page.wait_for_timeout(800)
    assert page.locator(".qa-filters-pending").is_hidden(), \
        "尚未改动筛选时不应出现「将在下一轮生效」提示"
    click_first_tree_label(page)                  # 改动筛选（顺序同上）
    page.wait_for_timeout(300)
    assert page.locator(".qa-filters-pending").is_visible(), \
        "改了筛选但未提示「将在下一轮生效」——静默失配复现"


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t5）。
# 插入位置：T1 的 `CASES = {...}` 字面量之后、`if __name__ == "__main__":` **之前**。
CASES["t4"] = [t4_ask_creates_session_and_lists_it,
               t4_history_shows_full_conversation,
               t4_mode_switch_reaches_request_body,
               t4_continue_in_history_session_appends,
               t4_filters_recorded_and_shown,
               t4_pending_filter_change_is_visible]
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t4`
Expected: FAIL — `.qa-session-title` 未出现（会话列表未加载）

- [ ] **Step 3: 实现**

重写 `static/components/qa.js`。**必须保留**：模式切换（`toggleMode`）、`renderMarkdown`、`openClause`、`scrollToBottom`。以下是新增/改写部分：

> ⚠️ **必须保留（T3 的交付，删掉会连带红掉已有探针）**：
> - `describeFilters(f)` 与 `effectiveFiltersText` —— T3 已把它们提前落地（见 `qa.js` 里该方法的注释与 T3 的
>   `t3_pending_filter_hint_appears_after_change`）。删掉/改写会让 **T3 那条与 T4 自己的
>   `t4_filters_recorded_and_shown` 一起红**。
> - `sessionsCollapsed`（T1 加、T2 的折叠探针依赖）。
> - **`send()` 的消息构造**：`role: 'assistant'` + `html`（正常回答走 `renderMarkdown`）+ catch 里的 `console.error`
>   —— 这是控制器为修「新模板下回答渲染成空气泡」做的 P0 热修（`52a3aa2`），本 Task 重写 `send()` 时要**保留这些语义**。

> ⚠️ **`send()` 的归属必须在本 Task 明确处理（本计划首版漏了，会直接炸）**：
> 模板用的是 `@keydown.enter.prevent="send()"` 与 `@click="send()"`（plan 模板 `qa-composer`），
> 而**本 Task 的"新增/改写"清单与"必须保留"清单里都没有 `send()`**；`send()` 的 SSE 版由 **T5** 拥有
> （T5 的 Files 写着「`send()` 改走 SSE」）。
> 若 T4 只照清单改，会落到两种坏结局之一：**删掉 → 发送键与回车彻底失效**；**留着旧版 → 回答永远空白**
> （旧 `send()` 不写 `msg.html`，而新模板用 `x-html="msg.html"` 渲染助手消息，见 plan 模板 `qa-answer`）。
> **定案（T4 必须做）**：本 Task 提供一版**非流式** `send()`——沿用 T5 将改写的签名 `async send(opts = {})`
> （`opts.relaxed` 供「放宽分类筛选」复用），用 `buildRequestBody()` 发 `POST /qa/ask`（不带 `stream`），
> 拿到 JSON 后写全 `bot` 消息的 `content`/`html`（`this.renderMarkdown(content, sources)`）/`sources`/`confusable`/
> `filteredOut`/`filtersText`，并 `await this.loadSessions()` 刷新列表与 `this.currentSessionId = data.session_id`。
> **T5 只把它的取数方式换成 SSE**，不动签名与收尾逻辑（T5 的 Step 4 会给出完整实现）。
> 这样 T4 的全部探针（发送→回答可见→会话出现在列表→续聊）才有意义。

```js
// AI 问答组件（QA 页）
document.addEventListener('alpine:init', () => {
    // 唯一 id：流式期间 x-for 的 key 需要稳定，避免每次 delta 重建 DOM 丢滚动位置
    let _uid = 0;

    Alpine.data('qaView', () => ({
        messages: [],
        sessions: [],
        searchHits: [],
        searchKeyword: '',
        input: '',
        loading: false,
        mode: 'rag',                 // rag 综合问答（默认） / verbatim 原文摘抄
        currentSessionId: null,      // null = 草稿态（首次发送才落库）
        sessionsCollapsed: false,
        rerankUsed: '',
        stageText: '正在检索…',
        // 本轮实际生效的筛选，显示在输入框上方（D5）。
        // T3 已定义（连同 describeFilters()/filtersChanged()）；重写时**原样保留**，别因为
        // "T4 才是会话/筛选的 Task" 就把它们搬回来或删掉。
        effectiveFiltersText: '',

        async init() {
            // ⚠️ 首行**必须**是 seedFiltersFromUrl()，顺序也不能换（先回填 URL → 再拉会话列表）。
            //    T1 已实现该方法并在 init() 里留了「← T4 引入 loadSessions() 后在此启用」的注释
            //    （见 T1 Step 5 的 qa.js 片段）。重写组件时把它丢掉 ⇒ T1 的
            //    `t1_filters_carry_into_qa_via_url`、`t1_qa_page_filters_survive_reload`
            //    立刻回归失败（筛选经 URL 带入 / 刷新后仍在，全靠这一行）。
            this.seedFiltersFromUrl();
            await this.loadSessions();
            this.scrollToBottom();
        },

        // ── 会话列表 ──

        async loadSessions() {
            try {
                const r = await fetch('/qa/sessions');
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                this.sessions = (await r.json()).sessions || [];
            } catch (e) {
                console.error('[qa] 加载会话列表失败:', e);
            }
        },

        newSession() {
            this.currentSessionId = null;   // 回到草稿态，首次发送才创建
            this.messages = [];
            this.input = '';
            this.rerankUsed = '';
        },

        async openSession(sid) {
            if (this.loading) return;
            try {
                const r = await fetch(`/qa/sessions/${sid}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                // 字段结构与 qa_messages 一一对应，直接映射；
                // 历史消息必须走完整渲染（含 KaTeX），因为流式降级渲染不跑公式
                //
                // ⚠️ role 值域是后端的 {user, assistant}（qa_messages.role），**不要**在本地另造
                // 'bot' 之类的别名：本计划首版把助手消息写成 role:'bot'，而这里直接映射后端值
                // 'assistant' ⇒ 模板的 x-if="msg.role === 'bot'" 恒为假 ⇒ **点开历史会话时
                // 只显示提问、不显示任何回答**。统一用 'assistant'（CSS 类名 qa-bot 保留不动）。
                this.messages = (data.messages || []).map(m => ({
                    uid: ++_uid,
                    role: m.role,
                    content: m.content,
                    html: m.role === 'assistant'
                        ? this.renderMarkdown(m.content, m.sources) : '',
                    sources: m.sources || [],
                    confusable: m.confusable || [],
                    // 当轮筛选：**后端已落库**（D5 依赖已就绪，不再是"待补"）——
                    // `app/routes/qa_routes.py` 的 `_finish_turn(...)` 现在会带
                    // `filters=_effective_filters(body)` 写进 `qa_messages.filters_json`
                    // （JSON 与 SSE `done` 两条路径都走它），`GET /qa/sessions/{id}` 返回的
                    // 每条消息里就有 `filters`，字段名与 `_effective_filters` 的键一致：
                    // 8 个分类维度名 + `status_filter` + `include_non_clause`。
                    // 这里据此还原「这条答案是在什么筛选下产生的」。
                    filtersText: this.describeFilters(m.filters),
                    filteredOut: 0,
                    streaming: false,
                }));
                this.currentSessionId = sid;
                this.scrollToBottom();
            } catch (e) {
                console.error('[qa] 载入会话失败:', e);
            }
        },

        // 导出 Markdown：后端返回带 Content-Disposition 的附件响应，
        // 交给浏览器直接下载，无需前端拼内容
        exportSession(s) {
            window.location.href = `/qa/sessions/${s.id}/export`;
        },

        async renameSession(s) {
            const next = window.prompt('新的会话名称：', s.title);
            if (next === null) return;
            const title = next.trim();
            if (!title) return;
            try {
                const r = await fetch(`/qa/sessions/${s.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title }),
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                await this.loadSessions();
            } catch (e) {
                console.error('[qa] 重命名失败:', e);
            }
        },

        async deleteSession(s) {
            // 删除不可撤销，必须二次确认（开发铁律 1.5）
            if (!window.confirm(`确认删除会话「${s.title}」？该操作不可撤销。`)) return;
            try {
                const r = await fetch(`/qa/sessions/${s.id}`, { method: 'DELETE' });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                if (this.currentSessionId === s.id) this.newSession();
                await this.loadSessions();
            } catch (e) {
                console.error('[qa] 删除失败:', e);
            }
        },

        // ── 跨会话搜索 ──

        async runSearch() {
            const q = this.searchKeyword.trim();
            if (!q) { this.searchHits = []; return; }
            try {
                const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                this.searchHits = (await r.json()).hits || [];
            } catch (e) {
                console.error('[qa] 搜索失败:', e);
            }
        },

        async jumpToHit(hit) {
            await this.openSession(hit.session_id);
            this.searchHits = [];
            this.searchKeyword = '';
            // 定位到命中消息：等 DOM 渲染完成后滚动 + 短暂高亮
            this.$nextTick(() => {
                const idx = this.messages.findIndex(m => m.content === hit.content);
                if (idx < 0) return;
                const nodes = this.$refs.msgBox.querySelectorAll('.qa-msg');
                const el = nodes[idx];
                if (el) {
                    el.scrollIntoView({ block: 'center' });
                    el.classList.add('qa-highlight');
                    setTimeout(() => el.classList.remove('qa-highlight'), 2000);
                }
            });
        },

        // 生成期间的渲染：只做 HTML 转义，不解析 Markdown。
        // 换行由 CSS 的 white-space: pre-wrap 呈现（见 app.css 的 .qa-answer.streaming）。
        _streamingHtml(text) {
            return escHtml(text || '');
        },

        // ── 筛选可读化 ──

        // describeFilters(f) / collectFilters() / buildRequestBody(question) / filtersChanged()
        // 四个方法**已在 T3 定义**（那里是唯一定义点，含完整实现与 LABELS 常量表），
        // 本 Task 重写组件时**原样保留、不要在此处再复制一份**（两份必然漂移）。
        // `effectiveFiltersText` 字段同理（见上方数据块）。

        // ── 降级状态提示 ──

        // ⚠️ 必须显式判 'crossencoder' / 'vector' / 'none' 三态。
        // 后端 QAResponse.rerank_used 的缺省是空串——那是**契约外的第四态**
        // （后端未上报），不是 RERANK_NONE。用 `else → 无精排` 的写法会在
        // 字段只是没上报时谎报降级（Task 2 实现者发现的跨计划问题）。
        rerankBadge() {
            if (this.rerankUsed === 'crossencoder') return '⚡ CE 精排';
            if (this.rerankUsed === 'vector') return '≈ 向量精排';
            if (this.rerankUsed === 'none') return '⚠️ 无精排';
            return '';   // 未上报 → 不显示标记（模板的 x-show="rerankUsed" 会隐藏它）
        },

        rerankTip() {
            if (this.rerankUsed === 'crossencoder') return 'CrossEncoder 精排生效';
            if (this.rerankUsed === 'vector') return 'CE 模型不可用，已降级为向量精排';
            if (this.rerankUsed === 'none') {
                return '未检测到精排模型，当前按关键词排序；'
                     + '请在维护页「健康检查」查看缺失的模型';
            }
            return '';   // 未上报 → 无提示
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const box = this.$refs.msgBox;
                if (box) box.scrollTop = box.scrollHeight;
            });
        },

        // 以下 renderMarkdown / openClause / toggleMode 保持既有实现不变
    }));
});
```

在 `static/app.css` 追加高亮样式：

```css
.qa-msg.qa-highlight { outline: 2px solid var(--pico-primary); transition: outline 0.2s; }
```

- [ ] **Step 4: 提升版本号并重启**

`base.html`：确保 `qa.js?v=19`（`base.html` 的 `<script>` 标签保留）。

- [ ] **Step 5: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t4`
Expected: 全部 `PASS` —— **条数由脚本自报，不要在计划里写死**（首版手写的「3 行 / 5 行」与 CASES 实际登记数不符，且 T3~T5 的 Run 键名整体错位了一位，已修）。脚本会打印本次运行的用例名与 PASS/FAIL 计数，照它核对。

> 若 LLM 未配置导致回答失败，`t4` 中 `.qa-bot` 不会出现——先确认 `ai.backend.qa` 已配置可用后端（隔离库是副本，配置随副本带过来）。

- [ ] **Step 6: 提交**

```bash
git add static/components/qa.js static/app.css app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 会话列表、切换载入与续聊交互"
```

---

## Task 5: 流式渲染（SSE + 降级渲染）

> 📌 **施工后的补充（2026-09-23，控制器按 T5 实现者上报的三处修正回填）**：
> 1. **本 Task 的用例集必须包含一条错误路径用例**：`t5_error_frame_is_not_retried_as_fallback`。
>    控制器在派发时曾称两条边界「都有对应用例」——**该说法不成立**（Step 1 登记的 4 条一条都不碰错误路径），
>    故 mutation「让 `error` 帧也 throw」在原样登记下**无从判红**。
>    确定性做法：用 `PUT /settings` 把 `ai.custom.api_key` 置空造出稳定错误，断言**只发 1 次 `/qa/ask` 且带 `stream:true`**，
>    用例内 `finally` 还原设置。
> 2. **`jumpToHit` / `scrollToBottom` 有一个共同的真 bug（不只是缺 CSS）**：Alpine 3.15 的 magic
>    （`$refs`/`$root`）解析上下文是**调用该方法的元素**（`.qa-hit` / 消息容器），故 `this.$refs.msgBox` 恒为 undefined
>    ⇒ `jumpToHit` 抛 `TypeError`、**高亮类从未加上**；`scrollToBottom` 则被 `if (box)` 守卫**静默吞掉**（从未真正贴底，
>    流式期间每帧都要贴底）。修法：新增 `_messageBox()` 按类名取（QA 页唯一），两处都改用它。
> 3. **高亮判据不能只看「底色非透明」**：`.qa-bot` 自带不透明底色（实测 `rgb(251,252,252)`）⇒ 删掉 `.qa-highlight`
>    规则该判据照样为真（假绿）。正确判据 = **同一元素「有类 / 无类」的底色必须不同** + 用 `MutationObserver` 取样
>    （`jumpToHit` 只保留 2 秒高亮，轮询会采样落空）。
> 4. **流式后 `_qa_ask` 必须多等一条件**：助手气泡先乐观占位 ⇒ 旧判据会在**流中途**返回，让依赖 `done` 帧字段的
>    T4 用例成片假红。加「最后一条 `.qa-bot .qa-answer` 不得带 `.streaming`」。

> ⚠️ **本 Task 同时补一件 T4 做不到的事：跨会话搜索命中的「高亮」样式**。
> `qa.js` 会给命中的消息加 `.qa-highlight` 类（`jumpToHit` 的滚动+高亮），但 **`app.css` 里 0 处匹配**
> ⇒ 目前**高亮是视觉 no-op**（T4 的 Files 不含 `app.css`，且那轮硬约束明确禁改它）。
> ⇒ 本 Task 在 `app.css` 补 `.qa-highlight` 规则（`app.css` 已加入本 Task 的 Files，见下方），
> 并把 `app.css?v=21 → ?v=22` 一并递增。（另注：本 Task 也改 `qa.js` ⇒ `qa.js?v=22 → ?v=23`。）

> 🧩 **本 Task 同时补两处此前遗漏的探针**（T4 复核指出：两处都是「实现已完成、但无用例守」，且成本都很低）：
>
> 1. **`relax()`（放宽分类筛选）**：T4 实现者认为"mock 下无法稳定复现 `filtered_out>0`"而跳过，
>    **该理由偏弱**——阈值是**可配参数** `retrieve.qa_min_candidates`（`app/qa/registry` 注册，默认 3、范围 1~30），
>    而探针库本就是副本 ⇒ **把它调到 30，再勾任一分类维度**，即可稳定造出「候选不足 → 前端显示放宽提示」；
>    然后点「放宽分类筛选」→ 断言**请求体 `relaxed === true`**（用 T1-R1 的请求计数手法）且会话数不增。
> 2. **`/qa/search` + 点命中跳转**：断言「搜一句 → `.qa-hit` ≥1 → 点击 → 该会话被载入且命中内容可见」。
>    高亮本身是 **class 开关**（`qa.js` 的 `jumpToHit` 加 `.qa-highlight`），可用
>    `page.evaluate(() => el.classList.contains('qa-highlight'))` 断言，**不依赖 CSS**（故它不受本 Task 补样式的影响）。

> 🔌 **本 Task 的探针需要「AI 有回答」且要观测流式的中途态** ⇒ **必须配置 mock LLM**
> （`%TEMP%/qa_mock_llm.py`，监听 `127.0.0.1:8199`；其流式响应刻意在帧间 sleep，留出可观测的「生成中」窗口）。
> 副本库需写（**本段自带配置，因为简报不含计划开头的前置节**）：
>
> ```sql
> INSERT OR REPLACE INTO settings (key, value) VALUES
>   ('ai.backend','custom'), ('ai.custom.base_url','http://127.0.0.1:8199/v1'),
>   ('ai.custom.api_key','mock'), ('ai.custom.model','mock-model');
> ```

**Files:**
- Modify: `static/components/qa.js`（`send()` 改走 SSE）
- Modify: `static/app.css`（补 `.qa-highlight` 规则 + `.qa-answer.streaming` 的 `white-space: pre-wrap`；
  见上方 ⚠️ 说明）
- Modify: `app/templates/base.html`（`app.css?v=21 → ?v=22`；`qa.js?v=**23** → ?v=24`
  —— 开工前请以 `grep 'qa.js?v=' app/templates/base.html` 的**实际值**为准：本计划写过的版本号已被 T1~T4 的
  多次递增与两次控制器代补甩开，**不要照抄数字**）
- **不改** `static/components/md-render.js`（首版此处列过一条「给它加一个流式档」的 Modify，
  与本节正文及「文件结构」表的「不改」自相矛盾，已删——流式期间不经过任何 Markdown 解析器，
  收尾才走既有完整管线，`md-render.js` 无需任何新档）
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `POST /qa/ask` 带 `stream=true`（后端计划 T15），事件 `stage` / `delta` / `done` / `error`
- Produces: `qaView()._streamingHtml(text) -> string`——把增量文本转义为可安全插入的 HTML（保留换行）
- 行为契约：生成期间 `msg.html` 是**转义纯文本**（`white-space: pre-wrap`）；收到 `done` 后改用 `renderMarkdown`（完整管线含 KaTeX + 源链接改写）
- **本 Task 不改 `md-render.js`**——流式期间不经过任何 Markdown 解析器

> **为何必须降级**：一次回答有几十上百个 delta，每个都跑 KaTeX auto-render 会卡死；且公式未闭合时（`$$E = mc^`）KaTeX 会渲染失败甚至抛错。详见设计文档 §4.11。

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`（登记进 `CASES["t5"]`）：

```python
def t5_streaming_renders_progressively(page):
    """正常场景（渐进渲染）：**中途态必须真的存在**——带 `.streaming` 类且文本短于最终文本。

    ⚠️ 首版只等「`.qa-answer` 文本非空」——那把流式换成一次性非流式实现（拿到整段再一次性写入）
    也照样通过，与用例名不符（假绿）。这里钉住两件事：
      ① 生成期间采样到的文本**严格短于**最终文本（真的在长）；
      ② 收尾后 `.streaming` 类消失（降级样式只在生成期间存在）。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")

    # ① 中途态：等待期间采样。wait_for_function 默认按 rAF 轮询，能抓到最早的那一帧。
    page.wait_for_function(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a && a.classList.contains('streaming') && a.innerText.trim().length > 0; }",
        timeout=60000)
    mid_len = page.evaluate(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a ? a.innerText.trim().length : 0; }")
    assert mid_len > 0, "流式首帧应有文本"

    # ② 收尾态：.streaming 类消失且文本变长（等收尾用 .streaming 判定，不依赖按钮禁用态）
    page.wait_for_function(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a && !a.classList.contains('streaming') && a.innerText.trim().length > 0; }",
        timeout=60000)
    final_len = page.evaluate(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a ? a.innerText.trim().length : 0; }")
    assert final_len > mid_len, \
        f"最终文本（{final_len} 字）未长于中途采样（{mid_len} 字）——不是渐进渲染，而是一次性写入"


def t5_streaming_shows_plain_text_not_markdown(page):
    """异常场景（关键约束）：生成期间显示转义纯文本，不解析 Markdown。

    半截 Markdown（未闭合的 ** / 表格 / 公式）会让解析器反复重排，
    比「纯文本 → 一次性排版好」更晃眼。判据：生成期间 .qa-answer 带
    .streaming 类，且内部没有 Markdown 解析产物。

    ⚠️ 首版这里用「发送按钮是否禁用」推断"还在流式"，然后写成条件分支——条件不成立就
    **整段跳过**，用例体压根不执行 ⇒ 恒绿，等于没有守卫。现在改为**主动等**：
    `wait_for_function` 断言 `.streaming` 出现（等不到就红），不做任何条件跳过。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_function(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a && a.classList.contains('streaming') && a.innerText.trim().length > 0; }",
        timeout=60000)
    ans = page.locator(".qa-bot .qa-answer").first
    assert "streaming" in (ans.get_attribute("class") or ""), \
        "生成期间 .qa-answer 缺 .streaming 类（pre-wrap 样式未生效）"
    assert ans.locator("p, strong, table, h1, h2, ul").count() == 0, \
        "生成期间出现了 Markdown 解析产物，应只显示转义纯文本"
    assert ans.locator(".katex").count() == 0, \
        "生成期间出现了 KaTeX 渲染产物"


def t5_rerank_badge_handles_unreported_state(page):
    """异常场景（防谎报）：后端未上报精排级别时不得显示降级标记。

    `QAResponse.rerank_used` 的缺省是空串——那是**契约外的第四态**（未上报），
    不是 `RERANK_NONE`。若把 `rerankBadge()` 写成 `else → 无精排`，会在字段
    缺失时谎报「系统已降级」。本用例直接钉住三态 + 未上报态的返回值契约。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    got = page.evaluate("""() => {
        const d = window.Alpine.$data(document.getElementById('qa-root'));
        const probe = (v) => { d.rerankUsed = v; return d.rerankBadge(); };
        return {unreported: probe(''), none: probe('none'),
                vector: probe('vector'), ce: probe('crossencoder')};
    }""")
    assert got["unreported"] == "", \
        f"未上报（空串）时应无任何标记，实得 {got['unreported']!r} —— 谎报降级"
    assert got["none"].strip(), "none 态必须有标记"
    assert got["vector"].strip(), "vector 态必须有标记"
    assert got["ce"].strip(), "crossencoder 态必须有标记"


def t5_stage_indicator_reflects_real_stage_events(page):
    """正常场景：阶段提示随 `stage` 事件**变化**，不只是默认值。

    ⚠️ 首版只断言 `.qa-stage` 文本非空——但 `stageText` 的默认值就是「正在检索…」，
    于是**整块 stage 事件处理可以缺失**（不解析 `stage` 帧、不写 `stageText`）而无人报警（假绿）。
    这里断言出现**只有真的收到 stage 事件才会出现**的文案：后端依次发
    `retrieving → generating`（**只有两态**；`reranking` 已在施工中删除，理由见设计文档 §4.11 的实现修订），
    对应「生成中…」——该文案在默认值「正在检索…」之外，故必须由真实 stage 事件驱动才会出现。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-stage:visible", timeout=15000)
    page.wait_for_function(
        "() => { const s = document.querySelector('.qa-stage');"
        " return s && /精排|生成中/.test(s.innerText); }",
        timeout=15000)
    got = page.locator(".qa-stage").first.inner_text()
    assert "精排" in got or "生成中" in got, \
        f"阶段提示未随 stage 事件变化（仍是默认值/空）：{got!r}"


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t6）。
# 插入位置：T1 的 `CASES = {...}` 字面量之后、`if __name__ == "__main__":` **之前**。
CASES["t5"] = [t5_streaming_renders_progressively,
               t5_streaming_shows_plain_text_not_markdown,
               t5_rerank_badge_handles_unreported_state,
               t5_stage_indicator_reflects_real_stage_events]
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t5`
Expected: FAIL —— `.qa-answer` 从不带 `.streaming` 类（一次性 JSON 响应），
`.qa-stage` 的文案也不随 stage 变化（尚未带 `stream` 标志）。

- [ ] **Step 3: 在 qa.js 实现 `send()`（SSE）**

```js
        async send(opts = {}) {
            const q = this.input.trim();
            if (!q || this.loading) return;

            const body = this.buildRequestBody(q);
            // 「放宽到全部规范」重发：忽略分类维度（后端 relaxed=True）
            if (opts.relaxed) body.relaxed = true;
            // 乐观插入用户消息（失败时回滚，见 finally 分支）
            const userMsg = { uid: ++_uid, role: 'user', content: q, html: '' };
            this.messages.push(userMsg);
            this.input = '';
            this.loading = true;
            this.rerankUsed = '';
            this.stageText = '正在检索…';
            this.scrollToBottom();

            const botMsg = {
                uid: ++_uid, role: 'assistant', content: '', html: '',
                sources: [], confusable: [], filteredOut: 0,
                streaming: true,      // 生成中：.qa-answer 走 pre-wrap 纯文本样式
            };
            this.messages.push(botMsg);
            const bot = this.messages[this.messages.length - 1];

            try {
                await this._streamAsk(body, bot);
            } catch (e) {
                // **仅**传输层失败才走兜底：`fetch` 抛错 / 响应非 2xx / `resp.body` 缺失 /
                // reader 读流中断。应用层错误由 SSE 的 `error` 帧承载，已在 _handleSseChunk
                // 内就地展示（那里**不 throw**）⇒ 不会走到这里、不会重发同一问题。
                console.error('[qa] 流式请求失败（传输层），回退非流式:', e);
                await this._fallbackAsk(body, bot, userMsg);
            } finally {
                this.loading = false;
                // 收尾：用完整管线（含 KaTeX）重渲染一次，公式在此时排版
                bot.streaming = false;
                if (bot.content) {
                    bot.html = this.renderMarkdown(bot.content, bot.sources);
                }
                this.scrollToBottom();
                await this.loadSessions();
            }
        },

        // SSE 读取。不用 EventSource：它只支持 GET，而我们需要 POST body
        // （问题 + 筛选 + session_id）。
        // 与兜底路径同一 URL，仅以 stream 标志区分响应形态（后端为单一入口）。
        async _streamAsk(body, bot) {
            const resp = await fetch('/qa/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...body, stream: true }),
            });
            if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

            const reader = resp.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buf = '';

            for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });
                // SSE 以空行分隔消息；保留最后一段不完整数据在 buf 中
                const chunks = buf.split('\n\n');
                buf = chunks.pop() || '';
                for (const chunk of chunks) this._handleSseChunk(chunk, bot);
            }
        },

        _handleSseChunk(chunk, bot) {
            let event = 'message';
            const dataLines = [];
            for (const line of chunk.split('\n')) {
                if (line.startsWith('event:')) event = line.slice(6).trim();
                else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
            }
            if (!dataLines.length) return;
            let data;
            try {
                data = JSON.parse(dataLines.join('\n'));
            } catch (e) {
                console.warn('[qa] SSE 数据解析失败，跳过该帧:', e);
                return;
            }

            if (event === 'stage') {
                // ⚠️ 后端 stage **只有两态**（`reranking` 已在施工中删除：检索与 CE 精排都在同一个
                // 同步函数内、无法从里面 yield，拆两帧是假装能区分它们；见设计文档 §4.11 的实现修订）。
                // 保留一个 `×: '处理中…'` 兜底，但**不要**再加回 `reranking` 分支。
                this.stageText = {
                    retrieving: '正在检索…',
                    generating: '生成中…',
                }[data.stage] || '处理中…';
            } else if (event === 'delta') {
                bot.content += data.text || '';
                // 流式期间降级渲染：marked + DOMPurify，不跑 KaTeX
                // 生成期间输出转义纯文本（CSS 的 white-space: pre-wrap 保留换行）。
                // 不跑任何 Markdown 解析器：半截 Markdown（未闭合的 ** / 表格 / 公式）
                // 会让解析器反复重排，比"纯文本 → 一次性排版好"更晃眼。
                bot.html = this._streamingHtml(bot.content);
                this.scrollToBottom();
            } else if (event === 'done') {
                bot.sources = data.sources || [];
                bot.confusable = data.confusable_hits || [];
                bot.filteredOut = data.filtered_out || 0;
                this.rerankUsed = data.rerank_used || '';
                this.currentSessionId = data.session_id || this.currentSessionId;
                // 当轮筛选：既记在本条答案下（历史追溯），也更新输入框上方的
                // 「本轮生效」——后者与 store 不一致时 filtersChanged() 会提示
                const ft = this.describeFilters(data.effective_filters);
                bot.filtersText = ft;
                this.effectiveFiltersText = ft;
            } else if (event === 'error') {
                // ⚠️ **不要 `throw`**！_handleSseChunk 由 _streamAsk 调用，throw 会一路冒到
                // send() 的 catch → 立刻走 _fallbackAsk **重发同一个问题**：后端已经报错
                // （未配置后端 / 模型不可用）时这是白等一轮，且用户看到**两次完整生成**
                // （重复计费、重复落库）。
                // 边界：SSE 的 `error` 事件是**应用层**错误（后端已受理请求并显式回了 error 帧），
                // ⇒ 只展示与记录，**不进兜底分支**。
                // `_fallbackAsk` 只服务**传输层失败**（`fetch` 抛错 / `resp.body` 缺失 /
                // reader 读流中断）——见 send() 的 catch 注释。
                bot.content = data.message || 'AI 服务返回错误';
                console.error('[qa] AI 服务返回错误（不再自动重发）:', data.message);
            }
        },

        // 流式失败兜底：同一 URL 重发，只是不带 stream → 走一次性 JSON。功能不丢。
        // **只用于传输层失败**（fetch 抛错 / 读流异常），不作为应用层错误的补救路径。
        async _fallbackAsk(body, bot, userMsg) {
            try {
                const r = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...body, stream: false }),
                });
                const data = await r.json();
                bot.content = data.answer || '(AI 未返回回答)';
                bot.sources = data.sources || [];
                bot.confusable = data.confusable_hits || [];
                bot.filteredOut = data.filtered_out || 0;
                this.rerankUsed = data.rerank_used || '';
                this.currentSessionId = data.session_id || this.currentSessionId;
                const ft = this.describeFilters(data.effective_filters);
                bot.filtersText = ft;
                this.effectiveFiltersText = ft;
            } catch (e2) {
                console.error('[qa] 非流式兜底同样失败:', e2);
                bot.content = '请求失败，请稍后重试';
                // 用户消息回滚：本轮未成功，不留在界面上误导
                this.messages = this.messages.filter(m => m.uid !== userMsg.uid);
            }
        },

        // 一键放宽：把上一个问题以「不带分类维度」重发一次。
        // 保留原回答供对照，因此是一次新的问答轮次而非就地替换。
        async relax() {
            const lastUser = [...this.messages].reverse()
                .find(m => m.role === 'user');
            if (!lastUser || this.loading) return;
            this.input = lastUser.content;
            await this.send({ relaxed: true });
        },
```

在 `qa.js` 顶部新增转义工具（模块级）：

```js
// HTML 转义：兜底渲染路径插入用户/DB 可控字符串前必须转义（开发铁律 1.1）
function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
```

- [ ] **Step 4: 提升版本号并重启**

`base.html`：**只 bump `qa.js`**：`qa.js?v=19` → `?v=20`。
（首版这里还写了把 `md-render.js` 的版本号从 v=13 bump 到 v=14——已删：**本 Task 不改 `md-render.js`**，
bump 一个没改过的文件只会让版本号空转。）

- [ ] **Step 5: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t5`
Expected: 全部 `PASS` —— **条数由脚本自报，不要在计划里写死**（首版手写的「3 行 / 5 行」与 CASES 实际登记数不符，且 T3~T5 的 Run 键名整体错位了一位，已修）。脚本会打印本次运行的用例名与 PASS/FAIL 计数，照它核对。

- [ ] **Step 6: 提交**

```bash
git add static/components/qa.js app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 流式输出（SSE 读取 + 流式期间降级渲染）"
```

---

## 完成标准

- [ ] `scripts/probe_qa_ui.py` 全部用例通过（t1~t5）
- [ ] 手工复核：QA 页折叠/展开会话管理、切换历史会话续聊、重命名、删除二次确认、**导出 Markdown 下载**、搜索跳转高亮、流式渐进渲染 + 收尾公式排版
- [ ] 手工复核：QA 页 URL 可复制分享，粘贴到新标签能还原同一筛选与页面
- [ ] **回归**：检索页的翻页、换词、分类树筛选、CE 精排、状态过滤五条路径均正常
- [ ] **回归**：规范 / 规则 / 词库 / 维护 / 审核 各页均**不出现** QA 界面
- [ ] **回归**：条文详情弹窗（回复中的超链接）行为**不变**
- [ ] **回归**：AI 后端设置（⚙️）可从 QA 页头部打开；原文摘抄 / 综合问答模式切换正常
- [ ] 5 个 Task 各自单次提交，提交信息符合 `type: 描述` 规范
- [x] 清理：删除 `data/_probe_qa.db`（副本库）✓
- [x] ~~删除 `scripts/probe_qa_ui.py`~~ → **改为保留**（2026-09-24 用户裁定）：它是本仓唯一的前端行为验收网
      （34 条用例），运行方式与前置已写进脚本自身的 docstring。**请勿按本条旧计划删它。**
- [ ] 还原临时改动：若验证时改过 `DATABASE_PATH` 指向副本库，务必还原

## 遗留与后续

- **CLI 后端取消**（设计文档 D11 提及）：3 个调用点 `classifier_ai.py:20`、`import_routes.py:113`、`qa_routes.py:264`。`APIBackend.classify_batch_sync` 已实现，功能上可覆盖。**不在本计划范围**，需另立计划。
- **模型安装期可选化**（设计文档 D12）：属封装方案范畴，留待封装时统一设计。
- **`.center-panel-v2` 的滚动与 QA 页高度**：已用最小复现实测确认——`.center-panel-v2:has(.qa-root) { height:100% }` 在 1600×900 下使 `.qa-root` 高度正确解析为 852px，内部滚动与贴底输入框均正常，且 `:has()` 收窄后不影响其它页面。（实测时锚点曾是 `#main-content`；改为整页导航后该壳层不存在，锚点变为 `.center-panel-v2`——规则本身不变。）**若将来浏览器不支持 `:has()`**（项目用 Chrome/Edge，已支持），退路是给 `.center-panel-v2` 补 `display:flex; flex-direction:column` + 其直接子元素 `flex:1; min-height:0`。
- **QA 页与检索结果的双滚动条**：`.center-panel-v2` 本身可滚，`.qa-messages` 也可滚。QA 页下 `.qa-root` 占满高度，`.center-panel-v2` 应不产生滚动；若实施时发现外层也滚，给 `.center-panel-v2:has(.qa-root)` 补 `overflow: hidden`。
- **QA 页的 Alpine 初始化**：整页导航走 DOMContentLoaded，与 `/specs`、`/rules`、`/lexicon` 完全同形——本项目已验证过无数次，故 T1 的 `t1_qa_entry_is_a_page_link` 探针即是这一条的守卫（若 Alpine 未接管，`.qa-sessions` 的 `x-show` 不会生效、点击树标签不会走 `isQaView()` 分支）。

---

> 本计划的评审记录见 `2026-09-21-qa-backend-history-degradation.md` 末尾的「评审记录」与 `## GSTACK REVIEW REPORT`。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | `codex exec`（由 /plan-eng-review 自动发起） | Independent 2nd opinion | 1 | completed | 18 条：12 条经核实为真、6 条假阳性 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_found | 13 项发现，**全部已处置** |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**OUTSIDE COVERAGE:** provider=codex · phase=plan-review · **completed**（`codex exec` EXIT=0，32,333 tokens，只读沙箱）。
Codex 自述其 shell executor 故障（`CreateProcess helper_unknown_error`），**无法读取仓库**，结论全部由提示词文本推导——
因此其中 6 条（会话标题 XSS、`done` 事件缺 `effective_filters`、T1 需改 `context.py`、`build_history` 无预算来源、
`relax()` 产生 Q,A,A、`filter_by_score` 需跳过）经逐条核实为**假阳性**，已排除。

**CROSS-MODEL:** 两端一致的三条——① 流式路由复制检索链是架构缺陷；② 多轮上下文预算无明确上界；
③ `_prepare_qa_context` 的抽取应前置而非放在最后一个任务。分歧一条：Codex 建议流式渲染改用纯文本
（**已采纳**，见 D15）；它另建议把模型降级修正拆成独立交付单元**先交付**（未采纳，用户选择并入本轮）。

**VERDICT:** ENG REVIEWED — 13 项发现全部处置完成，无未决项。两份计划（后端 15 Task / 前端 5 Task）可进入实施。

NO UNRESOLVED DECISIONS
