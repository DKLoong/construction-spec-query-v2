"""QA 页的浏览器行为回归套件（t1~t5 共 34 条用例）。

**保留决定（2026-09-24，用户裁定）**：原计划把它当「一次性探针，验证完删除」，
但它是本仓**唯一**的前端行为验收网（覆盖整页路由与 URL 筛选、折叠几何、筛选统一、
会话续聊、流式两态渲染、搜索命中高亮、错误边界），故**保留**。
⇒ 请勿按旧计划删它；若将来 UI 大改导致失效，请修用例而不是关掉。

## 运行方式（缺一不可）

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_qa.db                 # 副本库，**不要**在 dev 库上跑
export DATABASE_PATH="$PWD/data/_probe_qa.db"           # 只走环境变量，禁止改源码常量
D:/Python/python.exe "$TEMP/qa_mock_llm.py" 8199 &      # mock LLM（临时工具，不入仓）
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &   # 不加 --reload
# 副本库写入 mock 配置（否则 /qa/ask 会去调真实模型）：
#   ai.backend='custom' / ai.custom.base_url='http://127.0.0.1:8199/v1'
#   ai.custom.api_key='mock' / ai.custom.model='mock-model'
D:/Python/python.exe scripts/probe_qa_ui.py t1          # t1|t2|t3|t4|t5
```

**前置要点**：
- 全站受 `AuthMiddleware` 保护（`app/main.py:110-129`）：runner 会先登录（`PROBE_USER`/`PROBE_PASS` 可覆盖，
  默认取 `scripts/create_admin.py` 的默认账号）。
- **探针与 mock 强绑定**：共享助手 `_qa_ask` 要求回答含 mock 的确定性标记 `GB 50204`
  ⇒ **不要**拿真实模型跑，会成片假红。
- 用例自带前置（如 relax 用例自己把 `qa.retrieve.qa_min_candidates` 调到 30 并在 `finally` 还原）
  ——**不要**依赖副本库里手工设过的状态（曾被「只在上一轮跑过的库里通过」咬过）。
- 改静态 js/css 后，跑之前记得 `base.html` 的 `?v=N` 已递增或强刷（缓存会让探针读到旧文件）。
- 跑完清理：杀掉 8123/8199 两个进程、删除 `data/_probe_qa.db`。

用法（单组）：
  D:/Python/python.exe scripts/probe_qa_ui.py t1
"""
import json
import os
import re
import sqlite3
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"

# ⚠️ 简报缺项（本 Agent 补，已上报控制器）：全站受 AuthMiddleware 保护
# （app/main.py:110-129）——未登录访问**任何**页面都 302 → /login。简报给的探针没有任何
# 登录步骤，直接跑会在 `page.click("text=🤖 AI问答")` 上等不到元素而超时 ⇒ RED 是
# 「因未登录而红」而非「因 /qa 不存在而红」，属于**假红**（真实代码一行未改也会红）。
# 故在 runner 里补一次登录引导；**用例函数体一字未改**。
PROBE_USER = os.environ.get("PROBE_USER", "admin")
PROBE_PASS = os.environ.get("PROBE_PASS", "admin123")


def login(page):
    """登录隔离实例。凭据默认取 dev 库副本里的管理员，可用环境变量覆盖。"""
    page.goto(f"{BASE}/login")
    page.fill("input[name=username]", PROBE_USER)
    page.fill("input[name=password]", PROBE_PASS)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/", timeout=10000)


# ⚠️ 简报第二个缺项（本 Agent 补，已上报控制器）：简报所有用例都写
# `page.click(".tree-label >> nth=0")`，但**分类项在 `<details>`（默认折叠）内**
# （app/templates/partials/tree_panel.html:125-149，无 `open` 属性、也无自动展开逻辑）
# ⇒ Playwright 判定不可见，click 30s 超时（RED/GREEN 都跑不到断言，实测 call log:
#   locator resolved to <span class="tree-label">… element is not visible）。
# 故补一个「先展开第一个维度、再点第一个分类项」的辅助函数；用例语义不变。
def click_first_tree_label(page):
    """展开第一个维度后点击其第一个分类项（模拟用户操作）。

    「展开完成」用**有界可见性等待**收口，不用固定 sleep：`.tree-label` 在折叠的
    `<details>` 内**不可见**（见 `wait_tree_filter_seeded` 的注释），故「DOM 中第一个
    `.tree-label` 变为可见」恰好等价于「第一个维度已展开」——正是下面那次 click
    能成立的前置条件本身。
    原写法 `wait_for_timeout(250)`：快机器上白等，慢机器上不够（只是被 Playwright
    click 自身的 actionability 等待兜住才没红）。
    """
    page.wait_for_selector(".tree-container details", state="attached", timeout=10000)
    page.click(".tree-container details > summary >> nth=0")   # 折叠 → 展开
    page.locator(".tree-label").first.wait_for(state="visible", timeout=10000)
    page.click(".tree-label >> nth=0")


def wait_tree_filter_seeded(page):
    """等分类树渲染出**已选中**的节点（分类树是异步 fetch 后渲染的）。

    用 `state="attached"` 而非默认的 visible：节点在折叠的 `<details>` 内不可见，
    等 visible 会必然超时。回填失败时这里会超时失败——语义仍然是红。
    """
    page.wait_for_selector(".tree-label.active", state="attached", timeout=10000)


# ── 判据工具：**请求计数**（不是结果计数、不是固定 sleep）─────────────────
# 为什么必须有：`#search-results` / `#qa-root` 之类的 DOM 计数在「点击触发了重搜」与
# 「点击什么都没做」两种世界里**都**成立（点击前已 wait 出该元素）⇒ DOM 计数是结构性
# 不可证伪的；`wait_for_timeout(800)` 之类的固定 sleep 在慢机器上会**假绿**。
# 唯一能区分两个世界的是「有没有**新发起**一次 /search 请求」。

def track_search_requests(page):
    """登记 /search 请求监听器，返回累积记录请求 URL 的列表。

    必须在**点击之前**调用：Playwright 不补发注册之前的请求事件，故列表天然是
    「注册之后发出的请求」，`n_before = len(seen)` 即点击前的基线。
    """
    seen = []
    page.on("request", lambda r: seen.append(r.url) if "/search?" in r.url else None)
    return seen


def poll_until(page, predicate, timeout_ms, interval_ms=100):
    """有界轮询 `predicate()`；满足即返回 True，超时返回 False。

    **不无限挂起、不抛异常**（超时交给调用点的 assert 给出精确失败信息）。
    两个用途：
      - 正向断言：等某个事件出现（如"新发起了 /search 请求"、"地址栏被回写维度键"）；
      - 负向断言的有界观察窗：一旦违反立即返回 True（**提前判红**，不是"睡够就算过"）。
        异步副作用无法零等待，负断言本质需要一个窗口；窗口只决定"等多久"，判据仍是计数。
    """
    waited = 0
    while True:
        if predicate():
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(interval_ms)
        waited += interval_ms


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
    click_first_tree_label(page)                # 选中一个分类树条目
    # 原写法 `wait_for_timeout(500)`。这里要等的条件 = **store 里已记下这次选择**
    # （`buildQaUrl()` 读的是 `Alpine.store('searchState').filters`，不是地址栏；见 qa.js:15-28）。
    # `.tree-label.active` 由 `:class` 直接绑定同一个 store 字段（tree.js:66 同步写入，
    # 就在 selectFilter 里、先于 dispatchSearch），故「出现 active 节点」是该条件的忠实代理。
    # 用同文件已有的 helper，不新造机制。
    wait_tree_filter_seeded(page)
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    # ⚠️ 不要写成 `assert "?" in page.url`——那是**恒真**的：`buildQaUrl()` 总会带上
    # `status_filter`（store 默认"仅现行"），所以 query 串必然非空，删掉整个维度回填它照样绿。
    # 也不要写成 `assert "dim" in page.url`——子串判据过松（页面里任何含 `dim` 的字符都满足）。
    # 断言必须落到**一个真实维度键**上（`dimN_xxx=`，与 qa.js 的 QA_DIM_KEYS 同形），
    # 下面这条 + 紧随的 active 计数共同钉住"筛选真的带过来了"
    assert re.search(r"dim\d_[a-z]+=", page.url), \
        f"QA URL 未携带分类维度参数：{page.url}"
    wait_tree_filter_seeded(page)               # 分类树异步渲染，等回填生效
    active = page.locator(".tree-label.active").count()
    assert active >= 1, "QA 页未回填筛选（分类树无选中项）"


def t1_qa_page_filters_survive_reload(page):
    """边界场景：QA 页刷新后筛选仍在（URL 持久化的意义所在）。"""
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    click_first_tree_label(page)
    wait_tree_filter_seeded(page)   # 条件同上条用例：等 store 记下这次选择（替代固定 sleep 500ms）
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    wait_tree_filter_seeded(page)
    n_before = page.locator(".tree-label.active").count()
    page.reload()
    page.wait_for_selector("#qa-root", timeout=10000)
    wait_tree_filter_seeded(page)
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
    click_first_tree_label(page)                # 分类树立即重搜（检索页语义）
    page.wait_for_selector("#search-results", timeout=10000)
    page.evaluate("document.querySelector('.center-panel-v2').scrollTop = 500")
    seen = track_search_requests(page)      # 登记必须在**回车之前**（Playwright 不补发注册前的请求）
    n_before = len(seen)
    page.fill("input[type=search]", "钢筋")
    page.press("input[type=search]", "Enter")
    # 原写法 `wait_for_timeout(1200)`。拆成**一个前置条件 + 一个有界等待**：
    # ① 前置：换词回车必须真的新发起一次 /search——否则"回顶"无从谈起，
    #    且能把「搜索没触发」与「回顶逻辑坏」两种红区分开（本文件的核心方法论：请求计数）；
    # ② 有界等待：等回顶真正发生（htmx:afterSettle 里 `panel.scrollTop = 0`，search.js:168）。
    #    超时即报红且信息明确；固定 sleep 则是"睡够就算过"，慢机器上 scrollTop 仍为 500 ⇒ 假红。
    assert poll_until(page, lambda: len(seen) > n_before, timeout_ms=5000), \
        f"换词回车后未新发起 /search 请求（回车前 {n_before} 次，回车后 {len(seen)} 次）"
    assert poll_until(
        page,
        lambda: page.evaluate("document.querySelector('.center-panel-v2').scrollTop") == 0,
        timeout_ms=3000), \
        "换词搜索后未回顶（检索页回顶逻辑被改坏）"


