# QA 前端：容器契约 + 界面右置 + 筛选统一 + 会话 UI + 流式渲染 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AI 问答从弹窗改为中栏内的右侧常驻界面，接入会话列表与续聊，让分类树筛选对检索页与 QA 页同时生效，并把流式输出渲染到界面。

**Architecture:** 先在中栏引入稳定壳层 `#main-content` 并把 htmx swap 目标迁进去（否则输入框每次检索都被冲掉），再在其内部用 flex 分两列承载「对话区 | 会话管理（可折叠）」。筛选统一靠一个共享的 `view` 状态：在 QA 页时左栏筛选只更新状态、不发检索，从而让「下一轮生效」成立。流式渲染全程降级——流式期间不跑 KaTeX，收尾时跑一次完整管线。

**Tech Stack:** FastAPI + Jinja2 · htmx · Alpine.js · marked / DOMPurify / KaTeX · Playwright（验证）

**Spec:** `docs/superpowers/specs/2026-09-21-qa-history-session-design.md`
**前置计划:** `docs/superpowers/plans/2026-09-21-qa-backend-history-degradation.md`（**必须先完成并合入**——本计划依赖 `/qa/sessions*`、`/qa/search`、`/qa/ask/stream`、`rerank_used` 字段）

## Global Constraints

- 前端渲染用户生成内容**必须**转义，防范 XSS（开发铁律 1.1）；本计划中凡插入 DB/用户可控字符串，先 `esc()` 或用 `x-text`（禁用 `x-html`）
- 网络请求必须捕获指定异常，禁止裸 `catch(e) {}` 吞掉错误（开发铁律 1.2）；允许带 `console.error` 的兜底捕获
- 循环内禁止发起网络请求（开发铁律 1.3）
- 业务常量集中管理，禁止魔法数字散落（开发铁律 1.3）
- 改静态文件/模板后：浏览器必须 **Ctrl+F5 强刷**，且 `base.html` 里 `<script src="...?v=N">` 的 `N` **必须递增**（项目 CLAUDE.md 三·5）
- **每次 Task 完成后追加一次静态资源版本号**（`app.css?v=N` / `qa.js?v=N` 等），否则用户拿到旧缓存
- 服务重启严格按项目 CLAUDE.md 三·1~4 执行（Windows 下 `--reload` 是双进程，必须全杀）
- **UI 验证用隔离实例**：副本库 + 独立端口 + 临时管理员，不污染 dev 库与 8000 端口
- 提交信息格式 `type: 描述`，单 Task 单提交

## 前置：隔离验证实例（每个 UI Task 都用到）

不要在生产 dev 库上验证 UI 改动。按下面步骤起一个一次性实例：

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_qa.db                      # 副本库，验证完删除
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

> 库路径通过 `app.database.DATABASE_PATH` 决定；副本验证时用环境变量或临时改常量指向 `data/_probe_qa.db`（**验证完必须还原**）。
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

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/templates/base.html` | 页壳、脚本标签、弹窗容器 | 修改 |
| `app/templates/partials/qa_page.html` | QA 页（对话区 + 会话管理） | **新建** |
| `app/templates/partials/qa_panel.html` | QA 面板（原弹窗内容） | **删除**（T2 后无引用） |
| `app/templates/partials/tree_panel.html` | 左栏（QA 入口按钮、CE tooltip） | 修改 |
| `app/templates/partials/result_list.html` | 检索结果包装 | 不改（`#search-results` 语义保持） |
| `static/components/qa.js` | QA 组件（会话、筛选、流式渲染） | 重写 |
| `static/components/tree.js` | 分类树（`view` 判断） | 修改 |
| `static/components/search.js` | 搜索框（`view` 判断） | 修改 |
| `static/components/md-render.js` | 渲染管线（新增无 KaTeX 的流式档） | 修改 |
| `static/app.css` | QA 页布局与折叠 | 修改 |
| `app/routes/qa_routes.py` | 新增 `GET /qa` 页面路由 | 修改 |
| `scripts/probe_qa_ui.py` | Playwright 验证探针（一次性，验证完删） | **新建** |

---

## Task 1: 中栏稳定壳层 `#main-content`

**Files:**
- Modify: `app/templates/base.html:23-25`
- Modify: `static/components/search.js:117-120`、`static/components/tree.js:95-98`
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Produces: DOM 容器 `#main-content`（`.center-panel-v2` 的**唯一子元素**，htmx swap 目标）
- 不变量：`.center-panel-v2` 本身**永不**被替换；`#search-results` 语义不变（翻页仍 `hx-target="#search-results"`）

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


