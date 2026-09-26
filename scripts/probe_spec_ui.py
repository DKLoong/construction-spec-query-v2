"""规范管理页 UI 行为回归套件（f1=条文详情弹窗翻页；后续 Task 在此追加 f2/f3）。

**保留决定（2026-09-25）**：与 probe_qa_ui.py 同性质——本仓前端行为的验收网，
覆盖「点内容列开弹窗 / 翻页计数与边界 / 控件基线对齐 / 状态切换确认框 /
分类输入悬浮提示」这些**后端测试看不见**的交互。请勿当一次性脚本删除；
若 UI 大改导致失效，请修用例而不是关掉。

## 运行方式（缺一不可）

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_spec.db          # 副本库，**不要**在 dev 库上跑
export DATABASE_PATH="$PWD/data/_probe_spec.db"    # 只走环境变量，禁止改源码常量
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &   # 不加 --reload
D:/Python/python.exe scripts/probe_spec_ui.py f1   # f1|f2|f3
```

**前置要点**：
- 全站受 `AuthMiddleware` 保护，runner 会先登录（`PROBE_USER`/`PROBE_PASS` 可覆盖，
  默认取 `scripts/create_admin.py` 的默认账号）。
- **用例自带前置**：不依赖副本库里手工点过的状态；条文数从页面上现数，不写死。
- 改静态 js/css 后，跑之前确认 `base.html` 的 `?v=N` 已递增（缓存会让探针读到旧文件）。
- 跑完清理：杀掉 8123 进程、删除 `data/_probe_spec.db`。
- **视口宽度会影响结论**（见 memory: ui-isolated-verification）：默认 1600×900，
  窄视口相关用例请自行在用例内 resize 并在 finally 还原。

用法（单组）：
  D:/Python/python.exe scripts/probe_spec_ui.py f1
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"
PROBE_USER = os.environ.get("PROBE_USER", "admin")
PROBE_PASS = os.environ.get("PROBE_PASS", "admin123")


def login(page):
    """登录隔离实例。凭据默认取 dev 库副本里的管理员，可用环境变量覆盖。"""
    page.goto(f"{BASE}/login")
    page.fill("input[name=username]", PROBE_USER)
    page.fill("input[name=password]", PROBE_PASS)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/", timeout=10000)


def poll_until(page, predicate, timeout_ms, interval_ms=100):
    """有界轮询 `predicate()`；满足即返回 True，超时返回 False。

    **不无限挂起、不抛异常**（超时交给调用点的 assert 给出精确失败信息）。
    """
    waited = 0
    while True:
        if predicate():
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(interval_ms)
        waited += interval_ms


# ═══════════════════════════════════════════
# 共用步骤
# ═══════════════════════════════════════════

def open_first_spec_clauses(page):
    """进入规范管理页 → 点第一本规范的「查看条文」→ 返回列表里的条文条数

    ⚠️ 数行必须用 `>` 直接子选择器：条文内容里的 md 表格（PaddleOCR-VL 导出的
    HTML `<table>`）渲染后会嵌进预览列，后代选择器 `.clause-table tbody tr`
    会把这些**嵌套表格的行**一并数进来（实测 73 条条文数出 247 个 tr）。
    """
    page.goto(f"{BASE}/specs")
    page.wait_for_selector("button:has-text('查看条文')", timeout=15000)
    page.locator("button:has-text('查看条文')").first.click()
    page.wait_for_selector(".clause-table > tbody > tr", timeout=15000)
    # 预览列是 htmx swap 后由 specs.js 渲染的；等它落位再点，避免点到空 div
    page.wait_for_selector(".clause-preview-md[data-clause-id]", timeout=15000)
    return page.locator(".clause-table > tbody > tr").count()


def open_modal_by_clicking_preview(page, nth=0):
    """点第 nth 个内容列 → 等弹窗可见且详情加载完成"""
    page.locator(".clause-preview-md[data-clause-id]").nth(nth).click()
    assert poll_until(page, lambda: page.evaluate(
        "() => document.getElementById('clause-modal-overlay').style.display === 'flex'"),
        timeout_ms=5000), "点击条文内容列未打开详情弹窗"
    page.wait_for_selector("#clause-modal-content article", timeout=10000)


def nav_state(page):
    """读取翻页条的当前状态：(计数文本, prev 是否禁用, next 是否禁用, 标题文本)"""
    return {
        "count": page.locator("#clause-nav-count").inner_text().strip(),
        "prev_disabled": page.locator("#clause-nav-prev").is_disabled(),
        "next_disabled": page.locator("#clause-nav-next").is_disabled(),
        "title": page.locator("#clause-modal-content h4").inner_text().strip(),
    }


def close_modal(page):
    page.keyboard.press("Escape")
    assert poll_until(page, lambda: page.evaluate(
        "() => document.getElementById('clause-modal-overlay').style.display === 'none'"),
        timeout_ms=3000), "ESC 未能关闭弹窗"


# ═══════════════════════════════════════════
# f1：条文详情弹窗 —— 点内容列打开 + 底部翻页
# ═══════════════════════════════════════════

def _force_spec_status(page, status="现行"):
    """把库内那本规范的状态幂等置为指定值（**用例自带前置**）。

    为什么必须显式做：检索默认只搜「现行」，规范若停在废止态，检索用例会以
    「没搜到结果」失败——离真实原因隔了一层。而状态确实可能不是现行：
    f3 会改它；更隐蔽的是，**变异测试会让 f3 中途失败并跳过清理步骤**
    （曾实测：变异版跑完库内留下「废止」，随后 f1 的检索用例莫名失败）。
    不要依赖「上一轮留下的状态」——那是本仓探针反复踩过的老坑。
    """
    page.goto(f"{BASE}/specs")
    page.wait_for_selector(".spec-status-select", timeout=15000)
    sid = page.locator(".spec-status-select").first.get_attribute("data-id")
    page.evaluate("""async ([id, status]) => {
        await fetch(`/specs/${id}/status`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: new URLSearchParams({status}),
        });
    }""", [sid, status])

def f1_click_preview_opens_modal_with_footer(page):
    """点内容列应打开详情弹窗，且底部出现翻页条（此前只能靠检索页打开）"""
    n = open_first_spec_clauses(page)
    assert n >= 2, f"探针需要至少 2 条条文才能验证翻页，副本库实有 {n} 条"
    open_modal_by_clicking_preview(page)
    footer_visible = page.locator("#clause-modal-footer").is_visible()
    assert footer_visible, "条文详情弹窗底部未出现翻页条"
    close_modal(page)


def f1_first_clause_shows_1_of_n_with_prev_disabled(page):
    """首条显示 1/N，且「上一条」禁用、「下一条」可用"""
    n = open_first_spec_clauses(page)
    open_modal_by_clicking_preview(page)
    st = nav_state(page)
    assert st["count"] == f"1/{n}", f"首条计数应为 1/{n}，实际 {st['count']}"
    assert st["prev_disabled"], "首条时「上一条」应禁用"
    assert not st["next_disabled"], "首条时「下一条」应可用"
    close_modal(page)


def f1_next_switches_content_and_count(page):
    """点「下一条」：计数递增**且正文真的换成下一条**（只改计数不改内容是典型假实现）"""
    open_first_spec_clauses(page)
    open_modal_by_clicking_preview(page)
    before = nav_state(page)
    page.locator("#clause-nav-next").click()
    # 等标题真的变化——这是「内容已换」的忠实代理，比 sleep 可靠
    assert poll_until(page, lambda: page.locator("#clause-modal-content h4").inner_text().strip()
                      != before["title"], timeout_ms=5000), \
        "点「下一条」后正文标题没变——只更新了计数、没换内容"
    after = nav_state(page)
    assert after["count"] == f"2/{before['count'].split('/')[1]}", \
        f"计数应递增到 2/N，实际 {after['count']}"
    close_modal(page)


def f1_prev_returns_to_first_and_disables_again(page):
    """从第 2 条点「上一条」应回到 1/N，且「上一条」重新禁用（边界双向）"""
    open_first_spec_clauses(page)
    open_modal_by_clicking_preview(page)
    first_title = nav_state(page)["title"]
    page.locator("#clause-nav-next").click()
    assert poll_until(page, lambda: page.locator("#clause-modal-content h4").inner_text().strip()
                      != first_title, timeout_ms=5000), "「下一条」未生效，无法验证回退"
    page.locator("#clause-nav-prev").click()
    assert poll_until(page, lambda: page.locator("#clause-modal-content h4").inner_text().strip()
                      == first_title, timeout_ms=5000), "点「上一条」未回到首条"
    st = nav_state(page)
    assert st["count"].startswith("1/"), f"回到首条后计数应为 1/N，实际 {st['count']}"
    assert st["prev_disabled"], "回到首条后「上一条」应重新禁用"
    close_modal(page)


def f1_nav_controls_baseline_aligned(page):
    """三控件须基线对齐：比较垂直中心（比 height 会被不同字号骗过）

    ⚠️ 两个测量陷阱（都实测踩过）：
      1. **必须同一帧内一次读完**。逐元素调用 bounding_box() 是三次独立往返，
         期间弹窗里的图片异步加载会重排布局，footer 跟着移动 ⇒ 数出 3.3px 的
         「未对齐」假红（实际三者中心完全一致）。故用一次 evaluate 取三个值。
      2. 读之前先等图片加载完（下面 poll），否则拿到的是过渡态布局。
    """
    open_first_spec_clauses(page)
    open_modal_by_clicking_preview(page)
    # 尽力等布局稳定：图片全部 complete（无图片则立即通过；超时不判红，交给下面的断言说话）
    poll_until(page, lambda: page.evaluate(
        "() => Array.from(document.querySelectorAll('#clause-modal-content img'))"
        ".every(i => i.complete)"), timeout_ms=8000)
    centers = page.evaluate("""() => ['#clause-nav-prev', '#clause-nav-count', '#clause-nav-next']
        .map(sel => { const r = document.querySelector(sel).getBoundingClientRect();
                      return r.top + r.height / 2; })""")
    spread = max(centers) - min(centers)
    assert spread <= 1.0, \
        f"翻页三控件未对齐：垂直中心最大相差 {spread:.1f}px（应 ≤1px）；实测中心 {centers}"
    close_modal(page)


def f1_search_result_opens_modal_with_nav(page):
    """检索结果打开的弹窗同样带翻页上下文（同一套弹窗，行为一致）

    ⚠️ 必须从**主页发起搜索**，不能 goto `/search?keyword=...`：`/search` 是
    htmx 用的**片段路由**（只返回 result_content.html），直接打开它页面里既没有
    弹窗容器也没有 clause-modal.js，点击必然无声无息（曾因此假红一次）。
    """
    _force_spec_status(page, "现行")   # 检索默认只搜现行，先确保它可被搜到
    page.goto(BASE)                       # 主页：完整 base.html + 弹窗容器
    page.fill(".search-box input[type=search]", "钢筋")
    page.press(".search-box input[type=search]", "Enter")
    page.wait_for_selector(".result-item", timeout=15000)
    n = page.locator(".result-item").count()
    assert n >= 2, f"关键词「钢筋」命中 {n} 条，不足以验证翻页"
    page.locator(".result-item").first.click()
    page.wait_for_selector("#clause-modal-content article", timeout=10000)
    st = nav_state(page)
    assert st["count"] == f"1/{n}", f"检索页弹窗首条计数应为 1/{n}，实际 {st['count']}"
    assert st["prev_disabled"], "检索页弹窗首条「上一条」应禁用"
    close_modal(page)


CASES = {
    "f1": [f1_click_preview_opens_modal_with_footer,
           f1_first_clause_shows_1_of_n_with_prev_disabled,
           f1_next_switches_content_and_count,
           f1_prev_returns_to_first_and_disables_again,
           f1_nav_controls_baseline_aligned,
           f1_search_result_opens_modal_with_nav],
}


# ═══════════════════════════════════════════
# f2：条文行的「编辑」入口（用户报的回归路径）
# ═══════════════════════════════════════════

def _first_row(page):
    return page.locator(".clause-table > tbody > tr").first


def _assert_split_editor(page):
    """断言已进入分栏编辑页（左编辑右实时预览），而非旧的行内输入框"""
    page.wait_for_url("**/edit-page", timeout=10000)
    page.wait_for_selector(".review-panels textarea", timeout=10000)
    assert page.locator(".review-preview").count() == 1, "分栏编辑页缺少右侧实时预览栏"


def f2_class_cancel_then_edit_opens_split_editor(page):
    """点「分类」→ 取消 → 点「编辑」：必须进分栏编辑页

    用户 2026-09-25 报的路径：取消后回填的行带着旧版「编辑」按钮，点了掉回行内输入框。
    根因是「正常行」有三份拷贝，回填那两份没跟着升级（已收敛为 clause_row.html）。
    """
    open_first_spec_clauses(page)
    _first_row(page).locator("button:has-text('分类')").click()
    page.wait_for_selector(".clause-table > tbody > tr input[name=dim4_specialty]", timeout=8000)
    page.locator(".clause-table > tbody > tr").first.locator("button:has-text('取消')").click()
    page.wait_for_selector(".clause-table > tbody > tr button:has-text('编辑')", timeout=8000)
    _first_row(page).locator("button:has-text('编辑')").click()
    _assert_split_editor(page)


def f2_class_save_then_edit_opens_split_editor(page):
    """点「分类」→ 保存 → 点「编辑」：同样必须进分栏编辑页（原值提交，不改数据）"""
    open_first_spec_clauses(page)
    _first_row(page).locator("button:has-text('分类')").click()
    page.wait_for_selector(".clause-table > tbody > tr input[name=dim4_specialty]", timeout=8000)
    page.locator(".clause-table > tbody > tr").first.locator("button:has-text('保存')").click()
    page.wait_for_selector(".clause-table > tbody > tr button:has-text('编辑')", timeout=8000)
    _first_row(page).locator("button:has-text('编辑')").click()
    _assert_split_editor(page)


def f2_restored_row_preview_is_rendered_markdown(page):
    """分类取消回填的行，内容列须仍是**渲染后的 md**（旧版回填是纯文本截断）

    判据：回填行存在 .clause-preview-md 且其内有块级子元素（mdRender 产物），
    旧实现的回填行只有一个纯文本 <span>（无该 div/子元素）。
    """
    open_first_spec_clauses(page)
    _first_row(page).locator("button:has-text('分类')").click()
    page.wait_for_selector(".clause-table > tbody > tr input[name=dim4_specialty]", timeout=8000)
    page.locator(".clause-table > tbody > tr").first.locator("button:has-text('取消')").click()
    page.wait_for_selector(".clause-table > tbody > tr button:has-text('编辑')", timeout=8000)
    info = _first_row(page).evaluate("""row => {
        const box = row.querySelector('.clause-preview-md');
        return { hasBox: !!box, childTags: box ? box.children.length : 0,
                 text: box ? box.textContent.trim().length : 0 };
    }""")
    assert info["hasBox"], "回填行的内容列不是 .clause-preview-md 容器（退回了旧版纯文本行）"
    assert info["childTags"] > 0 and info["text"] > 0, \
        f"回填行内容列未渲染出内容：{info}"


CASES["f2"] = [f2_class_cancel_then_edit_opens_split_editor,
               f2_class_save_then_edit_opens_split_editor,
               f2_restored_row_preview_is_rendered_markdown]


# ═══════════════════════════════════════════
# f3：规范状态切换的确认与回滚
# ═══════════════════════════════════════════

def _status_select(page):
    page.goto(f"{BASE}/specs")
    page.wait_for_selector(".spec-status-select", timeout=15000)
    return page.locator(".spec-status-select").first


def _status_value_after_reload(page):
    """刷新后重读下拉框的值——用于判定「是否真的落库」（界面值可能只是本地状态）"""
    page.reload()
    page.wait_for_selector(".spec-status-select", timeout=15000)
    return page.locator(".spec-status-select").first.input_value()


def f3_status_change_confirm_cancel_and_accept(page):
    """改状态：取消则回滚且不落库；确认则落库（用例自恢复原值）

    判据用「刷新后重读」而非读界面值：只有服务端真改了，刷新才会变。
    """
    sel = _status_select(page)
    before = sel.input_value()
    target = "废止" if before != "废止" else "现行"

    # 1) 取消：回滚 + 不落库
    msgs = []
    page.once("dialog", lambda d: (msgs.append(d.message), d.dismiss()))
    sel.select_option(target)
    assert msgs, "修改规范状态没有弹确认框（防误操作失效）"
    assert target in msgs[0] and "是否应用" in msgs[0], f"确认框文案不符：{msgs[0]!r}"
    assert sel.input_value() == before, "取消后下拉框未回滚到修改前的值"
    assert _status_value_after_reload(page) == before, \
        "取消后状态仍被写入服务端——取消必须不发请求"

    # 2) 确认：落库
    page.once("dialog", lambda d: d.accept())
    _status_select(page).select_option(target)
    assert _status_value_after_reload(page) == target, "确认后状态未落库"

    # 3) 还原（副本库虽是一次性的，但同轮后续用例不应继承本用例的改动）
    page.once("dialog", lambda d: d.accept())
    _status_select(page).select_option(before)
    assert _status_value_after_reload(page) == before, "用例未能恢复原状态"


CASES["f3"] = [f3_status_change_confirm_cancel_and_accept]


# ═══════════════════════════════════════════
# f4：规范分类保存后，表格标签同步
# ═══════════════════════════════════════════

def f4_class_save_updates_table_label(page):
    """改规范分类并保存 → 表格里那一行的标签必须立刻变（用例自还原）

    用户报的现象：下方出现了「✅ 分类已保存」，但表格的分类列纹丝不动。
    """
    page.goto(f"{BASE}/specs")
    page.wait_for_selector("button:has-text('📋 分类')", timeout=15000)
    # 落点是单元格**内部**的 span（不能是 td：htmx 用 DOMParser 提取 OOB 时顶层 td 会被丢弃）
    cell = page.locator("[id^='spec-class-cell-']").first

    # 从编辑表单读 dim2 原值（比从标签文本反推可靠：标签可能是占位「-」）
    page.locator("button:has-text('📋 分类')").first.click()
    page.wait_for_selector("#spec-class-area input[name=dim2_stage]", timeout=8000)
    original = page.input_value("#spec-class-area input[name=dim2_stage]")

    new_val = "施工" if original != "施工" else "验收"
    page.fill("#spec-class-area input[name=dim2_stage]", new_val)
    page.locator("#spec-class-area button[type=submit]").click()

    assert poll_until(page, lambda: new_val in cell.inner_text(), timeout_ms=8000), \
        f"保存后表格分类标签未同步更新（单元格仍为 {cell.inner_text()!r}）"

    # 还原
    page.locator("button:has-text('📋 分类')").first.click()
    page.wait_for_selector("#spec-class-area input[name=dim2_stage]", timeout=8000)
    page.fill("#spec-class-area input[name=dim2_stage]", original)
    page.locator("#spec-class-area button[type=submit]").click()
    assert poll_until(page, lambda: new_val not in cell.inner_text(), timeout_ms=8000), \
        "用例未能还原分类（同轮后续用例不应继承本次改动）"


CASES["f4"] = [f4_class_save_updates_table_label]


# ═══════════════════════════════════════════
# f5：分类输入框的聚焦悬浮提示
# ═══════════════════════════════════════════

def _hint_state(page):
    """读取提示框状态与「是否可能被裁剪」所需的三项事实。

    ⚠️ **不要用 elementsFromPoint 判裁剪**：提示框带 `pointer-events:none`
    （故意如此，免得挡住它下面的输入框），而 elementsFromPoint **会跳过这类元素**
    —— 于是无论裁没裁都返回「不在渲染树」，是个恒假的判据（本用例踩过：
    明明位置正确、在视口内，却报「被滚动容器裁剪」）。

    改用三条充分判据：fixed 定位 + 不落在滚动容器内 + 完整矩形在视口内。
    """
    return page.evaluate("""() => {
        const el = document.getElementById('field-hint-pop');
        if (!el) return null;
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {display: cs.display, text: el.textContent.trim(), font: cs.fontFamily,
                top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width,
                position: cs.position,
                inScrollContainer: !!el.closest('.clause-table-wrapper'),
                inViewport: r.top >= 0 && r.left >= 0 &&
                            r.bottom <= window.innerHeight && r.right <= window.innerWidth};
    }""")


def _assert_hint_below_input(page, inp):
    box = inp.bounding_box()
    assert box, "输入框不可见，无法测量"
    assert poll_until(page, lambda: (_hint_state(page) or {}).get("display") == "block",
                      timeout_ms=3000), "聚焦分类输入框后未出现提示框"
    st = _hint_state(page)
    assert "半角标点" in st["text"], f"提示文案不符：{st['text']!r}"
    assert st["top"] >= box["y"] + box["height"] - 1, \
        f"提示框未出现在输入框下方（top={st['top']:.1f} vs 输入框底 {box['y'] + box['height']:.1f}）"
    assert abs(st["left"] - box["x"]) < 3, \
        f"提示框未与输入框左对齐（left={st['left']:.1f} vs {box['x']:.1f}）"
    assert st["width"] <= box["width"] + 1, \
        f"提示框比输入框宽（{st['width']:.0f} > {box['width']:.0f}）——" \
        "条文分类的输入框紧邻操作列，铺开会横向盖住「保存/取消」按钮"
    assert "mono" in st["font"].lower() or "consolas" in st["font"].lower(), \
        f"提示框未使用等宽字体：{st['font']}"
    return st


def f5_spec_class_hint_appears_below_and_hides_on_blur(page):
    """规范分类：聚焦显示在输入框下方（等宽），失焦消失"""
    page.goto(f"{BASE}/specs")
    page.wait_for_selector("button:has-text('📋 分类')", timeout=15000)
    page.locator("button:has-text('📋 分类')").first.click()
    inp = page.locator("#spec-class-area input[name=dim2_stage]")
    inp.wait_for(timeout=8000)

    inp.focus()
    _assert_hint_below_input(page, inp)

    inp.evaluate("el => el.blur()")
    assert poll_until(page, lambda: (_hint_state(page) or {}).get("display") == "none",
                      timeout_ms=3000), "失焦后提示框未消失"


def f5_clause_class_hint_survives_table_scroll_container(page):
    """条文分类（表格行内）：聚焦**最后一行**的输入框，提示仍完整可见

    这是选 fixed 定位的全部理由：表格外层 .clause-table-wrapper 是
    max-height:55vh + overflow:auto 的滚动容器，absolute 提示块在最后一行会被裁掉。
    """
    open_first_spec_clauses(page)
    rows = page.locator(".clause-table > tbody > tr")
    last = rows.nth(rows.count() - 1)
    last.locator("button:has-text('分类')").click()
    inp = last.locator("input[name=dim6_material]")
    inp.wait_for(timeout=8000)
    inp.focus()

    st = _assert_hint_below_input(page, inp)
    assert st["position"] == "fixed", \
        f"提示框不是 fixed 定位（{st['position']}），会被表格滚动容器裁剪"
    assert not st["inScrollContainer"], "提示框落在了滚动容器内部，会被 overflow 裁掉"
    assert st["inViewport"], f"提示框未完整落在视口内，用户看不到：{st}"

    inp.evaluate("el => el.blur()")
    assert poll_until(page, lambda: (_hint_state(page) or {}).get("display") == "none",
                      timeout_ms=3000), "失焦后提示框未消失"


CASES["f5"] = [f5_spec_class_hint_appears_below_and_hides_on_blur,
               f5_clause_class_hint_survives_table_scroll_container]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "f1"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_page(viewport={"width": 1600, "height": 900})
        login(pg)
        n = 0
        for fn in CASES[which]:
            fn(pg)
            print(f"PASS {fn.__name__}")
            n += 1
        # 自报条数：核对时一律照这一行，不在别处手写预计条数（会漂移）
        print(f"== {which}: {n}/{len(CASES[which])} passed ==")
        browser.close()