def t1_tree_click_in_qa_page_does_not_navigate(page):
    """正常场景（D8 核心）：QA 页内点分类树不跳回检索页。

    这是「切换筛选只影响下一轮问答检索」成立的前提——若仍触发检索，
    用户会被弹回检索结果页。

    判据 = **请求计数 + 地址栏正向证据**（旧写法是 `wait_for_timeout(800)` + `#qa-root` 计数，
    慢机器上可能假绿）：
      1. 负向：点击后**不得**新发起 /search 请求（有界观察窗，出现即提前判红）；
      2. 正向：地址栏被回写**真实维度键**——QA 分支末尾就是 `syncQaUrl()`，它出现才证明
         「点击确实被处理过了」，从而排除「点击什么都没做」这种也能满足负断言的假绿
         （慢机器上请求迟到时，这条会兜住）；
      3. QA 界面仍在（未被 htmx 换掉）+ 地址栏仍在 /qa。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    seen = track_search_requests(page)
    n_before = len(seen)
    click_first_tree_label(page)
    # 判据 1（负向）：不得新发起检索请求
    assert not poll_until(page, lambda: len(seen) > n_before, timeout_ms=400), \
        f"QA 页点分类树不应发起检索请求（点击前 {n_before} 次，点击后 {len(seen)} 次）"
    # 判据 2（正向）：地址栏已回写维度键 ⇒ 点击已被处理完（不是"什么都没做"）
    assert poll_until(
        page, lambda: bool(re.search(r"dim\d_[a-z]+=", page.url)), timeout_ms=5000), \
        f"QA 页点分类树未生效（地址栏未回写分类维度键）：{page.url}"
    # 判据 3：QA 界面未被换掉
    assert page.locator("#qa-root").count() == 1, \
        "点分类树后 QA 页消失了——说明仍触发了检索"
    assert "/qa" in page.url, f"被弹回检索页：{page.url}"


def t1_tree_click_in_search_page_still_searches(page):
    """异常场景（回归）：检索页内点分类树仍要立即重搜。

    ⚠️ 判据必须是**请求计数**，不能是结果计数：本用例点击前已经 wait 出了 `#search-results`，
    点击后它的计数在「点击触发了重搜」与「点击什么都没做」两种世界里**都**是 1
    ⇒ 结果计数结构性不可证伪（复核实证：把 tree.js 的检索页分支改成恒早退，旧写法照样绿）。
    现在改为：点击前登记 /search 请求监听，点击后断言**确实新发起**一次检索请求，
    且该请求携带**真实分类维度键**（只数请求还不够，要钉住"这次请求是这次点击引发的"）。
    """
    page.goto(f"{BASE}/")
    page.fill("input[type=search]", "混凝土")
    page.press("input[type=search]", "Enter")
    page.wait_for_selector("#search-results", timeout=10000)
    seen = track_search_requests(page)      # 点击前登记：只统计点击之后发出的请求
    n_before = len(seen)
    click_first_tree_label(page)
    got_new = poll_until(page, lambda: len(seen) > n_before, timeout_ms=5000)
    assert got_new, \
        f"检索页点分类树应新发起一次 /search 请求（点击前 {n_before} 次，点击后 {len(seen)} 次）" \
        "——计数未增即点击未触发重搜"
    new_urls = seen[n_before:]
    assert any(re.search(r"dim\d_[a-z]+=", u) for u in new_urls), \
        f"新发起的 /search 请求未携带分类维度参数（点击未生效）：{new_urls}"


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

    ⚠️ `before` 采样前**必须**先等 `.qa-sessions` 可见（见下面那行 wait_for_selector 的注释）：
    否则采到的是"Alpine 尚未初始化"的中间态，会让宽度判据**假红**，而它的数值签名与真 RED
    一模一样（`1236.8125 -> 1236.8125`），会掩盖真因。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    # 竞态修复：`#qa-root` 在**首屏 HTML 里就有**，而 Alpine 是 defer 加载
    # （app/templates/base.html:12）⇒ 只等 `#qa-root` 不保证 Alpine 已初始化。
    # 初始化之前 `.qa-sessions` 仍带 x-cloak（`[x-cloak]{display:none!important}`，app.css:257）
    # 而**不占宽度**，此时 `.qa-thread`/`.qa-composer` 量到的是"会话栏缺席"的**宽值**；
    # 折叠后宽度不变 ⇒ 宽度判据假红，且签名（宽 -> 同宽）与真 RED（宽 -> 未变宽）无法区分。
    # 等 `.qa-sessions` **可见**即等价于"Alpine 已初始化且 x-show 已求值"：x-cloak 由 Alpine
    # 在启动该组件时移除、x-show="!sessionsCollapsed" 在同一步求值，二者同步完成
    # ⇒ 可见性一旦成立，本次要测量的两处几何（会话栏占宽、composer 位置）都已定型。
    page.wait_for_selector(".qa-sessions", state="visible", timeout=10000)
    before = {s: page.locator(s).bounding_box()
              for s in (".qa-composer", ".qa-messages", ".qa-thread")}
    page.click("#qa-session-toggle")
    # 折叠也是**异步生效**（Alpine 响应式：`x-show` 改 inline display 在下一微任务才落地）。
    # 原写法 `wait_for_timeout(300)` 是「固定 sleep 当同步」：慢机器上 300ms 不够 ⇒ `after` 会采到
    # **旧几何** ⇒ 宽度判据**假红**（与上面 `before` 的竞态同源、方向相反）。
    # 改为**有界轮询**，把"等待"与"判据"分离：等待条件不成立 ⇒ **在此处以 timeout 形式报红**
    # （信息明确：折叠未生效），绝不会让下面的几何判据在旧值上给出误导性的失败信息。
    assert poll_until(page, lambda: page.locator(".qa-sessions").is_hidden(), timeout_ms=3000), \
        "点击折叠按钮后会话管理栏仍未收起（折叠未生效）"
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


def t2_composer_input_shares_row_with_buttons(page):
    """正常场景（I-1 的直接判据）：输入框与按钮列**同行**，且模式切换仍独占上一行。

    为什么必须单开一条：原 t2 用例 2 只量 `.qa-composer` 这个**外层盒子**的宽/y，
    不量它的内部构成 ⇒ 结构性漏检。pico 有全局规则
      `button[type=submit],input:not([type=checkbox],[type=radio]),select,textarea{width:100%}`
    故 `.qa-composer textarea{flex:1 1 auto}` 的 `flex-basis:auto` 会解析成 **100%**；
    而 flex 换行发生在收缩**之前**（`.qa-composer` 带 `flex-wrap:wrap`）
    ⇒ textarea 独占第 2 行、`.qa-composer-btns` 被整列挤到**第 3 行**。
    此时 `.qa-composer` 自己的几何完全正常（实测折叠后 842 -> 1219 仍成立）⇒ 旧判据全绿。

    判据三条（几何，互相独立）：
      1. 纵向：textarea 与 `.qa-composer-btns` 顶边之差 < 2px（flex-start 下同行即顶边对齐；
         错位时两者相隔一个 textarea 高度 ~79px，判别度极高）；
      2. 横向：textarea **未占满整行**——`textarea 宽 < composer 宽 - 按钮列宽 + 容差`。
         basis 解析成 100% 时 textarea 宽 ≈ composer 宽（等式右侧还差一个按钮列宽 ~75px）
         ⇒ 专门钉住"独占整行"这一形态；
      3. 模式切换独占了输入框**上方**那一行（label 底边 <= textarea 顶边）——它是靠
         `flex-wrap:wrap` + `flex:0 0 100%` 实现的，把它连同 wrap 一起删掉虽能让 1/2 通过，
         却会让 label 退回"textarea 左侧的一列"（设计不允许）⇒ 必须一并锁住。
    """
    page.goto(f"{BASE}/qa")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.wait_for_selector(".qa-composer", state="visible", timeout=10000)
    composer = page.locator(".qa-composer").bounding_box()
    ta = page.locator(".qa-composer textarea").bounding_box()
    btns = page.locator(".qa-composer-btns").bounding_box()
    mode = page.locator(".qa-mode-switch").bounding_box()
    assert all((composer, ta, btns, mode)), "输入区四块几何量测不全"

    # 判据 1：同行
    dy = abs(ta["y"] - btns["y"])
    assert dy < 2, (f"输入框与按钮列不在同一行（顶边相差 {dy:.2f}px）："
                    f"textarea y={ta['y']:.2f}, 按钮列 y={btns['y']:.2f}"
                    "——flex-basis 被 pico 的 width:100% 撑成整行、按钮被换到下一行")

    # 判据 2：textarea 不独占整行
    tol = 4.0
    assert ta["width"] < composer["width"] - btns["width"] + tol, (
        f"输入框占满了整行（textarea 宽 {ta['width']:.2f}，"
        f"composer 宽 {composer['width']:.2f}，按钮列宽 {btns['width']:.2f}，容差 {tol}）"
        "——按钮列没有与它分享同一行")

    # 判据 3：模式切换独占上一行
    assert mode["y"] + mode["height"] <= ta["y"] + 1, (
        f"模式切换未独占输入框上方一行（label 底边 {mode['y'] + mode['height']:.2f} "
        f"> textarea 顶边 {ta['y']:.2f}）")


def t2_mode_checkbox_survives_narrow_viewport(page):
    """边界场景：窄视口下模式切换的勾选框不被 flex-shrink 压扁。

    已有的第一道防护是 label 内 checkbox 的 inline `width:1rem;height:1rem`，
    它只挡「`appearance:none` + `width:auto` 塌缩成 4px」那一类，
    **挡不住 flex-shrink**：`.qa-mode-switch` 是 nowrap flex 行、三个子项都可收缩，
    可用宽度不足时（实测收缩在视口 850→800px 之间开始）勾选框会按其 1rem 基准被等比压小
    ⇒ 「11.8px 症状」经另一条路径复现（实测视口 750px 时恰好 11.766px ≈ 11.8px）。
    修法是 `.qa-mode-switch input{flex:0 0 auto}`。

    判据：窄视口下勾选框宽度 >= 1rem 的 95%，且 >= 14px。
    1rem 的实值**从页面读**（`getComputedStyle(document.documentElement).fontSize`），
    不硬编码 16px——pico 与本项目样式都可能改根字号（实测该根字号是 14.4px）。

    **量两个宽度（800 + 700），不是凑数**：收缩是**渐变**的（收缩量按各子项基准宽度占比分摊），
    实测（去掉 `.qa-mode-switch input{flex:0 0 auto}` 后逐宽度量得，composer 宽随视口线性收缩）：

        vw=900 -> 14.391px   vw=850 -> 14.391px   vw=800 -> 13.547px（收缩刚发生）
        vw=750 -> 11.766px   vw=700 ->  9.984px   vw=650 ->  8.219px

    800 是"收缩刚发生"的临界点，判据裕度只有 ~0.13px——只靠它，未来任何字号/字体度量变化都
    可能让这条守卫**静默失效**（不再收缩 ⇒ 断言恒真）。700 的裕度是 ~3.7px（低于阈值 27%），
    是**决定性**的那一条。GREEN 侧无假红风险：`flex: 0 0 auto` 下勾选框宽度恒等于 1rem
    （与文字宽度无关），实测 900~700 各宽度均为 14.391px。
    """
    try:
        page.goto(f"{BASE}/qa")
        page.wait_for_selector("#qa-root", timeout=10000)
        page.wait_for_selector(".qa-mode-switch", state="visible", timeout=10000)
        # 800 = 控制器指定的临界宽度；700 = 决定性宽度（裕度 ~3.7px）
        for width in (800, 700):
            page.set_viewport_size({"width": width, "height": 900})
            rem = page.evaluate(
                "parseFloat(getComputedStyle(document.documentElement).fontSize)")
            box = page.locator(".qa-mode-switch input[type=checkbox]").bounding_box()
            assert box is not None, f"视口 {width}px 下模式切换的勾选框量不到几何"
            assert box["width"] >= rem * 0.95, (
                f"窄视口({width}px)下勾选框被 flex-shrink 压扁：{box['width']:.3f}px，"
                f"应 >= {rem * 0.95:.3f}px（1rem={rem}px 的 95%）")
            assert box["width"] >= 14, (
                f"窄视口({width}px)下勾选框宽度 {box['width']:.3f}px 低于 14px 下限")
    finally:
        # 用例在 runner 的**同一个 page** 上顺序执行，视口必须还原（本用例排在最后仍还原，防追加）
        page.set_viewport_size({"width": 1600, "height": 900})


def t2_qa_page_not_shown_on_other_pages(page):
    """异常场景（回归）：其它页面不得冒出 QA 界面。

    ⚠️ **本用例是路由隔离哨兵（守 T1 的 `/qa` 整页路由），不构成 T2 交付物的验收证据。**
    理由：它唯一的失败模式要求改动**本 Task 文件清单之外**的东西（例如把 qa_page.html
    并入 base.html、或在检索页也渲染 `#qa-root`）——T2 改动的任何文件都无法让它变红，
    故它对 T2 的交付物**零信息量**。保留它是因为它作为**跨 Task 回归哨兵**有价值
    （防后续 Task 把 QA 界面误挂到其它页面）；不挪进 `CASES["t1"]`，以免打乱已被复核记录的
    t1 计数。
    """
    for path in ("/specs", "/rules", "/lexicon"):
        page.goto(f"{BASE}{path}")
        assert page.locator("#qa-root").count() == 0, f"{path} 不该出现 QA 界面"


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
    # 左栏「仅现行」默认勾选；取消它 ⇒ 两个状态框都不勾 ⇒ 共享 store 的
    # buildStatusFilter() 返回 null（`collectFilters()` 转空串）
    page.locator(".left-panel label:has-text('仅现行') input[type=checkbox]").uncheck()
    # 原写法 `wait_for_timeout(300)`：把「等 store 反映这次取消勾选」这件事从固定 sleep
    # 换成**有界断言**（本文件方法论：等待与判据分离，等待失败即报红、信息明确）。
    # 断言的对象正是本用例的判据所依赖的那个值本身（store 的组合结果 = null）。
    assert poll_until(
        page,
        lambda: page.evaluate(
            "(window.Alpine && Alpine.store('searchState')).buildStatusFilter()") is None,
        timeout_ms=3000), \
        "取消「仅现行」后共享 store 的 buildStatusFilter() 未变为 null（左栏状态未写进 store）"
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
    label = page.locator(".left-panel label:has-text('启用 CE 精排')")
    title = label.get_attribute("title") or ""
    assert title, "CE 精排复选框所在 label 缺少 title 属性（tooltip 未落地）"
    assert "问答" in title or "AI" in title, f"CE 复选框缺少问答相关说明: {title!r}"


def t3_ce_rerank_not_disabled_in_qa_page(page):
    """异常场景（防回退）：QA 页内 CE 复选框不得被置灰。

    历史上的方案是「QA 界面里 CE 置灰」，但那会锁死检索侧的 CE 功能
    （QA 常驻后没有「进入 QA 界面」这个时刻了）。改用 tooltip 说明。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    # ⚠️ 选择器由 `.left-panel input[type=checkbox]` + `nth(1)` 改为**稳定 id**
    # （T3 实现者上报的隐患）：`nth(1)` 依赖复选框的**排列顺序**，将来在 CE 之前
    # 插入任何复选框（例如又一个筛选开关）都会让它**静默失效**——那时 nth(1) 仍能
    # 命中某个复选框、`is_disabled()` 仍为 False ⇒ 用例恒绿却已不再守 CE。
    # T4 已给该复选框加 `id="ce-rerank-toggle"`（tree_panel.html）。
    box = page.locator("#ce-rerank-toggle")
    assert box.count() == 1, "左栏未找到 #ce-rerank-toggle（CE 精排复选框 id 丢失？）"
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
    # 原写法 `wait_for_timeout(800)`：把「等这一轮的 effectiveFiltersText 写入」换成有界断言
    # （`.qa-effective-filters` 的 x-show 直接绑它 ⇒ 可见即「已写入且非空」，是该条件的忠实代理）。
    assert poll_until(page, lambda: page.locator(".qa-effective-filters").is_visible(),
                      timeout_ms=5000), \
        "输入框上方未显示本轮生效筛选（effectiveFiltersText 未写入）"
    assert page.locator(".qa-filters-pending").is_hidden(), \
        "尚未改动筛选时不应出现「将在下一轮生效」提示"
    page.locator(".left-panel label:has-text('仅现行') input[type=checkbox]").uncheck()
    # 同上：等待条件 = 「提示真的出现」（判据本身），有界且失败信息明确
    assert poll_until(page, lambda: page.locator(".qa-filters-pending").is_visible(),
                      timeout_ms=3000), \
        "改了筛选却没提示「将在下一轮生效」——describeFilters/effectiveFiltersText 未能比对（静默失效）"


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t4）。
CASES = {"t1": [t1_qa_entry_is_a_page_link,
                t1_left_panel_present_on_qa_page,
                t1_filters_carry_into_qa_via_url,
                t1_qa_page_filters_survive_reload,
                t1_search_page_unaffected,
                t1_tree_click_in_qa_page_does_not_navigate,
                t1_tree_click_in_search_page_still_searches,
                t1_qa_enter_search_returns_to_search_page],
         # ⚠️ 窄视口用例**排在最后**：它会改 page 的视口（虽在 finally 里还原），
         #    放最后可保证它失败时不吞掉其它用例的 PASS 行。
         "t2": [t2_layout_structure_present,
                t2_composer_input_shares_row_with_buttons,
                t2_sessions_panel_collapses_without_moving_composer,
                t2_qa_page_not_shown_on_other_pages,
                t2_mode_checkbox_survives_narrow_viewport],
         # T3：筛选统一（QA 读共享 store）+ CE tooltip。顺序 = 「守卫/静态契约 → 真正的判据」，
         # 最后一条会真发一次 /qa/ask（含真实模型调用），放最后以便前面的失败不吞掉它的输出。
         "t3": [t3_qa_page_has_no_duplicate_status_checkboxes,
                t3_ce_rerank_has_tooltip,
                t3_ce_rerank_not_disabled_in_qa_page,
                t3_left_panel_status_unchecked_sends_empty_status_filter,
                t3_pending_filter_hint_appears_after_change]}


