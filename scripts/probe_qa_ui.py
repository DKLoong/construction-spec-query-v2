"""QA 界面改造的一次性验证探针（验证完删除）。

用法：先起隔离实例（本计划「前置」节），再
  D:/Python/python.exe scripts/probe_qa_ui.py t1
"""
import os
import re
import sys

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