def t1_main_content_survives_search(page):
    """正常场景：检索后 #main-content 元素本身仍在（只换子节点）。

    这是输入框不被冲掉的前提——若 swap 目标仍是 .center-panel-v2，
    该元素会被整体替换，其内部所有状态（含输入框内容）丢失。
    """
    page.goto(f"{BASE}/")
    assert page.locator("#main-content").count() == 1, "缺少 #main-content 壳层"
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    assert page.locator("#main-content").count() == 1, \
        "#main-content 被替换了：swap 目标仍在 .center-panel-v2"


def t1_pagination_still_works(page):
    """边界场景：翻页仍作用于 #search-results（不能连带换掉壳层）。"""
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    assert page.locator("#search-results").count() == 1


def t1_scroll_resets_on_new_search(page):
    """异常场景（回归）：换词搜索后滚动条回到顶部。

    search.js 的 afterSettle 回顶逻辑依赖滚动容器，改动 swap 目标时
    容易连带改坏这一条。
    """
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    page.evaluate("document.querySelector('.center-panel-v2').scrollTop = 500")
    page.fill("input[type=search]", "钢筋")
    page.press("input[type=search]", "Enter")
    page.wait_for_timeout(1200)
    top = page.evaluate("document.querySelector('.center-panel-v2').scrollTop")
    assert top == 0, f"新搜索后未回顶，scrollTop={top}"


CASES = {"t1": [t1_main_content_survives_search,
                t1_pagination_still_works,
                t1_scroll_resets_on_new_search]}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "t1"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1600, "height": 900})
        for fn in CASES[which]:
            fn(pg)
            print(f"PASS {fn.__name__}")
        browser.close()
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t1`
Expected: FAIL — `AssertionError: 缺少 #main-content 壳层`

- [ ] **Step 3: 实现**

`app/templates/base.html`，把 `<main>` 块改为：

```html
        <main class="center-panel-v2">
            <!-- 稳定壳层：htmx 的 swap 目标。仅子节点被替换，壳层本身永不被换，
                 故其内部（检索结果 / QA 页）的输入框等状态不会随检索丢失 -->
            <div id="main-content">
                {% if center_content %}{% include center_content %}{% endif %}
            </div>
        </main>
```

`static/components/search.js`，把 `search()` 的 htmx 调用改为：

```js
            htmx.ajax('GET', `/search?${params.toString()}`, {
                target: '#main-content',
                swap: 'innerHTML'
            });
```

`static/components/tree.js`，把 `dispatchSearch()` 的 htmx 调用改为：

```js
                htmx.ajax('GET', `/search?${params.toString()}`, {
                    target: '#main-content',
                    swap: 'innerHTML'
                });
```

> **不要改** `search.js` 的 afterSettle 回顶逻辑——滚动容器仍是 `.center-panel-v2`（它有 `overflow-y:auto`），
> `#main-content` 不是滚动容器。把 `document.querySelector('.center-panel-v2').scrollTop = 0` 原样保留。

- [ ] **Step 4: 提升静态资源版本号并重启服务**

`base.html` 中 `app.css?v=19` → `?v=20`；`search.js?v=9` → `?v=10`；`tree.js?v=6` → `?v=7`。

按项目 CLAUDE.md 三·1~4 重启 8123 实例（全杀残留进程）。

- [ ] **Step 5: 运行探针确认通过**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t1`
Expected: 3 行 `PASS`

- [ ] **Step 6: 手工复核另两条入口**

浏览器访问 `http://127.0.0.1:8123`，强刷（Ctrl+F5），确认：
1. 点分类树任一节点 → 结果区更新，**输入框所在栏未闪烁/未重建**
2. 勾选「启用 CE 精排」→ 结果重排，雷达动画正常显示与消失

- [ ] **Step 7: 提交**

```bash
git add app/templates/base.html static/components/search.js static/components/tree.js scripts/probe_qa_ui.py
git commit -m "refactor: 中栏引入 #main-content 稳定壳层，htmx swap 目标迁入"
```

---

## Task 2: `/qa` 页面路由与 QA 页布局