# ── T4：会话列表、切换载入与续聊 ─────────────────────────────────────────────
# 本组全部用例都要「AI 真的回答」（会话列表/续聊都以成功轮次落库为前提）
# ⇒ 必须按计划「前置 C」配 mock LLM（见文件头与本 Task 简报顶部指针）。
# 等待一律「有界轮询 + 内容判据」，无固定 sleep（本文件方法论，见 poll_until 注释）。

# mock LLM 的**确定性标记**：`%TEMP%/qa_mock_llm.py` 的固定回答含《GB 50204》。
# 判据用它把「后端真的回答了」与「前端渲染出了一个非空气泡」区分开（见 _qa_ask）。
MOCK_ANSWER_MARKER = "GB 50204"

# `send()` 失败兜底的字面量（qa.js 的 catch 分支）。**判据必须能识别它**：
# 该消息的 content/html 都是这个**非空**固定串 ⇒ 只看"气泡文本非空"时，
# 「被后端 4xx 拒绝的一轮」与「成功的一轮」在 DOM 上完全无法区分（本组曾有的假绿）。
# R1 补：该气泡在「兜底也失败」时已连同用户消息一起被**回滚**（qa.js `_fallbackAsk` 的
# catch）⇒ 该路径上它不再进入 DOM，判据退化为兜底防护（仍是免费的一道）。保留常量。
FAIL_ANSWER_TEXT = "请求失败"


def _qa_open(page):
    """进 QA 页并等 Alpine 初始化完成。

    `.qa-sessions` 由 x-cloak + x-show 控制（x-cloak 在 Alpine 启动该组件时摘除），
    故「它可见」等价于「组件已初始化且 x-show 已求值」——与 T2 折叠用例同一判据。
    """
    page.goto(f"{BASE}/")
    page.click("text=🤖 AI问答")
    page.wait_for_selector("#qa-root", timeout=10000)
    page.wait_for_selector(".qa-sessions", state="visible", timeout=10000)


def _qa_round_done(page, expect_msgs, timeout_ms=90000):
    """**这一轮是否已收尾**（有界轮询，返回 bool；四条判据见 `_qa_ask` 的 docstring）。

    从 `_qa_ask` 抽出（R1）：`relax` 那条用例也要「先等这一轮真的结束」再判会话集合——
    在那里等不到收尾，会话判据根本无从判起（旧写法正是拿 1.5s 观察窗冒充"等结束"）。
    抽成**单一来源**，避免两处各写一份判据而漂移。
    """
    # 在页面内一次性判定，避免 Python 侧多次取节点时遭遇 render 中途的失效句柄
    # （解构数组形参：Playwright 只把 arg 当作**单个**实参传给 JS 函数）
    probe = """([n, failText, marker]) => {
        const msgs = document.querySelectorAll('.qa-msg');
        if (msgs.length < n) return false;
        const ans = document.querySelectorAll('.qa-bot .qa-answer');
        if (!ans.length) return false;
        const last = ans[ans.length - 1];
        if (last.classList.contains('streaming')) return false;   // 还在流式 ⇒ 本轮未收尾
        const t = (last.textContent || '').trim();
        if (t === '') return false;
        if (t.indexOf(failText) !== -1) return false;
        return t.indexOf(marker) !== -1;
    }"""
    return poll_until(
        page,
        lambda: page.evaluate(probe, [expect_msgs, FAIL_ANSWER_TEXT, MOCK_ANSWER_MARKER]),
        timeout_ms=timeout_ms)


def _qa_ask(page, question, expect_msgs=2, timeout_ms=90000):
    """发送一个问题，等**回答真的渲染出来**。

    判据四条（缺一不可，实现见 `_qa_round_done`）：
      · `.qa-msg` 条数达标 —— 等这一轮收尾（用户消息 + 助手消息都入列）；
      · 最后一条 `.qa-bot .qa-answer` **不带 `.streaming`** —— 流式改造后助手气泡是
        **乐观占位**的：它一出现就有内容（首个 delta 起），此后还要吐十几帧。若只等
        「正文非空」，本助手会在**流中途**返回 ⇒ 依赖 `done` 帧写入的字段
        （currentSessionId / filtersText / effectiveFiltersText）全都没就绪，
        T4 的会话与筛选用例会成片假红（**不是**产品缺陷，是判据没跟上流式改造）。
      · 该正文**不得含失败兜底字面量**，且**必须含 mock 的确定性标记** ——
        `send()` 在 `!resp.ok`/网络异常时走兜底，助手消息 content/html 都是
        **非空**固定串『请求失败，请稍后重试』⇒「非空」这一条对「HTTP 层失败但 UI 看不出」
        零判别力（第二次提问被后端 422 拒绝时，条数达标 + 气泡非空 + 不新建会话 + 标题不变
        四条判据**全绿**，而追问根本没落到 DB）。补上这两条后，**任何**用到本助手的用例
        都不会再把失败轮判成成功（哪怕某个用例忘了用真实状态断言）。
    """
    page.fill(".qa-composer textarea", question)
    page.press(".qa-composer textarea", "Enter")
    assert _qa_round_done(page, expect_msgs, timeout_ms), \
        (f"提问「{question}」后未等到渲染完成的回答（当前 .qa-msg="
         f"{page.locator('.qa-msg').count()}，期望 >= {expect_msgs}；"
         f"最后一条答案正文={_last_answer_text(page)!r}）"
         f"——空串=「空气泡」，含『{FAIL_ANSWER_TEXT}』=后端拒绝/网络失败，"
         f"不含『{MOCK_ANSWER_MARKER}』=渲染出来的不是 mock 的回答")


def _last_answer_text(page):
    """最后一条助手气泡的可见正文（仅用于失败信息，让红的原因可判）。"""
    return page.evaluate(
        "() => { const a = document.querySelectorAll('.qa-bot .qa-answer');"
        " return a.length ? (a[a.length - 1].textContent || '').trim() : ''; }")


def _qa_state(page):
    """页面内读组件状态：会话栏**渲染所依据**的 id 列表 + 当前会话 id。

    为什么读组件状态而不是 DOM 文本：`.qa-session-item` 上不带 id 属性，而**标题子串**
    判据在「本轮之前跑过同标题的提问」时，即便 `send()` 完全不刷新列表也成立
    （探针库的会话会跨用例、跨轮次累积）⇒ 结构性不可证伪。id 单调递增，比 id 才有判别力。
    """
    return page.evaluate(
        "() => { const d = window.Alpine.$data(document.querySelector('#qa-root'));"
        " return { ids: (d.sessions || []).map(s => s.id), current: d.currentSessionId }; }")


