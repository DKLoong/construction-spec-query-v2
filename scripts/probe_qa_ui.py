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
    """展开第一个维度后点击其第一个分类项（模拟用户操作）。"""
    page.wait_for_selector(".tree-container details", state="attached", timeout=10000)
    page.click(".tree-container details > summary >> nth=0")   # 折叠 → 展开
    page.wait_for_timeout(250)
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
    page.wait_for_timeout(500)
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
    page.wait_for_timeout(500)
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