**Files:**
- Modify: `app/routes/qa_routes.py`（新增 `GET /qa`）
- Create: `app/templates/partials/qa_page.html`
- Modify: `app/templates/partials/tree_panel.html:115-117`（入口按钮）
- Modify: `app/templates/base.html`（移除 QA 弹窗 overlay）
- Modify: `static/app.css`
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `#main-content`（T1）
- Produces: 路由 `GET /qa` → 渲染 `partials/qa_page.html` 进 `#main-content`
- Produces: DOM 结构 `#qa-root > (.qa-thread > (.qa-messages, .qa-composer), .qa-sessions)`，其中 `.qa-sessions` 可折叠
- 不变量：`.qa-messages` 是消息滚动容器、`.qa-composer` 贴其底部；`.qa-sessions` 折叠**不得**影响 `.qa-composer` 位置

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`（同时把新用例登记进 `CASES["t2"]`）：

```python
def t2_qa_button_opens_qa_page(page):
    """正常场景：点左栏「AI问答」→ 中栏换成 QA 页（不是弹窗）。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    assert page.locator("#qa-modal-overlay").count() == 0, "弹窗应已移除"
    assert page.locator("#main-content").count() == 1, "QA 页必须装在壳层内"


def t2_sessions_panel_collapses_without_moving_composer(page):
    """边界场景（核心）：折叠会话管理栏，输入框位置不变。

    这是「输入框挪到结果栏底部」这条需求的验收点——折叠会话管理
    绝不能把输入框一起带走。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    before = page.locator(".qa-composer").bounding_box()
    page.click("#qa-session-toggle")
    page.wait_for_timeout(300)
    after = page.locator(".qa-composer").bounding_box()
    assert before and after
    assert abs(before["y"] - after["y"]) < 2, \
        f"折叠会话管理后输入框纵向位移 {after['y'] - before['y']}px，应保持不动"
    assert after["x"] + after["width"] > before["x"] + before["width"], \
        "折叠后对话区应变宽"


def t2_qa_page_not_shown_on_other_pages(page):
    """异常场景（回归）：其它页面不得冒出 QA 界面。

    .center-panel-v2 是全站共用的，若 QA 误放进栏壳而非 #main-content，
    会在规范/规则/词库等页面凭空出现。
    """
    for path in ("/specs", "/rules", "/lexicon"):
        page.goto(f"{BASE}{path}")
        assert page.locator("#qa-root").count() == 0, f"{path} 不该出现 QA 界面"
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t2`
Expected: FAIL — `#qa-root` 未出现（按钮仍是弹窗）

- [ ] **Step 3: 实现路由**

在 `app/routes/qa_routes.py` 中新增（放在会话管理接口之前）：

```python
@router.get("/qa")
async def qa_page(request: Request):
    """QA 页：渲染进中栏 #main-content（与检索结果平级的独立界面）。"""
    from app.main import templates
    return templates.TemplateResponse(request, "partials/qa_page.html", {})
```

- [ ] **Step 4: 实现 QA 页模板**

创建 `app/templates/partials/qa_page.html`：

```html
<!-- AI 问答页（中栏内，与检索结果平级的独立界面）
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
                    <template x-if="msg.role === 'bot'">
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
                            <!-- 流式期间用 outerHTML 承载降级渲染结果；收尾后由 renderMarkdown 重渲染 -->
                            <div class="qa-answer" x-html="msg.html"></div>
                            <template x-if="msg.filteredOut > 0">
                                <div class="qa-relax-hint">
                                    <span x-text="'⚠️ 当前分类筛选下仅命中 ' + msg.filteredOut + ' 条，回答可能不完整'"></span>
                                    <button type="button" class="outline"
                                            style="font-size:0.75rem;padding:0.1rem 0.4rem;margin:0 0 0 0.4rem"
                                            @click="relax()">放宽到全部规范</button>
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

        <div class="qa-composer">
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

- [ ] **Step 5: 实现样式**

在 `static/app.css` 的 QA 区块（`.qa-modal-*` 之后）追加：

```css
/* ── QA 页：对话区（主） | 会话管理（右，可折叠） ──
   flex 分列而非新增 grid 列：视觉效果与三栏等价（20% | 50% | 30%），
   但避开 .app-layout 的 grid 改动与 .full-width（审查页）分支的连带影响。 */
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

- [ ] **Step 6: 改造入口按钮 + 移除弹窗**

`app/templates/partials/tree_panel.html`，把 QA 按钮改为：