def _force_session_list(page):
    """**强制**拉一次会话列表并**等它落地**（`page.evaluate` 会 await 返回的 Promise）。

    为什么需要（裁决 R1-5）：用 `_qa_state()['ids']` 判「会话 id 集合未变」时，两侧取样都
    必须是**权威列表**。`send()` 的 finally 里 `await this.loadSessions()` 排在
    `bot.streaming = false` **之后**，而"本轮收尾"的判据只等到前者 ⇒ 若不做这一步，
    取样可能落在列表刷新**之前**——变异世界（本轮真的新建了会话）里新 id 还没入列，
    集合相等照样成立 ⇒ 判据**假绿**（与旧写法「1.5s 观察窗」是同一类错误：
    判据与它要判定的事件之间没有因果关系）。
    走的是组件自己那条 `loadSessions()`（与 send() 同一路径），不新增取数通道。
    """
    page.evaluate("""async () => {
        const d = window.Alpine.$data(document.querySelector('#qa-root'));
        await d.loadSessions();
        return true;
    }""")


def _current_session_state(page):
    """取当前会话 id 与其在**后端**的消息条数（页面内一次 round trip）。

    这是「追问是否真的追加到该会话」唯一落到真实状态的判据：`.qa-msg` 计数、
    气泡文本、会话条数、标题**全部来自前端内存**，`send()` 的失败兜底同样会推入一条
    非空的助手气泡 ⇒ 只看 DOM 无法证伪"追加"这件事本身。`fetch` 与页面同源，
    复用登录态的 cookie，无需再走一遍鉴权。
    """
    return page.evaluate("""async () => {
        const d = window.Alpine.$data(document.querySelector('#qa-root'));
        const sid = d.currentSessionId;
        if (!sid) return { sid: null, count: -1 };
        const r = await fetch('/qa/sessions/' + sid);
        if (!r.ok) return { sid, count: -1 };
        const data = await r.json();
        return { sid, count: (data.messages || []).length };
    }""")


def _route_session_id(url):
    """从 `/qa/sessions/{id}` 或 `/qa/sessions/{id}/export` 里取出会话 id（取不到 → None）。"""
    m = re.search(r"/qa/sessions/(\d+)(?:/|$)", url)
    return int(m.group(1)) if m else None


def _active_session_title(page):
    """当前「激活」会话的标题（空串 = 无激活项，即草稿态）。"""
    titles = page.locator(".qa-session-item.active .qa-session-title").all_inner_texts()
    return titles[0].strip() if titles else ""


def t4_ask_creates_session_and_lists_it(page):
    """正常场景：提问后会话出现在列表，且标题为首轮问题截断。

    ⚠️ 列表判据必须**比对 id**，不能用标题子串（旧写法 `any(q[:20] in t for t in titles)`）：
    探针库的会话跨用例、跨轮次累积，上一轮跑过同标题的提问时，「列表里存在含该标题的项」
    在 `send()` **完全不刷新列表**的世界里**也成立** ⇒ 子串判据结构性不可证伪
    （而且后端列表无 LIMIT，全量返回 ⇒ 越攒越容易误判绿）。
    """
    _qa_open(page)
    q = "混凝土强度等级如何评定"
    max_before = max(_qa_state(page)["ids"], default=0)   # 空列表 → 0（会话 id 从 1 起）
    _qa_ask(page, q)
    # 答案必须真的过了渲染管线：mock 的固定回答含 `**混凝土强度等级**`，
    # 渲染出来必有 <strong>（钉住 send() 写 html + renderMarkdown 这条链）
    assert page.evaluate(
        "() => document.querySelectorAll('.qa-bot .qa-answer strong').length") >= 1, \
        "回答未渲染出 Markdown 加粗（send() 未写 msg.html，或渲染管线未跑）"
    # 判据 ①：当前会话 id 必须**前进**到提问前最大 id 之上 ⇒ 确实是一个**新**会话
    new_id = _qa_state(page)["current"]
    assert new_id is not None, "提问后 currentSessionId 仍为空（会话未落库）"
    assert new_id > max_before, \
        (f"提问后未新建会话：提问前列表里最大 id={max_before}，当前会话 id={new_id}"
         f"（当前 ids={_qa_state(page)['ids']}）")
    # 判据 ②：该新 id 必须出现在**会话栏列表**里 ⇒ send() 真的刷新了列表。
    # 只断 ① 不够：currentSessionId 来自响应体，不经过列表。
    assert poll_until(page, lambda: new_id in _qa_state(page)["ids"], timeout_ms=5000), \
        (f"新会话 id={new_id} 未出现在会话栏（send() 未刷新会话列表）："
         f"ids={_qa_state(page)['ids']}")
    # 判据 ③：新会话必须成为「当前会话」，且标题 = 首轮问题（否则续聊无从谈起）
    assert _active_session_title(page) == q, \
        f"新建的会话未被标记为当前会话或标题不符: {_active_session_title(page)!r}"


def t4_history_shows_full_conversation(page):
    """正常场景：点历史会话 → 中栏载入该会话全部消息。"""
    _qa_open(page)
    _qa_ask(page, "第一个问题")
    assert poll_until(page, lambda: page.locator(".qa-session-item").count() >= 1,
                      timeout_ms=5000), \
        "首轮提问后会话列表仍为空（send() 未刷新会话列表）"
    page.click("text=＋ 新会话")          # 清空到草稿态
    assert poll_until(page, lambda: page.locator(".qa-msg").count() == 0, timeout_ms=3000), \
        f"新会话应清空对话区（当前 {page.locator('.qa-msg').count()} 条）"
    # 清空后当前会话必须回到草稿态（无激活项）——否则下面的点击不是「从零载入」
    assert _active_session_title(page) == "", "点新会话后仍停留在他会话（未回到草稿态）"
    page.click(".qa-session-item >> nth=0")   # 点回历史会话
    assert poll_until(page, lambda: page.locator(".qa-msg").count() >= 2, timeout_ms=10000), \
        f"载入历史后应出现问答两条（当前 {page.locator('.qa-msg').count()} 条）"
    # 内容精确性：载入的必须是**那次**提问（防「载入了别的会话」也判绿）
    assert page.locator(".qa-msg.qa-user").first.inner_text().strip() == "第一个问题", \
        f"载入的历史会话内容与提问不一致: {page.locator('.qa-msg.qa-user').first.inner_text()!r}"


def t4_mode_switch_reaches_request_body(page):
    """正常场景（§4.8「必须保留的既有功能」）：模式切换必须真的进入请求体。

    为什么必须有这条：模式切换控件在本计划首版的新模板里**被漏掉了**，而当时**没有任何探针会报警**
    （完成标准却写着"模式切换正常"）⇒ 功能静默消失。本探针把「控件存在 + 切换生效 + 值到达请求体」
    三件事一次性钉住：删控件 → `page.check` 找不到元素即红；`toggleMode` 不写 mode / `buildRequestBody`
    不带 mode → 断言红。T4 重写了 `send()`（改走 `buildRequestBody()`），这条同时是那次重写的回归判据。
    """
    _qa_open(page)

    captured = {}

    def _on_request(req):
        if req.method == "POST" and req.url.endswith("/qa/ask"):
            captured["body"] = req.post_data_json

    page.on("request", _on_request)
    page.check(".qa-mode-switch input[type=checkbox]")     # 切到「原文摘抄」
    assert page.locator(".qa-mode-switch input[type=checkbox]").is_checked(), \
        "模式切换的勾选框未被勾上（toggleMode / :checked 绑定坏）"
    _qa_ask(page, "混凝土强度等级如何评定")

    assert captured.get("body", {}).get("mode") == "verbatim", \
        f"模式切换未进入请求体（mode 应为 verbatim）: {captured.get('body')}"


def t4_continue_in_history_session_appends(page):
    """异常场景（核心）：在历史会话里继续提问，追加到同一会话而非新建。

    四条判据（互相独立，避免单判据假绿）：
      ① 追问前**确有**当前会话（标题非空）—— 否则 ②③ 都可能在"两边都空"上恒真；
      ② 会话条数**不得增加**（有界观察窗：一旦新建立即判红）；
      ③ 追问后当前会话仍是**同一标题**（钉「追加到该会话」而不只是「没多开一个」）；
      ④ **后端真实状态**：该会话的消息数真的从 2 涨到 >= 4。

    ⚠️ ④ 是本组**唯一**能证伪「追加」这件事本身的判据：②③ 与 `.qa-msg` 计数、
    气泡文本**全部来自前端内存**，而 `send()` 在 `!resp.ok`/网络异常时走 catch，
    推入的助手消息 content/html 是**非空**固定串『请求失败，请稍后重试』
    ⇒ 第二次提问被后端 4xx 拒绝（如字段被 QaRequest 拒 → 422）时，
    「条数达标 / 未新建会话 / 标题不变」三条**全绿**，而 DB 里该会话仍只有 2 条消息。
    """
    _qa_open(page)
    _qa_ask(page, "第一个问题")
    assert poll_until(page, lambda: page.locator(".qa-session-item").count() >= 1,
                      timeout_ms=5000), "首轮提问后会话列表仍为空（send() 未刷新会话列表）"
    before_title = _active_session_title(page)
    assert before_title == "第一个问题", \
        f"首轮提问后未定位到当前会话（应为『第一个问题』，实得 {before_title!r}）"
    n_before = page.locator(".qa-session-item").count()
    state_before = _current_session_state(page)
    assert state_before["sid"] and state_before["count"] == 2, \
        (f"首轮提问后该会话在后端应有 2 条消息，实得 {state_before}"
         "——前置条件不成立，判据 ④ 无从判起")

    _qa_ask(page, "继续追问", expect_msgs=4)

    # 判据 ②：负断言的有界观察窗（等待与判据分离；违反即提前判红，不是"睡够就算过"）
    grew = poll_until(
        page, lambda: page.locator(".qa-session-item").count() != n_before, timeout_ms=1500)
    n_after = page.locator(".qa-session-item").count()
    assert not grew, f"在历史会话中追问不应新建会话（追问前 {n_before} 条，追问后 {n_after} 条）"
    assert page.locator(".qa-msg").count() >= 4, \
        f"追问后应有四条消息（当前 {page.locator('.qa-msg').count()} 条）"
    # 判据 ③：仍在同一个会话里（只"没多开"不等于"追问落到了原会话"）
    assert _active_session_title(page) == before_title, \
        (f"追问后当前会话变了：{before_title!r} -> {_active_session_title(page)!r}"
         "（追问应追加到原会话，session_id 未随请求携带？）")

    # 判据 ④：**落到后端真实状态**——该会话真的多了两条消息。
    after = {"sid": None, "count": -1}

    def _appended():
        after.update(_current_session_state(page))
        return after["count"] >= 4

    assert poll_until(page, _appended, timeout_ms=5000), \
        (f"追问未真的追加到原会话：会话 {after['sid']} 在后端只有 {after['count']} 条消息"
         f"（追问前 {state_before['count']} 条，应 >= 4）"
         "—— 前端气泡非空**不等于**后端落了库：后端拒绝/网络失败同样会推入一条非空的"
         f"『{FAIL_ANSWER_TEXT}』气泡，且不新建会话、不改标题")
    assert after["sid"] == state_before["sid"], \
        (f"追问后当前会话 id 变了：{state_before['sid']} -> {after['sid']}"
         "—— 判据 ④ 查的必须是**原会话**的消息数")


def t4_filters_recorded_and_shown(page):
    """正常场景（D5）：答案下方显示当轮筛选，输入框上方显示本轮生效筛选，
    且**载入历史会话后答案下方的筛选仍在**（这才真正走落库→回填那条路径）。

    ⚠️ 只断言「当前这条答案下有 `.qa-msg-filters`」是不够的：那来自本轮内存里的消息对象，
    即便 `filters_json` **完全没落库**、`GET /qa/sessions/{id}` 的 `filters` 恒为空，
    它也照样通过 ⇒ 恰漏掉本用例声称覆盖的 D5 落库路径。故末尾补一段
    「新会话 → 点回历史会话 → 再断言」，把回填路径钉死。
    """
    _qa_open(page)
    # ⚠️ 顺序是「先点、再等 active」——简报给的是反的（`wait_tree_filter_seeded(); click_...`）：
    # 全新页面里**一个筛选都没选**，那一行会直接等 10s 超时判红（实测：
    # Page.wait_for_selector: Timeout 10000ms exceeded —— waiting for ".tree-label.active"）。
    # 正确顺序与 T1/T2 既有用例一致（click_first_tree_label 的 docstring 即为此而写）。
    click_first_tree_label(page)                                          # 勾一个分类树筛选
    wait_tree_filter_seeded(page)         # 等 store 记下这次选择（active 由绑定同一 store 字段的 :class 控制）
    _qa_ask(page, "混凝土强度等级如何评定")
    assert poll_until(page, lambda: page.locator(".qa-msg-filters").count() >= 1,
                      timeout_ms=5000), \
        "答案下方未显示当轮筛选（D5 落库/展示未生效）"
    assert page.locator(".qa-effective-filters").count() == 1, \
        "输入框上方未显示本轮生效筛选"
    # `.qa-effective-filters` 的**计数**是结构性判据（x-show 只改 display、节点恒在）
    # ⇒ 必须再断言其文本非空，否则「本轮生效」可以是空白一条
    assert page.evaluate(
        "() => (document.querySelector('.qa-effective-filters').textContent || '').trim()") != "", \
        "输入框上方的「本轮生效筛选」是空文本"

    # ── 落库回填路径（D5 的真正判据）──
    # 清空到草稿态再点回该历史会话：消息改为从 GET /qa/sessions/{id} 重新映射，
    # 此处的 `.qa-msg-filters` 只能来自 `qa_messages.filters_json`
    page.click("text=＋ 新会话")
    assert poll_until(page, lambda: page.locator(".qa-msg").count() == 0, timeout_ms=3000), \
        "新会话应清空对话区"
    page.click(".qa-session-item >> nth=0")
    assert poll_until(page, lambda: page.locator(".qa-msg-filters").count() >= 1,
                      timeout_ms=10000), \
        "载入历史会话后答案下方的当轮筛选消失——filters 未随消息落库或未回填（D5 路径坏）"


def t4_pending_filter_change_is_visible(page):
    """异常场景（设计文档场景 2 的可见性）：改动筛选后提示将在下一轮生效。

    没有这条提示时，用户在回复中/回复后切换筛选会以为立即生效——静默失配。
    """
    _qa_open(page)
    _qa_ask(page, "混凝土强度等级如何评定")
    assert poll_until(
        page,
        lambda: page.evaluate(
            "() => (document.querySelector('.qa-effective-filters').textContent || '').trim()") != "",
        timeout_ms=5000), \
        "输入框上方未显示本轮生效筛选（effectiveFiltersText 未写入）"
    assert page.locator(".qa-filters-pending").is_hidden(), \
        "尚未改动筛选时不应出现「将在下一轮生效」提示"
    # 顺序同前一条用例：先点、再等（简报给的反了，理由见 t4_filters_recorded_and_shown）
    click_first_tree_label(page)                                          # 改动筛选
    wait_tree_filter_seeded(page)         # 等 store 记下这次改动
    assert poll_until(page, lambda: page.locator(".qa-filters-pending").is_visible(),
                      timeout_ms=3000), \
        "改了筛选但未提示「将在下一轮生效」——静默失效复现"


def t4_new_session_resets_session_scoped_display(page):
    """边界场景（补裁决 R1-3）：点「＋ 新会话」必须复位**会话级显示状态**。

    `newSession()` 原先清了 `rerankUsed` 却留着上一会话的 `effectiveFiltersText`
    ⇒ 草稿态里输入框上方仍挂着上一条（很可能已不成立的）「本轮生效：…」，
    且 `filtersChanged()` 会拿这个**过时基线**比对当前选中
    ⇒「已修改，将在下一轮生效」提示在新会话里**虚假出现**（纯展示层，无后端风险）。

    判据两条，都落在**用户可见**的形态上：
      ① 点「＋ 新会话」后 `.qa-effective-filters` 那行**消失**——`x-show` 绑的正是该字段，
         空串即整行隐藏，是「该字段已被复位」的忠实代理（同时直接查组件字段，
         失败信息更精确）；
      ② 草稿态（尚未提问）里改了筛选，**也不该**冒出「将在下一轮生效」——新会话尚无
         "本轮生效"基线，拿上一会话的残留基线比对出来的提示是假的
         （有界观察窗：一旦出现即提前判红，不是"睡够就算过"）。
    """
    _qa_open(page)
    _qa_ask(page, "混凝土强度等级如何评定")            # 首轮：写入 effectiveFiltersText
    assert poll_until(page, lambda: page.locator(".qa-effective-filters").is_visible(),
                      timeout_ms=5000), \
        "首轮提问后输入框上方未显示「本轮生效」——前置条件不成立，本条无从判起"
    page.click("text=＋ 新会话")
    assert poll_until(page, lambda: page.locator(".qa-effective-filters").is_hidden(),
                      timeout_ms=3000), \
        ("点「＋ 新会话」后「本轮生效：…」仍在显示——newSession() 未复位 "
         "effectiveFiltersText（残留的过时基线会让 filtersChanged() 误判）")
    assert page.evaluate(
        "() => window.Alpine.$data(document.querySelector('#qa-root')).effectiveFiltersText") == "", \
        "newSession() 未复位 effectiveFiltersText（组件状态里仍残留上一会话的过时基线）"
    # 判据 ②：有界观察窗（负断言用观察窗，本文件惯例）
    click_first_tree_label(page)                       # 草稿态里改动筛选
    wait_tree_filter_seeded(page)
    assert not poll_until(page, lambda: page.locator(".qa-filters-pending").is_visible(),
                          timeout_ms=1500), \
        ("草稿态（尚未提问）改了筛选却冒出「将在下一轮生效」——filtersChanged() 在拿"
         "上一会话残留的过时基线比对（会话级显示状态未随新会话复位）")


def t4_session_ops_reach_the_right_routes(page):
    """正常场景（补简报缺项）：会话的「重命名 / 删除 / 导出」三个入口真的可用。

    ⚠️ 为什么补这条：本 Task 的交付物明写含「rename/delete/export 的入口」，而上述五条
    用例一个都不碰它们 ⇒ 三个按钮即便是纯装饰（`onclick` 为空、指向错路由）也无任何报警。
    「按钮在」与「按钮能用」是两件事，必须分别钉住。

    判据落在**请求本身**（方法 + 路由里的**会话 id** + PATCH 体里的 title），不看列表文本：
    列表刷新是异步的，文本判据要配轮询；而请求判据天然精确、天然无竞态。

    ⚠️ 路由判据必须**比对 id**（旧写法只 `re.search(r"/qa/sessions/\\d+$")` 校验形状）：
    形状判据下，请求打到**任意别的会话**也照样全绿 ⇒ 对"这三个按钮操作的是当前会话"
    零判别力。目标会话 id 取自组件状态 `currentSessionId`（首轮提问后即为该新会话），
    点击也**限定在 `.qa-session-item.active`**（同一判据的 DOM 侧对应物）。
    导出走 `window.location.href` ⇒ 用 `page.expect_download()` 接住后端本就发的
    `Content-Disposition: attachment`（不拦截、不 abort，页面原地不动；见下方注释）。

    ⚠️ 顺序是 rename → export → **delete 最后**：删除会销毁目标会话（并触发 `newSession()`
    把 currentSessionId 置空），放在中间会让后续断言失去"目标会话"这一基准
    （旧写法用 `nth=0` 掩盖了这点：删完 nth=0 已是**另一个**会话，而形状判据照样绿）。
    """
    _qa_open(page)
    _qa_ask(page, "第一个问题")
    assert poll_until(page, lambda: page.locator(".qa-session-item").count() >= 1,
                      timeout_ms=5000), "首轮提问后会话列表仍为空"
    target = _qa_state(page)["current"]
    assert target is not None, "首轮提问后 currentSessionId 为空——拿不到目标会话 id"
    assert page.locator(".qa-session-item.active").count() == 1, \
        "会话栏未唯一标记出当前会话（.qa-session-item.active 计数 != 1）"

    seen = []

    def _on_request(req):
        if "/qa/sessions/" in req.url:
            seen.append({"method": req.method, "url": req.url, "data": req.post_data})

    page.on("request", _on_request)

    # 重命名：window.prompt（prefill 当前标题）→ PATCH /qa/sessions/{id}
    def _on_dialog(d):
        d.accept("改名后的标题" if d.type == "prompt" else None)

    page.on("dialog", _on_dialog)
    page.click(".qa-session-item.active >> .qa-session-ops button[title='重命名']")
    assert poll_until(page, lambda: any(x["method"] == "PATCH" for x in seen),
                      timeout_ms=5000), \
        f"点「重命名」未发出 PATCH /qa/sessions/{{id}}（已见请求 {seen}）"
    patch = next(x for x in seen if x["method"] == "PATCH")
    assert _route_session_id(patch["url"]) == target, \
        (f"重命名打到了**别的会话**：期望 /qa/sessions/{target}，实得 {patch['url']}"
         "—— 只校验路由形状（/qa/sessions/\\d+$）时，指向任意会话都判绿")
    assert json.loads(patch["data"] or "{}").get("title") == "改名后的标题", \
        f"PATCH 请求体未携带新标题: {patch['data']!r}"

    # 导出：window.location.href → GET /qa/sessions/{id}/export
    # ⚠️ **不要**用 `page.route(..., abort)` 去拦这次导航（T4 的原写法）：
    #    abort 让渲染进程落到 `chrome-error://chromewebdata/` —— 整个页面被**销毁**
    #    （实测：Alpine 变 undefined、`#qa-root` 计数 0、`currentSessionId` 丢失）。
    #    原用例把导出放在最后一步，这个副作用被"用例已结束"掩盖了；本轮把导出移到删除之前，
    #    它立刻以"删不掉（找不到 .active）"的**假红**暴露出来。
    #    改用后端本就发出的 `Content-Disposition: attachment`（qa_routes.py 的 export 路由）：
    #    浏览器**下载**而非导航 ⇒ 页面原地不动；判据还多了一层"确实拿到可下载的附件"。
    with page.expect_download() as dl_info:
        page.click(".qa-session-item.active >> .qa-session-ops button[title='导出 Markdown']")
    download_url = dl_info.value.url
    assert _route_session_id(download_url) == target, \
        (f"导出打到了**别的会话**：期望 /qa/sessions/{target}/export，实得 {download_url}"
         "—— 只校验路由形状（/qa/sessions/\\d+/export$）时，指向任意会话都判绿")

    # 删除：window.confirm 二次确认后 → DELETE /qa/sessions/{id}（**最后**执行，见 docstring）
    page.click(".qa-session-item.active >> .qa-session-ops button[title='删除']")
    assert poll_until(page, lambda: any(x["method"] == "DELETE" for x in seen),
                      timeout_ms=5000), \
        f"点「删除」未发出 DELETE /qa/sessions/{{id}}（已见请求 {seen}）"
    dele = next(x for x in seen if x["method"] == "DELETE")
    assert _route_session_id(dele["url"]) == target, \
        f"删除打到了**别的会话**：期望 /qa/sessions/{target}，实得 {dele['url']}"


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t5）。
CASES["t4"] = [t4_ask_creates_session_and_lists_it,
               t4_history_shows_full_conversation,
               t4_mode_switch_reaches_request_body,
               t4_continue_in_history_session_appends,
               t4_filters_recorded_and_shown,
               t4_pending_filter_change_is_visible,
               # 修复轮 R1 补（裁决 3）：newSession() 未复位会话级显示状态（无探针覆盖）。
               t4_new_session_resets_session_scoped_display,
               # 本 Agent 补（简报缺项）：rename/delete/export 入口无任何用例覆盖。
               # 放最后：它会对会话执行 rename/delete（改变列表），且拦了一次导航。
               t4_session_ops_reach_the_right_routes]