```html
        <button type="button" class="outline" style="width:100%;font-size:0.8rem"
                hx-get="/qa" hx-target="#main-content" hx-swap="innerHTML">
            🤖 AI问答
        </button>
```

`app/templates/base.html`：
- **删除** `@open-qa-modal="openQA()"` 属性、`openQA()` / `closeQA()` 方法，以及整个 `<div id="qa-modal-overlay"> ... </div>` 块（含其中 `{% include "partials/qa_panel.html" %}`）
- **保留** `#clause-modal-overlay`（条文详情弹窗，本计划明确不改）
- **保留** `{% include "partials/settings_dialog.html" %}`

**同时删除 `app/templates/partials/qa_panel.html`。** 它是弹窗内容模板，`base.html` 的 include 移除后即无引用；新模板 `qa_page.html` 是自包含的（设计上不再拆分）。删除前先确认无残留引用：

```bash
grep -rn "qa_panel.html" app/ static/ | grep -v "__pycache__"
```
Expected: 无输出。若有输出，先改掉那些引用再删。

- [ ] **Step 7: 提升版本号并重启**

`base.html`：`app.css?v=20` → `?v=21`。删除 `qa.js` 标签里的 `?v=18` 引用（T5 会重写 qa.js 后再加回），或暂改为 `?v=19`。

按项目 CLAUDE.md 三·1~4 重启实例。

- [ ] **Step 8: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t2`
Expected: 3 行 `PASS`

> 若 `t2` 因 `qaView()` 尚未实现而报 `Alpine Expression Error`，属预期——T3 补齐组件。
> 此时先确认 `#qa-root`、`.qa-composer`、`#qa-session-toggle` 三个选择器都已渲染出来即可。

- [ ] **Step 9: 提交**

```bash
git add app/routes/qa_routes.py app/templates/partials/qa_page.html \
        app/templates/partials/tree_panel.html app/templates/base.html \
        static/app.css scripts/probe_qa_ui.py
git commit -m "feat: QA 从弹窗改为中栏内右侧常驻界面（会话管理可折叠）"
```

---

## Task 3: `view` 状态与「QA 页筛选不发检索」

**Files:**
- Modify: `static/components/tree.js`
- Modify: `static/components/search.js`
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Produces: `$store.searchState.view`（`'search'` | `'qa'`），初值 `'search'`
- Consumes: `#qa-root`（T2）的存在性作为 QA 视图判据
- 行为契约：`view === 'qa'` 时，`selectFilter()` / `onStatusChange()` / `onCeChange()` **只更新状态、不发检索**；`search()`（回车）不受限，用于从 QA 页切回检索页

> **为何用 DOM 存在性而非 Alpine 生命周期**：`qaView` 的 `destroy()` 在 htmx swap 场景下是否触发未经验证（设计文档 §4.3 已标注为待验证项）。用 `#qa-root` 存在性判断零风险且同样准确。

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`：

```python
def t3_tree_click_in_qa_page_does_not_navigate(page):
    """正常场景（核心）：QA 页内点分类树，不跳回检索结果页。

    这是「切换筛选影响下一轮检索」成立的前提——若仍触发 dispatchSearch，
    用户会被弹回检索页。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.click(".tree-label >> nth=0")
    page.wait_for_timeout(800)
    assert page.locator("#qa-root").count() == 1, \
        "点分类树后 QA 页消失了——说明仍触发了检索"


def t3_tree_click_in_search_page_still_searches(page):
    """异常场景（回归）：检索页内点分类树仍要立即重搜。"""
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    page.click(".tree-label >> nth=0")
    page.wait_for_selector("#search-results", timeout=10000)
    assert page.locator("#qa-root").count() == 0
    assert page.locator("#search-results").count() == 1


def t3_status_checkbox_in_qa_page_no_search(page):
    """边界场景：QA 页内勾「仅现行」只改状态，不触发检索。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.click("text=仅现行")
    page.wait_for_timeout(800)
    assert page.locator("#qa-root").count() == 1, "勾选状态过滤不应把用户弹出 QA 页"
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t3`
Expected: FAIL — `点分类树后 QA 页消失了`

- [ ] **Step 3: 实现**

`static/components/tree.js`，在 `Alpine.store('searchState', {...})` 中新增字段：

```js
        // 当前视图：'search' 检索页 | 'qa' QA 页。
        // QA 页内左栏筛选只更新状态、不发检索（让「下一轮生效」成立）
        view: 'search',