# ── T5：流式输出（SSE）+ 流式期间降级渲染 ────────────────────────────────────
# 本组必须配 mock LLM（`%TEMP%/qa_mock_llm.py`，监听 127.0.0.1:8199）：它的流式响应
# **帧间 sleep 0.12s**，从而造出可观测的「生成中」窗口——渐进渲染与 stage 中途态
# 两条判据都依赖这个窗口存在（真模型下窗口长度不可控，判据会漂）。
# 另：副本库需把 `qa.retrieve.qa_min_candidates` 调到 30（见 relax 用例 docstring）。


def t5_streaming_renders_progressively(page):
    """正常场景（渐进渲染）：**中途态必须真的存在**——带 `.streaming` 类且文本短于最终文本。

    ⚠️ 只等「`.qa-answer` 文本非空」是不够的——那把流式换成一次性非流式实现（拿到整段再
    一次性写入）也照样通过，与用例名不符（假绿）。这里钉住三件事：
      ① 生成期间采样到的文本**严格短于**最终文本（真的在长）；
      ② 收尾后 `.streaming` 类消失（降级样式只在生成期间存在）；
      ③ 一次提问**只发一次** `/qa/ask`（R1 补：done/delta 分支抛错不得逃逸成兜底重发）。
    """
    _qa_open(page)
    bodies = _track_ask_bodies(page)           # 必须在提问之前注册（③ 的判据）
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

    # ③ **一次提问 = 一次请求**（R1 补，裁决 3 的判据）：`_handleSseChunk` 的 done/delta
    #    分支内任一处抛错若**逃逸**到 send() 的 catch，就会被当成传输层失败而
    #    `_fallbackAsk` **重发同一个问题**（用户看到两次生成、重复计费、重复落库）——
    #    与 error 分支注释要避免的是同一件事。这里把"一轮只发一次 /qa/ask"钉住：
    #    把 done 分支的加固撤掉并让它抛错，本断言即红（见报告 §变异 C）。
    assert len(bodies) == 1, \
        (f"一次提问发了 {len(bodies)} 次 POST /qa/ask（stream 标志："
         f"{[b.get('stream') for b in bodies]}）—— done/delta 分支内的异常逃逸到了"
         " send() 的 catch，触发了兜底重发")


def t5_streaming_shows_plain_text_not_markdown(page):
    """异常场景（关键约束）：生成期间显示转义纯文本，不解析 Markdown。

    半截 Markdown（未闭合的 ** / 表格 / 公式）会让解析器反复重排，比「纯文本 → 一次性
    排版好」更晃眼。判据：生成期间 `.qa-answer` 带 `.streaming` 类，且内部没有 Markdown
    解析产物。

    ⚠️ 用「发送按钮是否禁用」推断"还在流式"、然后写成条件分支的话，条件不成立就**整段跳过**，
    用例体压根不执行 ⇒ 恒绿，等于没有守卫。故这里**主动等**：`wait_for_function` 断言
    `.streaming` 出现（等不到就红），不做任何条件跳过。
    """
    _qa_open(page)
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


# 徽章文案的**独立镜像**（app 侧的真源是 qa.js 的 `rerankBadge()`）。
# 做「done 帧上报值 → 渲染出来的徽章文本」这段比对时，判据必须是独立来源，
# 否则拿 rerankBadge() 的输出与自己比对是循环论证。
RERANK_BADGE_TEXT = {"crossencoder": "⚡ CE 精排", "vector": "≈ 向量精排", "none": "⚠️ 无精排"}


def t5_rerank_badge_handles_unreported_state(page):
    """异常场景（防谎报）：后端未上报精排级别时不得显示降级标记。

    两条判据（② 是 R1 补的，见各自的说明）：
      ① **值域契约**：`QAResponse.rerank_used` 的缺省是空串——那是**契约外的第四态**
         （未上报），不是 `RERANK_NONE`。若把 `rerankBadge()` 写成 `else → 无精排`，
         会在字段缺失时谎报「系统已降级」。故直接钉住三态 + 未上报态的返回值契约。
      ② **接线**（裁决 R1-5）：① 是纯值域单测——它只调 `rerankBadge()`，把 `done` 分支里
         `this.rerankUsed = data.rerank_used` 那行**删掉它照样绿**（字段恒为未上报态，
         而单测自己设值）。故补一条**行为**判据：走一轮真实提问（mock），把
         **网络响应体里 `done` 帧的 `rerank_used`** 与**渲染出来的徽章文本**直接比对。
         取网络响应体（而不是组件字段）是为了让这条链完整：后端上报值 → 组件字段 → DOM。
    """
    # ① 值域契约
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

    # ② 接线（行为）：fresh 页面 ⇒ 未上报态 ⇒ 提问 ⇒ done 帧驱动徽章
    _qa_open(page)
    badge = page.locator(".qa-rerank-badge")
    assert not badge.is_visible(), \
        (f"提问前徽章不应显示（rerankUsed 初始为未上报态），实得文本 {badge.inner_text()!r}"
         "——前置不成立，② 的比对无从判起")
    responses = _track_ask_responses(page)     # 必须在提问之前注册
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    assert _qa_round_done(page, 2, timeout_ms=90000), \
        "提问后本轮未收尾（无法判定徽章接线）"
    reported = _wire_done_payload(page, responses).get("rerank_used", "")
    assert reported in RERANK_BADGE_TEXT, \
        (f"后端上报的 rerank_used 不在三态值域内：{reported!r}"
         "——（未上报/未知值时前端应不显示标记，但本用例依赖后端如实上报）")
    expected = RERANK_BADGE_TEXT[reported]
    assert poll_until(page, lambda: badge.is_visible(), timeout_ms=3000), \
        (f"done 帧上报 rerank_used={reported!r}，徽章却始终不显示"
         "——该字段没有驱动徽章（done 分支里的赋值被删/徽章未绑 rerankUsed）")
    assert badge.inner_text().strip() == expected, \
        (f"徽章文本与 done 帧上报值不符：done.rerank_used={reported!r} 应显示 {expected!r}，"
         f"实得 {badge.inner_text()!r}（徽章未取自 done 帧的 rerank_used）")


def t5_stage_indicator_reflects_real_stage_events(page):
    """正常场景：阶段提示随 `stage` 事件**变化**，且与后端的两态顺序一致。

    ⚠️ 只断言 `.qa-stage` 文本非空是不够的——`stageText` 的默认值就是「正在检索…」，
    于是**整块 stage 事件处理可以缺失**（不解析 `stage` 帧、不写 `stageText`）而无人报警（假绿）。

    判据 = **观察到的文案序列**（R1 收紧）：
      · 用 MutationObserver 在提问**之前**装上（JS 不补发注册之前的事件，与
        `t5_search_hit_jump_...` 的高亮取样同一手法）⇒ 记录**必然命中**，不依赖轮询采样；
      · 断言序列恰为 `['正在检索…', '生成中…']`：既钉住 `generating` 的落点，也钉住顺序；
      · 序列里**不得**出现「处理中…」——那是映射表之外的 stage 才会走到的兜底文案，
        它出现即意味着后端发了 `{retrieving, generating}` 之外的 stage（契约漂移）。
      取样用 `textContent`（不是 `innerText`）：x-show 折叠时元素不参与渲染，`innerText`
      的取值随渲染状态而变，而此处要比对的正是**文案本身**。

    ⚠️ 旧写法在 `wait_for_function(/生成中/)` 之后又 `assert "生成中" in got`，是**同义反复**
    （上一行已保证该条件），零信息量 ⇒ 已删，改为下面的序列判据。
    """
    _qa_open(page)
    # 观察器必须在提问**之前**安装（它是 `generating` 那次改写的唯一取样点）
    page.evaluate("""() => {
        const el = document.querySelector('.qa-stage');
        const seq = [];
        window.__qaStageSeq = seq;
        const read = () => {
            const t = (el.textContent || '').trim();
            if (t && seq[seq.length - 1] !== t) seq.push(t);
        };
        new MutationObserver(read).observe(el,
            { childList: true, subtree: true, characterData: true });
        read();
        return true;
    }""")
    page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
    page.press(".qa-composer textarea", "Enter")
    # 等「生成中…」真的出现（只在收到 generating 帧后才会出现；等不到即红）
    page.wait_for_function(
        "() => { const s = document.querySelector('.qa-stage');"
        " return s && /生成中/.test(s.textContent || ''); }",
        timeout=15000)
    # 再等观察器把这次改写记下来（MutationObserver 在微任务里跑，早于上面的 rAF 轮询，
    # 这一步只是把"序列已定型"变成显式前置条件，避免与下一行的取样竞态）
    page.wait_for_function(
        "() => (window.__qaStageSeq || []).indexOf('生成中…') !== -1", timeout=15000)
    seq = page.evaluate("() => window.__qaStageSeq")
    assert seq == ["正在检索…", "生成中…"], \
        (f"阶段文案序列应为 ['正在检索…', '生成中…']（后端只有 retrieving → generating 两态），"
         f"实得 {seq!r}——序列里出现 '处理中…' 说明收到了映射表之外的 stage，"
         "缺 '生成中…' 说明生成阶段的 stage 事件未被处理")


def _track_ask_bodies(page):
    """登记 POST /qa/ask 的**请求体**监听器，返回累积列表（必须在点击之前调用）。

    与 `track_search_requests` 同一手法：Playwright 不补发注册之前的事件，
    故列表天然只含「注册之后发出的请求」——用它证伪「请求体带了 relaxed」这件事。
    """
    seen = []

    def _on_request(req):
        if req.method == "POST" and req.url.endswith("/qa/ask"):
            try:
                seen.append(req.post_data_json or {})
            except ValueError:      # 请求体不是 JSON（理论上不会出现）：留一条空记录，不吞掉这次请求
                seen.append({})

    page.on("request", _on_request)
    return seen


def _track_ask_responses(page):
    """登记 POST /qa/ask 的**响应**监听器，返回累积列表（必须在提问之前调用）。

    为什么要读响应体（而不是组件字段）：判「徽章文本来自 `done` 帧的 rerank_used」时，
    组件字段与徽章文本都来自前端内存，两者比对只能证明「字段→DOM」这一段；
    要比对「**后端上报值** → 徽章」，必须拿到网络上真实的那一帧。
    """
    seen = []
    page.on("response", lambda r: seen.append(r) if r.url.endswith("/qa/ask") else None)
    return seen


def _wire_done_payload(page, responses, timeout_ms=5000):
    """取最后一个 /qa/ask 响应的 SSE 体里 `done` 帧的载荷（**网络上的真实取值**）。

    响应体要等流结束才可读，故有界轮询；读不出就**响亮失败**（不静默跳过，
    否则整条判据会退化成恒真）。
    """
    body = ""
    waited = 0
    while True:
        if responses:
            try:
                body = responses[-1].text()
            except PlaywrightError as e:      # 流尚未结束/响应体不可读：等下一轮
                body = f"<response.text() 不可读: {type(e).__name__}>"
        m = re.search(r"event:\s*done\s*\r?\ndata:\s*(\{.*\})", body)
        if m:
            return json.loads(m.group(1))
        assert waited < timeout_ms, \
            (f"未从 /qa/ask 响应体里读到 done 帧（{timeout_ms}ms 内）：{body[:300]!r}"
             "—— 本用例的判据依赖从网络上取 done 的取值")
        page.wait_for_timeout(100)
        waited += 100


def t5_relax_resends_with_relaxed_flag(page):
    """正常场景（补漏）：点「放宽分类筛选」→ 以 `relaxed=true` 重发，且**不新建会话**。

    前置（本 Task 简报给的低成本做法）：副本库把 `qa.retrieve.qa_min_candidates`
      （可配参数 `qa.retrieve.qa_min_candidates`，默认 3、范围 1~30）调到 **30**，
      再勾任一分类维度 ⇒ 分类筛选后的候选必然 < 30 ⇒ 后端如实报告 `filtered_out>0`
      ⇒ 前端出现「候选不足 + 放宽分类筛选」提示。不必依赖某条问题恰好筛空。

    两条判据：
      ① 点放宽后**新发起**的 POST /qa/ask，请求体 `relaxed === true`
         （用请求体监听器判定——只看"又出现了气泡"无法区分「带 relaxed 重发」与
          「点了个寂寞但列表被别的路径刷新了」）；
      ② **会话 id 集合不变**：放宽是**同一会话里的一次新轮次**，不是新开会话。

    ⚠️ ② 的旧写法（`timeout_ms=1500` 的观察窗 + `.qa-session-item` 的 **DOM 计数**）
    是**不可证伪**的（裁决 R1-5），两处错叠加：
      · 窗口太短：本轮**最短耗时** = 检索 ~1s + mock 流 ≥1.4s，之后才 `loadSessions`
        ⇒ 观察窗结束时列表**必然**还没刷新 ⇒ 即使本轮真新建了会话，断言也红不了
        （判据与它声明的原因之间没有因果关系）；
      · DOM 计数与本文件自己的教义相悖（`.qa-session-item` 计数结构性不可证伪，
        应按 `_qa_state()['ids']` 判）。
    现在改为：先 `poll_until` 等这一轮**真的收尾**（复用 `_qa_round_done`），再**强制刷新**
    会话列表后比对 id 集合（`_force_session_list` 的注释解释了为何必须强制刷新）。
    """
    _qa_open(page)
    # ⚠️ 前置**必须由本用例自己建立**（控制器用全新副本库复跑时暴露：旧版把它当成"库里已设好"，
    # 于是只在"上一轮跑过的库"里通过 ⇒ 典型的只在我机器上能过）。默认 3、范围 1~30，
    # 调到 30 后勾任一维度 ⇒ 筛选后候选必然 < 30 ⇒ 后端如实报告 filtered_out>0。
    _PARAM = "qa.retrieve.qa_min_candidates"
    origin = _verify_settings_restored(page, [_PARAM])
    try:
        assert _put_settings(page, {_PARAM: 30}), f"无法写入 {_PARAM}=30（前置建立失败）"
        click_first_tree_label(page)               # 勾一个分类维度 ⇒ 触发候选不足判定
        wait_tree_filter_seeded(page)
        _qa_ask(page, "混凝土强度等级如何评定")

        assert poll_until(page, lambda: page.locator(".qa-relax-hint").count() >= 1,
                          timeout_ms=8000), \
            ("分类筛选下未出现「候选不足」提示（后端 filtered_out>0 未上报，"
             f"或 {_PARAM} 未生效）——本用例的前置不成立")

        # 基线：先强制刷新一次，保证两侧比对用的都是**权威列表**（不是"可能还没刷新的内存态"）
        _force_session_list(page)
        state_before = _qa_state(page)
        n_msgs_before = page.locator(".qa-msg").count()
        bodies = _track_ask_bodies(page)           # 必须在点击之前注册
        # 取**最后一条**放宽提示：库里的旧会话也可能带提示，取第一条会点到别的轮次上
        page.locator(".qa-relax-hint button").last.click()

        assert poll_until(page, lambda: len(bodies) >= 1, timeout_ms=15000), \
            "点「放宽分类筛选」后未发出 POST /qa/ask（relax() 未复用 send()）"
        assert bodies[-1].get("relaxed") is True, \
            f"放宽重发的请求体未带 relaxed=true：{bodies[-1]}"
        # ② 前置：先等这一轮**真的收尾**（否则下面的取样落在本轮完成之前，判据无从判起）
        assert _qa_round_done(page, n_msgs_before + 2, timeout_ms=90000), \
            (f"放宽重发后本轮未收尾（.qa-msg={page.locator('.qa-msg').count()}，"
             f"期望 >= {n_msgs_before + 2}；最后一条答案="
             f"{_last_answer_text(page)!r}）——判据 ② 无从判起")
        _force_session_list(page)
        state_after = _qa_state(page)
        assert set(state_after["ids"]) == set(state_before["ids"]), \
            (f"放宽重发新建了会话（应为同一会话里的新轮次）：放宽前 ids={state_before['ids']}，"
             f"放宽后 ids={state_after['ids']}")
        assert state_after["current"] == state_before["current"], \
            (f"放宽重发的当前会话变了：{state_before['current']} -> {state_after['current']}"
             "（应为同一会话里的新轮次，session_id 必须随请求携带）")
    finally:
        # 还原 + 读回复验（不依赖页面状态的通道，见 `_restore_settings`）
        _restore_settings(page, origin)
        back = _verify_settings_restored(page, [_PARAM])
        assert str(back.get(_PARAM)) == str(origin.get(_PARAM)), \
            f"{_PARAM} 未还原：{origin.get(_PARAM)!r} -> {back.get(_PARAM)!r}"


def t5_search_hit_jump_loads_session_and_highlights(page):
    """正常场景（补漏）：跨会话搜索 → 点命中 → 载入**该**会话并高亮命中消息。

    高亮本体的判据是**计算样式**（`getComputedStyle(el).backgroundColor`）而不是
    `classList.contains('qa-highlight')`：后者在 `app.css` 的 `.qa-highlight` 规则
    **被删掉**时依然为真（视觉 no-op 却判绿）⇒ 锁不住「补了这条样式」这个交付。

    样式判据写成「同一元素**有/无**该类的底色必须不同」而非「底色非透明」：
    `.qa-bot` 本身就有不透明底色（`--pico-card-sectioning-background-color`），
    「非透明」在规则缺失时**照样成立**，是个假绿判据（本 Task 简报此处建议的写法不可用）。

    取样用 MutationObserver（在点击前安装）而非轮询：`jumpToHit` 只保留 2 秒高亮，
    轮询有采样落空的风险；观察器在类变化时同步触发，取样是必然命中的。
    """
    _qa_open(page)
    first = _qa_ask_and_remember(page, "混凝土强度等级如何评定")
    # 再造一个**不同**会话：命中必须能把它载回来（若命中来自当前会话，"载入了"无可证伪）
    page.click("text=＋ 新会话")
    assert poll_until(page, lambda: page.locator(".qa-msg").count() == 0, timeout_ms=3000), \
        "点「新会话」后对话区未清空"
    second = _qa_ask_and_remember(page, "混凝土强度等级如何评定")
    assert second != first, f"两次提问落在了同一个会话（{first}）——前置不成立"

    # 搜索 mock 回答里的确定性标记 ⇒ 命中必然来自助手消息（其会话里一定有前置的提问）
    page.fill(".qa-sessions-search input[type=search]", MOCK_ANSWER_MARKER)
    page.press(".qa-sessions-search input[type=search]", "Enter")
    assert poll_until(page, lambda: page.locator(".qa-hit").count() >= 1, timeout_ms=10000), \
        f"搜索「{MOCK_ANSWER_MARKER}」无命中（/qa/search 未返回，或命中列表未渲染）"

    hits = page.evaluate(
        "() => (window.Alpine.$data(document.querySelector('#qa-root')).searchHits || [])"
        ".map(h => h.session_id)")
    idx = next((i for i, sid in enumerate(hits) if sid != second), None)
    assert idx is not None, \
        f"搜索命中全部来自当前会话 {second}（hits={hits}）——换一个会话的命中才能证伪「载入」"
    target = hits[idx]

    # 观察器必须在点击**之前**安装（Playwright/JS 都不补发注册之前的事件）
    page.evaluate("""() => {
        const read = (el) => getComputedStyle(el).backgroundColor;
        const rec = { seen: false, bg: '', base: '' };
        window.__qaHl = rec;
        const check = () => {
            if (rec.seen) return;
            const el = document.querySelector('.qa-highlight');
            if (!el) return;
            rec.seen = true;
            rec.bg = read(el);                  // 高亮态底色
            // 同一元素「无高亮」时的真实底色：同步摘类 → 读 → 还原
            //（getComputedStyle 强制重算，故这一次读到的就是级联里少了该类的值）
            el.classList.remove('qa-highlight');
            rec.base = read(el);
            el.classList.add('qa-highlight');
        };
        new MutationObserver(check).observe(document.body,
            { attributes: true, subtree: true, attributeFilter: ['class'] });
        check();
        return true;
    }""")
    page.locator(".qa-hit").nth(idx).click()

    assert poll_until(page, lambda: _qa_state(page)["current"] == target, timeout_ms=10000), \
        (f"点命中后未载入**该**会话：期望 currentSessionId={target}，"
         f"实得 {_qa_state(page)['current']}（hits={hits}）")
    assert poll_until(page, lambda: page.locator(".qa-msg").count() >= 2, timeout_ms=10000), \
        f"载入的会话未渲染出问答两条（当前 {page.locator('.qa-msg').count()} 条）"
    assert MOCK_ANSWER_MARKER in page.locator(".qa-messages").inner_text(), \
        f"载入的会话里看不到命中内容（{MOCK_ANSWER_MARKER}）"

    assert page.evaluate("() => !!(window.__qaHl && window.__qaHl.seen)"), \
        "点命中后没有任何消息被加上 .qa-highlight（jumpToHit 的定位/高亮未生效）"
    hl = page.evaluate("() => window.__qaHl")
    assert hl["bg"] not in ("", "rgba(0, 0, 0, 0)", "transparent"), \
        f"高亮消息的底色为透明：{hl['bg']!r}（.qa-highlight 未生效）"
    assert hl["base"] and hl["bg"] != hl["base"], \
        (f".qa-highlight 对底色毫无影响（有类 {hl['bg']!r} / 无类 {hl['base']!r}）"
         "—— app.css 里缺该规则，高亮是视觉 no-op")


def _qa_ask_and_remember(page, question):
    """提问并返回**落库后的会话 id**（本组多处要按会话 id 判「载入的是哪一个」）。"""
    _qa_ask(page, question, expect_msgs=2)
    sid = _qa_state(page)["current"]
    assert sid, f"提问「{question}」后 currentSessionId 仍为空（会话未落库）"
    return sid


# ── 运行期设置的读写通道：**不依赖页面 DOM/JS**（裁决 R1-4）─────────────────
# 原写法走 `page.evaluate(fetch('/settings', ...))`。风险：用例失败若发生在页面崩溃 /
# 导航之后，`finally` 里的 `page.evaluate` **自身**就会抛 ⇒ 还原失败 ⇒ 被置空的
# `ai.custom.api_key` 留在**长期保留**的 `data/_probe_qa.db` 里，污染后续**所有**用例
# （下一个用例的前置断言才响亮失败，冤枉对象已经错位）。
# 故读写都改用与渲染进程无关的两条通道：
#   ① HTTP：`page.request`（Playwright 侧的 HTTP 客户端，与浏览器上下文共享 cookie）
#      ——不碰 DOM/JS，页面崩溃也照样能用；主通道（不需要额外环境变量）；
#   ② sqlite3：直连 `DATABASE_PATH` 指向的库——连实例都没了也能还原/复验。
#      **可选**：该环境变量未设置时明确报错，**绝不回落到 dev 库**（路径只认环境变量，不猜）。