```

新增模块级判据函数（放在 `document.addEventListener('alpine:init', ...)` 之前）：

```js
// 当前是否处于 QA 视图：以 DOM 存在性判断，不依赖 Alpine 生命周期钩子
// （htmx swap 场景下 destroy() 是否触发未经验证，用 DOM 判据零风险）
function isQaView() {
    return !!document.getElementById('qa-root');
}
```

`tree.js` 的 `selectFilter()`，在更新 filters 之后、`dispatchSearch()` 之前插入判断：

```js
        selectFilter(dimension, value) {
            const next = { ...this.$store.searchState.filters };
            const arr = Array.isArray(next[dimension]) ? next[dimension].slice() : [];
            const idx = arr.indexOf(value);
            if (idx >= 0) arr.splice(idx, 1); else arr.push(value);
            if (arr.length) next[dimension] = arr; else delete next[dimension];
            this.$store.searchState.filters = next;
            // QA 页内：筛选只更新共享状态，影响下一轮问答检索，不发起检索请求
            if (isQaView()) return;
            this.dispatchSearch();
        },
```

`static/components/search.js` 的 `onStatusChange()` 与 `onCeChange()` 各加一行前置判断：

```js
        onCeChange() {
            if (this.ceRerank) {
                showSearchToast('CE精排已开启，请耐心等待搜索结果');
            }
            // QA 页内 CE 开关对问答无效（问答走独立精排链），不触发检索
            if (isQaView()) return;
            this.search();
        },

        onStatusChange() {
            if (!this.statusCurrent && !this.statusRevising) {
                showSearchToast('注意：当前展示结果未过滤非现行规范', 4000);
            }
            // QA 页内：状态过滤只更新共享状态，影响下一轮问答检索
            if (isQaView()) return;
            this.search();
        },
```

> `search()` 本身（回车触发）**不加**判断——在 QA 页按回车搜索即切回检索页，这是符合直觉的显式动作（设计文档 §4.3）。

- [ ] **Step 4: 提升版本号并重启**

`base.html`：`tree.js?v=7` → `?v=8`；`search.js?v=10` → `?v=11`。

- [ ] **Step 5: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t3`
Expected: 3 行 `PASS`

- [ ] **Step 6: 提交**

```bash
git add static/components/tree.js static/components/search.js app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 页内左栏筛选只更新状态不发检索"
```

---

## Task 4: 筛选统一（QA 读 store）+ CE tooltip

**Files:**
- Modify: `app/templates/partials/tree_panel.html:14-18`（CE tooltip）
- Modify: `static/components/qa.js`
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `$store.searchState`（`tree.js`）
- Produces: `qaView().buildRequestBody(question)` —— 请求体携带 `status_filter`（由 store 组合）与 `include_non_clause`
- 行为契约：QA 面板内**没有**独立的状态过滤复选框；状态/前言筛选一律来自左栏共享 store

> 模板侧无需改动：T2 的全新 `qa_page.html` 从一开始就没有重复的状态复选框
> （旧的 `qa_panel.html` 已在 T2 删除）。本 Task 只补 CE tooltip 与 `qa.js` 的取数逻辑。

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`：

```python
def t4_qa_page_has_no_duplicate_status_checkboxes(page):
    """正常场景：QA 页内不再有独立的状态过滤复选框（统一到左栏）。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    in_qa = page.locator("#qa-root").get_by_text("仅现行").count()
    assert in_qa == 0, "QA 面板内不应再有「仅现行」复选框，应统一读左栏"


def t4_ce_rerank_has_tooltip(page):
    """正常场景：CE 精排复选框有说明性 tooltip（防误解，不锁功能）。"""
    page.goto(f"{BASE}/")
    label = page.locator("label:has-text('启用 CE 精排')")
    title = label.get_attribute("title") or ""
    assert "问答" in title or "AI" in title, f"CE 复选框缺少问答相关说明: {title!r}"


def t4_ce_rerank_not_disabled_in_qa_page(page):
    """异常场景（防回退）：QA 页内 CE 复选框不得被置灰。

    历史上的方案是「QA 界面里 CE 置灰」，但那会锁死检索侧的 CE 功能
    （QA 常驻后没有「进入 QA 界面」这个时刻了）。改用 tooltip 说明。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    box = page.locator("input[type=checkbox]").nth(1)
    assert not box.is_disabled(), "CE 精排复选框在 QA 页被置灰了，应仅用 tooltip 说明"
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t4`
Expected: FAIL — `QA 面板内不应再有「仅现行」复选框`

- [ ] **Step 3: 实现**

`app/templates/partials/tree_panel.html`，给 CE 复选框所在 label 加 `title`：

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

`static/components/qa.js`，把本地状态与组合逻辑改为读 store：

```js
        // 状态过滤改为读左栏共享 store（统一操作逻辑）：
        // QA 面板内不再有独立复选框，「仅现行 / 修订中」一律由左栏控制
        get statusFilter() {
            return this.$store.searchState.buildStatusFilter();
        },

        // 组合请求体：分类维度 + 状态过滤 + 前言放行，全部来自共享 store
        buildRequestBody(question) {
            const filters = (this.$store && this.$store.searchState)
                ? this.$store.searchState.filters : {};
            const body = { question, mode: this.mode, ...filters };
            const sf = this.statusFilter;
            // buildStatusFilter() 全不勾返回 null → 传空串（不过滤非现行），与既有语义一致
            body.status_filter = sf === null ? '' : sf;
            // 左栏「包含前言·条文说明」勾选状态，影响下一轮检索范围
            body.include_non_clause = !!this.$store.searchState.includeNonClause;
            if (this.currentSessionId) body.session_id = this.currentSessionId;
            return body;
        },