def _settings_http(page, method, payload=None):
    """经 Playwright 的 HTTP 客户端读写 /settings（与页面 DOM/JS 无关）。"""
    if method == "GET":
        resp = page.request.get(f"{BASE}/settings")
    else:
        resp = page.request.put(f"{BASE}/settings", data=payload or {})
    assert resp.ok, f"{method} /settings 失败：HTTP {resp.status}"
    return resp.json()


def _settings_sqlite(keys=None, payload=None):
    """直连副本库读写 settings（备用通道）。

    只读 `keys` 或只写 `payload`，二选一。`DATABASE_PATH` 未设置时抛 RuntimeError
    （**不猜路径**——回落到 `data/spec_query.db` 就等于改写 dev 库）。
    """
    db_path = os.environ.get("DATABASE_PATH", "")
    if not db_path:
        raise RuntimeError(
            "DATABASE_PATH 未设置：无法直连副本库操作 settings（拒绝猜路径，可能是 dev 库）。"
            "如需这条备用通道，请按报告「环境」节 export DATABASE_PATH=<副本库>")
    with sqlite3.connect(db_path, timeout=30) as conn:
        if payload is not None:
            for k, v in payload.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (str(k), str(v)),
                )
            return {}
        out: dict[str, str] = {}
        for k in keys or []:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (k,)
            ).fetchone()
            out[k] = row[0] if row else ""
        return out


def _read_origin_settings(page, keys):
    """把原值取到 Python 侧（裁决 R1-4）：主通道 HTTP，HTTP 不通时直连副本库。"""
    try:
        allset = _settings_http(page, "GET")
        return {k: allset.get(k) for k in keys}
    except (PlaywrightError, AssertionError) as e:
        print(f"[probe] 读设置走 HTTP 失败，改用 sqlite3 直连副本库：{type(e).__name__}: {e}",
              file=sys.stderr)
        return dict(_settings_sqlite(keys=keys))


def _put_settings(page, payload):
    """写运行期设置（造错用；**用完必须还原**）。返回是否成功。"""
    try:
        _settings_http(page, "PUT", payload)
        return True
    except (PlaywrightError, AssertionError):
        return False


def _restore_settings(page, origin):
    """把 origin 逐键写回运行期设置——**不依赖页面**（裁决 R1-4，见上方通道说明）。

    两条通道依次尝试，任一成功即返回；第二条（sqlite3）是最后保底，
    确保「页面崩了 ⇒ 还原不了 ⇒ 空 key 永久留在副本库」这个链路被切断。
    """
    try:
        _settings_http(page, "PUT", origin)
        return
    except (PlaywrightError, AssertionError) as e:
        print(f"[probe] HTTP 通道还原失败，改用 sqlite3 直连副本库：{type(e).__name__}: {e}",
              file=sys.stderr)
    _settings_sqlite(payload=origin)


def _verify_settings_restored(page, keys):
    """复验还原结果（返回实际值字典）；HTTP 不可用时退到 sqlite3；都不通则返回 None 值
    （让调用点的断言响亮失败，而不是静默跳过复验）。"""
    try:
        allset = _settings_http(page, "GET")
        return {k: allset.get(k) for k in keys}
    except (PlaywrightError, AssertionError):
        try:
            return dict(_settings_sqlite(keys=keys))
        except RuntimeError:
            return {k: None for k in keys}


def t5_error_frame_is_not_retried_as_fallback(page):
    """异常场景（本 Task 的关键边界）：SSE 的 `error` 帧**只展示**，不得触发非流式重发。

    ⚠️ 本 Task 简报说两条边界「都有对应用例」，但 error 帧这条**没有任何用例**守着
    （简报 Step 1 只登记了 4 条，且都不碰错误路径）⇒ mutation B（让 error 帧也 throw）
    没有判据可红。故本 Agent 补此用例（与 `t5_stage_indicator_reflects_real_stage_events`
    对应另一条边界，两者配对）。

    为什么必须这样判：`_handleSseChunk` 的 throw 会一路冒到 `send()` 的 catch ⇒ 走
    `_fallbackAsk` **重发同一个问题**。后端已经明确报错（未配置 / 模型不可用）时，这是
    白等一轮，且用户看到**两次完整生成**（重复计费、重复落库）。

    造错方式：把 `ai.custom.api_key` 置空 ⇒ `APIBackend.is_available()` 为 False
    ⇒ 后端依次发 `stage(retrieving)` → `error`（**不碰网络，确定性失败**，不依赖 mock 的
    行为）。判据三条：
      ① 界面上出现的是 **error 帧的文案**（「…不可用…」），不是兜底失败文案；
      ② 全程**只有一次** POST /qa/ask（负向判据走有界观察窗）；
      ③ 那一次请求体带 `stream: true`（钉「走的是流式入口」）。

    ⚠️ 还原必须**无条件可靠**（裁决 R1-4）：本用例把 `ai.custom.api_key` 置空，而该库
    （`data/_probe_qa.db`）是**长期保留**的复核资产——若失败发生在页面崩溃/导航之后，
    旧写法 `finally` 里的 `page.evaluate` 自身会抛 ⇒ 空 key 留下 ⇒ 污染后续所有用例。
    现在原值取到 Python 侧、还原走「`page.request` HTTP 通道 → 直连 sqlite3」两条
    **不依赖页面**的通道（见 `_restore_settings`），并**复验**还原结果。
    前置的「key 非空」断言**保留**：它在真被污染时响亮失败。
    """
    _qa_open(page)
    keys = ["ai.backend", "ai.custom.api_key"]
    origin = _read_origin_settings(page, keys)   # 原值取到 Python 侧（不经页面 JS）
    assert origin.get("ai.custom.api_key"), \
        f"前置不成立：副本库的 ai.custom.api_key 本应为 mock 值，实得 {origin!r}"
    bodies = _track_ask_bodies(page)      # 必须在提问之前注册
    try:
        assert _put_settings(page, {"ai.backend": "custom", "ai.custom.api_key": ""}), \
            "切换 ai 后端设置失败（PUT /settings）"
        page.fill(".qa-composer textarea", "混凝土强度等级如何评定")
        page.press(".qa-composer textarea", "Enter")
        assert poll_until(page, lambda: "不可用" in _last_answer_text(page), timeout_ms=40000), \
            (f"未显示 error 帧的文案（应含「不可用」），实得 {_last_answer_text(page)!r}"
             "—— 走了兜底分支就会变成『请求失败，请稍后重试』")
        # ② 负向判据的有界观察窗：一旦真的重发，立即判红（不是"睡够就算过"）
        retried = poll_until(page, lambda: len(bodies) >= 2, timeout_ms=1500)
        assert not retried, \
            (f"error 帧触发了非流式重发（共 {len(bodies)} 次 /qa/ask："
             f"{[b.get('stream') for b in bodies]}）——后端已报错还白等一轮、用户看到两次生成")
        assert len(bodies) == 1, f"期望恰好一次 /qa/ask，实得 {len(bodies)} 次"
        # ③ 走的是流式入口（body.stream 由 _streamAsk 注入）
        assert bodies[0].get("stream") is True, \
            f"提问未走流式入口（请求体 stream 应为 True）：{bodies[0]}"
        assert page.locator(".qa-msg").count() >= 2, \
            "error 帧是应用层错误：本轮已受理，用户消息不应被回滚"
    finally:
        # 无论成败都还原，且**不依赖页面**（裁决 R1-4）：页面崩溃/导航后 evaluate 会抛，
        # 空 key 就会留在长期保留的副本库里，污染后续所有用例。
        _restore_settings(page, origin)
        # 复验：还原是"写回去了"还是"自以为写回去了"，必须读回来才算证据。
        back = _verify_settings_restored(page, keys)
        assert back.get("ai.custom.api_key") == origin["ai.custom.api_key"], \
            (f"副本库的 ai.custom.api_key 未还原（应为 {origin['ai.custom.api_key']!r}，"
             f"实得 {back.get('ai.custom.api_key')!r}）—— 空 key 会污染后续所有用例")


# 登记进本 Task 的键：**键名 = Task 编号本身**（不是 t6）。
def t5_settings_button_opens_ai_tab(page):
    """回归（控制器收尾时用浏览器实机验证发现并修复的真回归）：QA 页头部的 ⚙️ 必须**直接落到 AI 页签**。

    背景：旧弹窗用的是 `onclick="window.dispatchEvent(new CustomEvent('open-settings', {detail:{tab:'ai'}}))"`
    ——那是 **CustomEvent** 的 `detail`；T2 的模板改写成 Alpine 的
    `@click="$dispatch('open-settings', {detail:{tab:'ai'}})"`，而 Alpine 的 `$dispatch(name, detail)`
    **第二个参数本身就是 detail** ⇒ `event.detail = {detail:{...}}` ⇒ settingsDialog 读 `e.detail.tab`
    得 undefined ⇒ **静默回退到 ocr 页签**。弹窗**照常打开**，所以「能打开设置」这类判据抓不到它。
    把模板改回 `{detail:{tab:'ai'}}` → 本用例必红（activeTab 会是 'ocr'）。
    """
    _qa_open(page)
    page.click('button[aria-label="AI 设置"]')
    state = page.wait_for_function(
        """() => {
            const el = document.querySelector('.qa-modal-overlay');
            const st = el && el._x_dataStack ? el._x_dataStack[0] : null;
            return st && st.open ? { open: st.open, tab: st.activeTab } : null;
        }""",
        timeout=10000,
    ).json_value()
    assert state["open"] is True, "⚙️ 未打开设置弹窗"
    assert state["tab"] == "ai", \
        f"⚙️ 应直接落到 AI 页签，实得 {state['tab']!r}" \
        "（Alpine 的 $dispatch 第二个参数就是 detail，不要再包一层 detail；旧弹窗的 CustomEvent 形状不能照抄）"
    # 收尾：关掉弹窗，避免影响后续用例
    page.evaluate(
        "() => { const el = document.querySelector('.qa-modal-overlay');"
        " if (el && el._x_dataStack) el._x_dataStack[0].close(); }")


CASES["t5"] = [t5_streaming_renders_progressively,
               t5_streaming_shows_plain_text_not_markdown,
               t5_rerank_badge_handles_unreported_state,
               t5_stage_indicator_reflects_real_stage_events,
               # 本 Task 补的两处遗漏探针（简报 🧩 段）：
               # relax 放宽重发、跨会话搜索命中跳转 + 高亮计算样式。
               t5_relax_resends_with_relaxed_flag,
               t5_search_hit_jump_loads_session_and_highlights,
               # 控制器收尾时用浏览器实机验证发现并修复的真回归（⚙️ 落错页签）⇒ 补守卫：
               t5_settings_button_opens_ai_tab,
               # 本 Agent 补（简报又一缺项）：error 帧 ⇏ 兜底重发这条边界**无用例**，
               # 简报 Step 1 登记的 4 条一条都不碰错误路径（mutation B 因此无从判红）。
               # 放最后：它会临时改运行期设置（用例内已用 finally 还原）。
               t5_error_frame_is_not_retried_as_fallback]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "t1"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1600, "height": 900})
        login(pg)          # 受 AuthMiddleware 保护，先登录（见文件头注释）
        n = 0
        for fn in CASES[which]:
            fn(pg)
            print(f"PASS {fn.__name__}")
            n += 1
        # 自报条数：各 Task 的 Expected 一律照这一行核对，**不要在计划里手写死条数**
        # （首版手写的「3 行 / 5 行 PASS」与 CASES 实际登记数不符，是同一类漂移）
        print(f"== {which}: {n}/{len(CASES[which])} passed ==")
        browser.close()