```

- [ ] **Step 4: 提升版本号并重启**

`base.html`：`tree.js?v=8` → `?v=9`；`qa.js` 版本号在 T5 统一处理。

- [ ] **Step 5: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t4`
Expected: 3 行 `PASS`

- [ ] **Step 6: 提交**

```bash
git add app/templates/partials/tree_panel.html static/components/qa.js \
        app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 筛选统一读共享 store + CE 精排加 tooltip 说明"
```

---

## Task 5: 会话列表、切换载入与续聊

**Files:**
- Modify: `static/components/qa.js`
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

追加到 `scripts/probe_qa_ui.py`：

```python
def t5_ask_creates_session_and_lists_it(page):
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


def t5_history_shows_full_conversation(page):
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


def t5_continue_in_history_session_appends(page):
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
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t5`
Expected: FAIL — `.qa-session-title` 未出现（会话列表未加载）

- [ ] **Step 3: 实现**

重写 `static/components/qa.js`。**必须保留**：模式切换（`toggleMode`）、`renderMarkdown`、`openClause`、`scrollToBottom`。以下是新增/改写部分：

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

        async init() {
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
                this.messages = (data.messages || []).map(m => ({
                    uid: ++_uid,
                    role: m.role,
                    content: m.content,
                    html: m.role === 'bot'
                        ? this.renderMarkdown(m.content, m.sources) : '',
                    sources: m.sources || [],
                    confusable: m.confusable || [],
                    filteredOut: 0,
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

        // ── 降级状态提示 ──

        rerankBadge() {
            if (this.rerankUsed === 'crossencoder') return '⚡ CE 精排';
            if (this.rerankUsed === 'vector') return '≈ 向量精排';
            return '⚠️ 无精排';
        },

        rerankTip() {
            if (this.rerankUsed === 'crossencoder') return 'CrossEncoder 精排生效';
            if (this.rerankUsed === 'vector') return 'CE 模型不可用，已降级为向量精排';
            return '未检测到精排模型，当前按关键词排序；'
                 + '请在维护页「健康检查」查看缺失的模型';
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

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t5`
Expected: 3 行 `PASS`

> 若 LLM 未配置导致回答失败，`t5` 中 `.qa-bot` 不会出现——先确认 `ai.backend.qa` 已配置可用后端（隔离库是副本，配置随副本带过来）。

- [ ] **Step 6: 提交**

```bash
git add static/components/qa.js static/app.css app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 会话列表、切换载入与续聊交互"
```

---

## Task 6: 流式渲染（SSE + 降级渲染）

**Files:**
- Modify: `static/components/md-render.js`（新增无 KaTeX 的流式档）
- Modify: `static/components/qa.js`（`send()` 改走 SSE）
- Test: `scripts/probe_qa_ui.py`

**Interfaces:**
- Consumes: `POST /qa/ask/stream`（后端计划 T15），事件 `stage` / `delta` / `done` / `error`
- Produces: `window.mdRender.renderStreaming(md) -> string`——marked + DOMPurify，**不跑 KaTeX**
- 行为契约：流式期间增量渲染用 `renderStreaming`；收到 `done` 后改用 `renderMarkdown`（完整管线含 KaTeX + 源链接改写）

> **为何必须降级**：一次回答有几十上百个 delta，每个都跑 KaTeX auto-render 会卡死；且公式未闭合时（`$$E = mc^`）KaTeX 会渲染失败甚至抛错。详见设计文档 §4.11。

- [ ] **Step 1: 写失败探针**

追加到 `scripts/probe_qa_ui.py`：

```python
def t6_streaming_renders_progressively(page):
    """正常场景：首字节早于完整回答，内容渐进出现。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    # 流式：先出现部分文本（时长阈值放宽，避免慢网络误判）
    page.wait_for_function(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a && a.innerText.trim().length > 0; }",
        timeout=60000)
    partial = page.locator(".qa-bot .qa-answer").first.inner_text()
    assert partial.strip(), "流式首帧应有文本"


def t6_streaming_avoids_katex_mid_stream(page):
    """异常场景（关键约束）：流式进行中不得出现 KaTeX 渲染产物。

    流式期间跑 KaTeX 会因公式未闭合而报错，且性能不可接受。
    判据：进行中不应有 .katex 节点；结束后才允许出现。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定 并给出公式")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_function(
        "() => { const a = document.querySelector('.qa-bot .qa-answer');"
        " return a && a.innerText.trim().length > 0; }",
        timeout=60000)
    # 此刻仍在流式中（发送按钮 disabled），不应有 katex 节点
    still_streaming = page.locator(".qa-composer-btns button").first.is_disabled()
    if still_streaming:
        assert page.locator(".qa-bot .qa-answer .katex").count() == 0, \
            "流式进行中出现了 KaTeX 渲染，应延迟到 done 之后"


def t6_stage_indicator_visible(page):
    """正常场景：等待期间显示阶段进度，覆盖流式之前的死时间。"""
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    page.wait_for_selector(".qa-stage:visible", timeout=15000)
    assert page.locator(".qa-stage").first.inner_text().strip(), "阶段提示不应为空"
```

- [ ] **Step 2: 运行探针确认失败**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t6`
Expected: FAIL — `.qa-answer` 无内容或 `.qa-stage` 不出现（仍在用非流式 `/qa/ask`）

- [ ] **Step 3: 在 md-render.js 增加流式档**

在 `static/components/md-render.js` 中，`renderHtml` 之后新增：

```js
    // 流式专用：只做 marked → DOMPurify，**不跑 KaTeX**。
    // 原因（设计文档 §4.11）：一次回答几十上百个 delta，每个都跑 KaTeX
    // auto-render 会卡死；且公式写到一半（"$$E = mc^"）未闭合会让 KaTeX 报错。
    // 收尾时改用 renderInto/renderHtml 跑完整管线，公式在那一刻才变成排版结果。
    function renderStreaming(md) {
        let raw = fixOrphanSup(rewriteImg(md || '', '')).replace(/\\n/g, '<br>');
        raw = raw.replace(/\\\(/g, '$').replace(/\\\)/g, '$');
        let html;
        try {
            html = window.marked.parse(raw, { gfm: true, breaks: true });
        } catch (e) {
            html = String(raw).replace(/</g, '&lt;');
        }
        return window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
    }
```

并把它加入导出对象：

```js
    window.mdRender = {
        fixOrphanSup: fixOrphanSup,
        rewriteImg: rewriteImg,
        katexize: katexize,
        renderInto: renderInto,
        renderHtml: renderHtml,
        renderStreaming: renderStreaming,
        renderSearchResults: renderSearchResults,
    };
```

- [ ] **Step 4: 在 qa.js 实现 `send()`（SSE）**

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
                uid: ++_uid, role: 'bot', content: '', html: '',
                sources: [], confusable: [], filteredOut: 0,
            };
            this.messages.push(botMsg);
            const bot = this.messages[this.messages.length - 1];

            try {
                await this._streamAsk(body, bot);
            } catch (e) {
                console.error('[qa] 流式请求失败，回退非流式:', e);
                await this._fallbackAsk(body, bot, userMsg);
            } finally {
                this.loading = false;
                // 收尾：用完整管线（含 KaTeX）重渲染一次，公式在此时排版
                if (bot.content) {
                    bot.html = this.renderMarkdown(bot.content, bot.sources);
                }
                this.scrollToBottom();
                await this.loadSessions();
            }
        },

        // SSE 读取。不用 EventSource：它只支持 GET，而我们需要 POST body
        // （问题 + 筛选 + session_id）。
        async _streamAsk(body, bot) {
            const resp = await fetch('/qa/ask/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
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
                this.stageText = {
                    retrieving: '正在检索…',
                    reranking: '已召回条文，精排中…',
                    generating: '生成中…',
                }[data.stage] || '处理中…';
            } else if (event === 'delta') {
                bot.content += data.text || '';
                // 流式期间降级渲染：marked + DOMPurify，不跑 KaTeX
                if (window.mdRender && window.mdRender.renderStreaming) {
                    bot.html = window.mdRender.renderStreaming(bot.content);
                } else {
                    bot.html = '<pre>' + escHtml(bot.content) + '</pre>';
                }
                this.scrollToBottom();
            } else if (event === 'done') {
                bot.sources = data.sources || [];
                bot.confusable = data.confusable_hits || [];
                bot.filteredOut = data.filtered_out || 0;
                this.rerankUsed = data.rerank_used || '';
                this.currentSessionId = data.session_id || this.currentSessionId;
            } else if (event === 'error') {
                throw new Error(data.message || 'AI 服务返回错误');
            }
        },

        // 流式失败兜底：回退到既有非流式接口，功能不丢
        async _fallbackAsk(body, bot, userMsg) {
            try {
                const r = await fetch('/qa/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await r.json();
                bot.content = data.answer || '(AI 未返回回答)';
                bot.sources = data.sources || [];
                bot.confusable = data.confusable_hits || [];
                bot.filteredOut = data.filtered_out || 0;
                this.rerankUsed = data.rerank_used || '';
                this.currentSessionId = data.session_id || this.currentSessionId;
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

- [ ] **Step 5: 提升版本号并重启**

`base.html`：`md-render.js?v=13` → `?v=14`；`qa.js?v=19` → `?v=20`。

- [ ] **Step 6: 运行探针**

Run: `D:/Python/python.exe scripts/probe_qa_ui.py t6`
Expected: 3 行 `PASS`

- [ ] **Step 7: 提交**

```bash
git add static/components/md-render.js static/components/qa.js app/templates/base.html scripts/probe_qa_ui.py
git commit -m "feat: QA 流式输出（SSE 读取 + 流式期间降级渲染）"
```

---

## 完成标准

- [ ] `scripts/probe_qa_ui.py` 全部用例通过（t1~t6）
- [ ] 手工复核：QA 页折叠/展开会话管理、切换历史会话续聊、重命名、删除二次确认、**导出 Markdown 下载**、搜索跳转高亮、流式渐进渲染 + 收尾公式排版
- [ ] **回归**：检索页的翻页、换词、分类树筛选、CE 精排、状态过滤五条路径均正常
- [ ] **回归**：规范 / 规则 / 词库 / 维护 / 审核 各页均**不出现** QA 界面
- [ ] **回归**：条文详情弹窗（回复中的超链接）行为**不变**
- [ ] **回归**：AI 后端设置（⚙️）可从 QA 页头部打开；原文摘抄 / 综合问答模式切换正常
- [ ] 6 个 Task 各自单次提交，提交信息符合 `type: 描述` 规范
- [ ] 清理：删除 `data/_probe_qa.db` 与 `scripts/probe_qa_ui.py`（一次性探针，开发铁律七·3）
- [ ] 还原临时改动：若验证时改过 `DATABASE_PATH` 指向副本库，务必还原

## 遗留与后续

- **CLI 后端取消**（设计文档 D11 提及）：3 个调用点 `classifier_ai.py:20`、`import_routes.py:113`、`qa_routes.py:264`。`APIBackend.classify_batch_sync` 已实现，功能上可覆盖。**不在本计划范围**，需另立计划。
- **模型安装期可选化**（设计文档 D12）：属封装方案范畴，留待封装时统一设计。
- **`.center-panel-v2` 的滚动与 QA 页高度**：QA 页用 `height:100%` + 内部 flex 滚动。若实际渲染发现双滚动条或高度塌陷，需给 `.center-panel-v2` 补 `display:flex; flex-direction:column` 或改用 `min-height:calc(100vh - X)`。T2 的探针只验证输入框位置不随折叠移动，未覆盖高度塌陷——实施时若发现，作为 T2 的补充修复。
