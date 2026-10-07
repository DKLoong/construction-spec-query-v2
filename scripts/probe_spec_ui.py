"""规范管理页 UI 行为回归套件（f1=条文详情弹窗翻页；后续 Task 在此追加 f2/f3）。

**保留决定（2026-09-25）**：与 probe_qa_ui.py 同性质——本仓前端行为的验收网，
覆盖「点内容列开弹窗 / 翻页计数与边界 / 控件基线对齐 / 状态切换确认框 /
分类输入悬浮提示」这些**后端测试看不见**的交互。请勿当一次性脚本删除；
若 UI 大改导致失效，请修用例而不是关掉。

## 运行方式（缺一不可）

```bash
cd /d/CC-Workspace/construction-spec-query-v2
cp data/spec_query.db data/_probe_spec.db          # 副本库，**不要**在 dev 库上跑
# ⚠ 四条路径全都要隔离，不能只改 DATABASE_PATH：f8/f10/f11 会**真实上传**文件，
#   只隔离库的话原文件会写进真实 data/uploads、OCR 产物写进真实 data/outputs
#   （与 tests/conftest.py::isolated_paths 的 C-4 同一个坑，那边记的是 patch 模块名）
export DATABASE_PATH="$PWD/data/_probe_spec.db"
export UPLOAD_DIR="$PWD/data/_probe_uploads"
export OUTPUT_DIR="$PWD/data/_probe_outputs"
export LANCE_DB_PATH="$PWD/data/_probe_lance"
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8123 &   # 不加 --reload
D:/Python/python.exe scripts/probe_spec_ui.py f1   # f1…f11，分组见 CASES
```

**前置要点**：
- 全站受 `AuthMiddleware` 保护，runner 会先登录（`PROBE_USER`/`PROBE_PASS` 可覆盖，
  默认取 `scripts/create_admin.py` 的默认账号）。
- **用例自带前置**：不依赖副本库里手工点过的状态；条文数从页面上现数，不写死。
- 改静态 js/css 后，跑之前确认 `base.html` 的 `?v=N` 已递增（缓存会让探针读到旧文件）。
- 跑完清理：杀掉 8123 进程，并删除 `data/_probe_spec.db` 与
  `data/_probe_uploads` / `data/_probe_outputs` / `data/_probe_lance` 四个隔离目录。
- **视口宽度会影响结论**（见 memory: ui-isolated-verification）：默认 1600×900，
  窄视口相关用例请自行在用例内 resize 并在 finally 还原。

用法（单组）：
  D:/Python/python.exe scripts/probe_spec_ui.py f1
"""
import json
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


# ═══════════════════════════════════════════
# f6：导入弹窗（回填清空 / 关闭方式 / 导入中关闭确认）
# ═══════════════════════════════════════════

def _open_import_dialog(page):
    page.goto(f"{BASE}/")
    page.wait_for_selector(".left-panel-content", timeout=15000)
    page.click("button:has-text('导入规范')")
    page.wait_for_selector(".dialog-box--closable", state="visible", timeout=10000)


def _set_file(page, name):
    """把文件框设成「只有文件名、内容无意义」的假文件——识别只看文件名。"""
    page.set_input_files("input[name=file]", {
        "name": name, "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n",
    })


def _field(page, name):
    return page.input_value(f"input[name={name}]")


def f6_1_autofill_rewrites_then_clears_inputs(page):
    """匹配 → 回填；不匹配 → **两框清空（含用户手输内容）**；再换回匹配 → 重新回填。

    走**真实后端**（POST /import/parse-filename），故同时验收文件名识别增强的收益：
    `CJJ2-2008城市桥梁工程施工与质量验收规范.pdf` 现应识别出 `CJJ 2-2008`。
    """
    _open_import_dialog(page)

    _set_file(page, "GB 50010-2010 混凝土结构设计规范.pdf")
    assert poll_until(page, lambda: _field(page, "code") == "GB 50010-2010", 5000), \
        f"匹配文件未回填编号：{_field(page, 'code')!r}"
    assert _field(page, "title") == "混凝土结构设计规范"

    # 1 位序号的识别增强（走真实接口，防「单测过但接口没接上」）
    _set_file(page, "CJJ2-2008城市桥梁工程施工与质量验收规范.pdf")
    assert poll_until(page, lambda: _field(page, "code") == "CJJ 2-2008", 5000), \
        f"1 位序号未识别回填：{_field(page, 'code')!r}"

    # 用户报的 bug 场景：先手改（模拟用户录入），再选一个识别不了的文件
    page.fill("input[name=code]", "手工输入的编号")
    assert _field(page, "code") == "手工输入的编号"
    _set_file(page, "新建文档.pdf")
    assert poll_until(
        page, lambda: _field(page, "code") == "" and _field(page, "title") == "", 5000), \
        (f"不匹配时未清空（用户裁定：一律清空，含手输内容）："
         f"code={_field(page, 'code')!r} title={_field(page, 'title')!r}")

    # 清空后「开始导入」**仍可点**（文件还选着）—— 这正是「不清空就会拿错编号导入」的实证
    submit = page.locator(".dialog-box--closable button[type=submit]")
    assert submit.is_enabled(), "清空后提交按钮却禁用了（与 hasFile 语义不符）"

    # 再换回匹配文件 → 重新回填
    _set_file(page, "GB 50010-2010 混凝土结构设计规范.pdf")
    assert poll_until(page, lambda: _field(page, "code") == "GB 50010-2010", 5000), \
        "再次选择匹配文件未重新回填"


def f6_2_overlay_click_does_not_close_dialog(page):
    """点遮罩外部不再关闭弹窗（防误触）；点右上角 × 才关。"""
    _open_import_dialog(page)
    box = page.locator(".dialog-box--closable")
    bb = box.bounding_box()
    assert bb, "取不到弹窗位置，无法定位遮罩上的点击点"
    # 取「弹窗左缘再往左 60px、与弹窗同一水平中线」的点：确保落在遮罩上而不在弹窗内
    page.mouse.click(max(5.0, bb["x"] - 60), bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(300)
    assert box.is_visible(), "点击遮罩外部竟关闭了弹窗（防误触改造失效）"

    page.click(".dialog-close")
    assert poll_until(page, lambda: not box.is_visible(), 3000), "点右上角 × 未关闭弹窗"


# 与 import_routes 的真实轮询态响应同形（关键是有 hx-get="/import/progress/…"，
# import.js 靠这个字符串判断「已进入后台轮询」从而保持 uploading=true）
_PROGRESS_STUB = (
    '<div id="import-status" hx-get="/import/progress/stub0001" '
    'hx-trigger="every 2s" hx-swap="outerHTML">处理中...</div>'
)


def f6_3_close_during_import_confirms_and_keeps_progress(page):
    """导入进行中点 × → 弹确认；取消不关；确认关掉后重开进度仍在、按钮未解禁。

    **后端被桩掉**（`page.route` 拦截 /import/upload 与 /import/progress/**）：真实导入要走
    解析 / 分类 / 向量索引（重、且依赖外部服务），不适合放进回归探针。本用例验的是**前端契约**：
    关窗确认、关窗后 `#import-result` 未被清空（这正是「重开能看到进度」的机制）、
    `uploading` 未被复位（否则「开始导入」提前解禁 → 重复导入）。
    """
    _open_import_dialog(page)
    page.route("**/import/upload", lambda r: r.fulfill(
        status=200, content_type="text/html", body=_PROGRESS_STUB))
    page.route("**/import/progress/**", lambda r: r.fulfill(
        status=200, content_type="text/html", body=_PROGRESS_STUB))
    try:
        _set_file(page, "GB 50010-2010 混凝土结构设计规范.pdf")
        page.click(".dialog-box--closable button[type=submit]")
        assert poll_until(
            page, lambda: "import-status" in page.locator("#import-result").inner_html(), 5000), \
            "上传后未进入轮询态（桩未生效？）"

        # 1) 导入中点 × → 弹确认；取消 → 弹窗仍在
        msgs = []
        page.once("dialog", lambda d: (msgs.append(d.message), d.dismiss()))
        page.click(".dialog-close")
        page.wait_for_timeout(300)
        assert msgs, "导入进行中点 × 未弹确认框"
        assert "后台" in msgs[0], f"确认文案未说明任务在后台继续：{msgs[0]!r}"
        assert page.locator(".dialog-box--closable").is_visible(), "取消确认后弹窗却关了"

        # 2) 确认 → 关闭；重开 → 进度内容仍在，且「开始导入」仍禁用
        page.once("dialog", lambda d: d.accept())
        page.click(".dialog-close")
        assert poll_until(
            page, lambda: not page.locator(".dialog-box--closable").is_visible(), 3000), \
            "确认后弹窗未关闭"
        page.click("button:has-text('导入规范')")
        page.wait_for_selector(".dialog-box--closable", state="visible", timeout=5000)
        assert "import-status" in page.locator("#import-result").inner_html(), \
            "重开弹窗后进度内容丢失（关窗不得清空 #import-result）"
        assert page.locator(".dialog-box--closable button[type=submit]").is_disabled(), \
            "重开弹窗后「开始导入」已解禁 —— 导入进行中可再次上传＝重复导入隐患"
    finally:
        page.unroute("**/import/upload")
        page.unroute("**/import/progress/**")


CASES["f6"] = [f6_1_autofill_rewrites_then_clears_inputs,
               f6_2_overlay_click_does_not_close_dialog,
               f6_3_close_during_import_confirms_and_keeps_progress]


# ═══════════════════════════════════════════
# f7：导入判重（编号/名称命中库中已有规范）
# 依赖副本库里存在 `JGJ 107-2016`（本仓 dev 库/副本库都有）——
# 用例内先断言它存在，缺了就直接报错，不静默空跑。
# ═══════════════════════════════════════════

_DUP_FILE = "JGJ107-2016钢筋机械连接技术规程.pdf"   # 文件名能识别出库中已有的编号
_DUP_CODE = "JGJ 107-2016"


def _dup_warn_visible(page):
    warn = page.locator(".import-dup-warn")
    return warn.count() > 0 and warn.first.is_visible()


def _dup_warn_text(page):
    warn = page.locator(".import-dup-warn")
    return warn.first.inner_text() if warn.count() else ""


def f7_1_select_file_shows_duplicate_warning(page):
    """选一个编号已在库中的文件 → 弹窗内出现判重提示（真实后端，不桩）"""
    _open_import_dialog(page)
    _set_file(page, _DUP_FILE)
    assert poll_until(page, lambda: _dup_warn_visible(page), 6000), \
        f"选文件后未出现判重提示（{_DUP_FILE}）"
    txt = _dup_warn_text(page)
    assert _DUP_CODE in txt, f"提示未列出命中项：{txt!r}"
    assert "编号相同" in txt, f"提示未说明命中原因：{txt!r}"


def f7_2_manual_typing_also_triggers_duplicate_check(page):
    """**手输编号**也必须触发判重（防抖）。

    这条最容易被漏：文件名识别失败时表单会清空字段让用户手输（见 f6_1），
    那条路径若漏查，最常见的重复导入场景就完全没有提示。
    """
    _open_import_dialog(page)
    page.fill("input[name=code]", _DUP_CODE)
    assert poll_until(page, lambda: _dup_warn_visible(page), 6000), \
        "手输编号后未出现判重提示（markManual 未接查重？）"


def f7_3_warning_follows_current_value(page):
    """提示随当前值变化：改成库里没有的编号后必须消失（防「提示常亮」）"""
    _open_import_dialog(page)
    page.fill("input[name=code]", _DUP_CODE)
    assert poll_until(page, lambda: _dup_warn_visible(page), 6000), "前置：未出现判重提示"
    page.fill("input[name=code]", "GB 99999-2099")
    assert poll_until(page, lambda: not _dup_warn_visible(page), 6000), \
        "改成库中不存在的编号后判重提示仍未消失（提示不随值变化＝形同虚设）"


def f7_4_duplicate_submit_requires_confirmation(page):
    """命中时提交要先确认：取消 → 不发请求；确认 → 请求带 dup_confirmed=true。

    **upload 被桩掉**：真导入要走 OCR/解析/向量索引（重且依赖外部）。本用例验的是
    前端契约与请求载荷（是否携带确认标记），不验导入本身。
    """
    _open_import_dialog(page)
    sent = []
    page.route("**/import/upload", lambda r: (
        sent.append(r.request.post_data or ""),
        r.fulfill(status=200, content_type="text/html",
                  body='<div id="import-status">处理中...</div>'),
    ))
    try:
        _set_file(page, _DUP_FILE)
        assert poll_until(page, lambda: _dup_warn_visible(page), 6000), \
            "前置：选文件后未出现判重提示"

        # 1) 取消确认 → 不得发出请求
        page.once("dialog", lambda d: d.dismiss())
        page.click(".dialog-box--closable button[type=submit]")
        page.wait_for_timeout(600)
        assert not sent, "取消确认后仍发出了导入请求（判重被绕过）"

        # 2) 确认 → 请求发出且带 dup_confirmed=true
        page.once("dialog", lambda d: d.accept())
        page.click(".dialog-box--closable button[type=submit]")
        assert poll_until(page, lambda: len(sent) > 0, 6000), "确认后未发出导入请求"
        body = sent[0]
        assert 'name="dup_confirmed"' in body, f"请求未携带 dup_confirmed：{body[:400]!r}"
        after = body.split('name="dup_confirmed"')[1][:60]
        assert "true" in after, f"dup_confirmed 未置为 true：{after!r}"
    finally:
        page.unroute("**/import/upload")


CASES["f7"] = [f7_1_select_file_shows_duplicate_warning,
               f7_2_manual_typing_also_triggers_duplicate_check,
               f7_3_warning_follows_current_value,
               f7_4_duplicate_submit_requires_confirmation]


# ═══════════════════════════════════════════
# f8：导入进度跨页恢复（常驻小浮标）
# ═══════════════════════════════════════════
# 背景：导入进度原本只活在**当前文档**的 DOM 里（#import-status 的 hx-get 轮询）。
# 弹窗用 x-show，所以关弹窗不销毁 DOM、轮询照跑——「关掉还能回来看」因此成立；
# 但一旦整页跳转，文档销毁、task_id 无处可寻：服务端台账（import_tasks）还在，
# 客户端却再没有把手，于是「导入期间不能干别的事」。
# 修复：base.html 全局加载 import-tracker.js，用 sessionStorage 记住 task_id，
# 在任意页面恢复为常驻小浮标。
#
# ⚠ 用例一律用 page.route 桩掉 /json 端点。真实导入是分钟级且依赖 OCR/向量模型，
# 无法在用例里稳定制造「进行中」那一瞬间；而被测对象本就是浮标的状态机。
# 桩掉后四个状态可确定性驱动。四条用例全部以「跨页」为现场（先落到 /specs 预置
# task_id，再跳到别页），因为跨页恢复正是本组的主题。

# 导入弹窗与其内进度落点的选择器。
# ⚠ 不能用 .dialog-overlay：规则页的新建/编辑弹窗共用该类，strict 模式会因 3 个匹配报错；
# 裸 #import-result 在语料库页也存在。
_IMPORT_DIALOG = ".dialog-box--closable"
_IMPORT_RESULT = ".dialog-box--closable #import-result"
_IMPORT_STATUS = "#import-status"
FLOATER = "#import-floater"
TASK_KEY = "importTaskId"
_JSON_ROUTE = "**/import/progress/*/json"


def _stub_import_progress(page, payload):
    page.route(_JSON_ROUTE, lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)))


def _seed_task(page, task_id="deadbeef"):
    """预置 sessionStorage 里的 task_id —— 等价于「上传成功后用户切走」的现场

    必须落在**另一个**页面上再断言：要验的正是跨页恢复。
    """
    page.goto(f"{BASE}/specs")
    page.evaluate("(id) => sessionStorage.setItem('importTaskId', id)", task_id)
    page.goto(f"{BASE}/rules")


def _wait_floater(page, timeout_ms=8000):
    return poll_until(page, lambda: page.locator(FLOATER).count() > 0, timeout_ms)


def _wait_floater_gone(page, timeout_ms=8000):
    return poll_until(page, lambda: page.locator(FLOATER).count() == 0, timeout_ms)


def f8_1_floater_survives_navigation(page):
    """进行中：切页后浮标仍在并显示实时百分比（二次切页也要在）"""
    _stub_import_progress(page, {"status": "processing", "progress": 45,
                                 "message": "正在生成向量...", "needs_review": False})
    try:
        _seed_task(page)
        assert _wait_floater(page), "切页后未出现导入进度浮标"
        text = page.locator(FLOATER).inner_text()
        assert "45" in text, f"浮标未显示百分比：{text!r}"
        # 再切一次：这才是「导入期间不能干别的事」的原痛点，一次切页不足以证明可反复
        page.goto(f"{BASE}/review")
        assert _wait_floater(page), "二次切页后浮标丢失"
        assert "45" in page.locator(FLOATER).inner_text()
    finally:
        page.unroute(_JSON_ROUTE)


def f8_2_review_needed_offers_entry_and_keeps_task(page):
    """待审查：浮标给出「去审查」入口，且不清 task_id（否则入口转瞬即逝）

    审查页只在 task 仍是 review_needed 时可进入，用户切走后若没有这个入口，
    那次导入的成果就没人知道去哪找。
    """
    _stub_import_progress(page, {"status": "review_needed", "progress": 50,
                                 "message": "等待审查", "needs_review": True})
    try:
        _seed_task(page)
        assert _wait_floater(page), "待审查时未出现浮标"
        assert page.locator(f'{FLOATER} a[href*="/import/review/deadbeef"]').count() == 1, \
            f"浮标缺少去审查入口：{page.locator(FLOATER).inner_text()!r}"
        assert page.evaluate(f"() => sessionStorage.getItem('{TASK_KEY}')") == "deadbeef", \
            "待审查时被清掉 task_id：切页后入口会消失"
    finally:
        page.unroute(_JSON_ROUTE)


def f8_3_terminal_state_clears_floater_and_task(page):
    """终态 done：撤下浮标并清 task_id（不清的话每个页面都会重新拉起浮标）"""
    _stub_import_progress(page, {"status": "done", "progress": 100,
                                 "message": "导入完成", "needs_review": False})
    try:
        _seed_task(page)
        assert _wait_floater_gone(page), "终态后浮标未撤下"
        assert page.evaluate(f"() => sessionStorage.getItem('{TASK_KEY}')") is None, \
            "终态后未清 task_id"
    finally:
        page.unroute(_JSON_ROUTE)


def f8_4_unknown_task_clears_floater(page):
    """未知任务（不存在或已被超期清理）属**正常**路径：浮标须自清

    否则每次开页面都会拉起一个永远 0% 的浮标，并无限轮询一个不存在的任务。
    """
    _stub_import_progress(page, {"status": "unknown", "progress": 0,
                                 "message": "未知任务", "needs_review": False})
    try:
        _seed_task(page)
        assert _wait_floater_gone(page), "未知任务时浮标未自清"
        assert page.evaluate(f"() => sessionStorage.getItem('{TASK_KEY}')") is None, \
            "未知任务时未清 task_id"
    finally:
        page.unroute(_JSON_ROUTE)


def f8_5_floater_yields_to_open_import_dialog(page):
    """导入弹窗打开时浮标让位（弹窗内已有进度），关闭后恢复

    同屏两份进度会让人以为是两个任务；让位同时也省掉一条重复轮询。
    """
    _stub_import_progress(page, {"status": "processing", "progress": 45,
                                 "message": "正在生成向量...", "needs_review": False})
    try:
        _seed_task(page)
        assert _wait_floater(page), "前置：切页后浮标未出现"

        page.click("button:has-text('导入规范')")
        assert poll_until(page, lambda: page.locator(FLOATER).count() == 0, 6000), \
            "导入弹窗打开后浮标未让位"

        page.click(".dialog-box--closable .dialog-close")
        assert _wait_floater(page), "弹窗关闭后浮标未恢复"
    finally:
        page.unroute(_JSON_ROUTE)


def _floater_has(page, needle):
    return page.locator(FLOATER).count() > 0 and needle in page.locator(FLOATER).inner_text()


def _make_text_pdf(path):
    """造一份带**文本层**的极小 PDF

    ⚠ 必须是文本层而非扫描件：`is_scanned()` 以「提取文本 < 100 字符」判定，
    扫描件会走 OCR 分支（真实 API 请求，探针不能碰）；文本层则走 extract_text，
    Phase 1 直接停在 review_needed，且不进 Phase 2（不写库、不生成向量）。
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    text = ("1 总则\n"
            "1.0.1 本规范用于探针验收，不得作为工程依据。\n"
            "1.0.2 混凝土施工应符合设计要求，钢筋进场时应按标准检验。\n"
            "1.0.3 本条文字用于确保提取出的文本层超过一百个字符，"
            "从而被判定为非扫描件，走 extract_text 而不触发 OCR 请求。\n")
    page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)
    doc.close()


def _upload_probe_file(page, code, title, ext=".pdf"):
    """经导入弹窗**真实**上传一份极小文件（Phase 1 都会停在 review_needed）。

    ⚠️ **扩展名决定进哪个审查页**（P1 的入口分派，spec §20.2）：
      - `.pdf` ⇒ **版面块审查页**（左原文件 / 右块列表）。用文本层 PDF，
        `is_scanned()` 为假 ⇒ 走 `extract_text`，**不触发 OCR**。
      - `.md`  ⇒ **源码审查页**（左 md 源码 / 右预览）。
    故：验源码页（搜索/滚动/确认）用 `.md`；验版面块页用 `.pdf`。
    """
    import tempfile
    from pathlib import Path as _Path
    stem = code.replace(" ", "-")
    tmp_dir = _Path(tempfile.mkdtemp())
    if ext == ".md":
        src = tmp_dir / f"{stem}.md"
        src.write_text(
            "# 探针验收规范\n\n## 1 总则\n\n### 1.1 一般规定\n\n"
            "混凝土施工应符合设计要求，钢筋进场时应按标准检验。\n\n"
            "### 1.2 材料\n\n1.0.2 本条文字用于让源码审查页有足够内容可滚动与搜索。\n\n"
            "1.0.3 第二段用于验证多段渲染与替换。\n",
            encoding="utf-8")
    else:
        src = tmp_dir / f"{stem}.pdf"
        _make_text_pdf(str(src))
    _open_import_dialog(page)
    page.set_input_files("input[name=file]", str(src))
    page.fill("input[name=code]", code)
    page.fill("input[name=title]", title)
    page.click(".dialog-box--closable button[type=submit]")
    # 这一步即「响应头 X-Import-Task-Id → import-tracker → sessionStorage」整条接线
    assert poll_until(page, lambda: page.evaluate(
        "() => !!sessionStorage.getItem('importTaskId')"), 15000), \
        "上传后任务号未落到 sessionStorage（响应头 X-Import-Task-Id 未接通？）"
    return page.evaluate("() => sessionStorage.getItem('importTaskId')")


def _upload_probe_pdf(page, code, title):
    """兼容旧调用（f8_6 验响应头接线）—— 等价于上传文本层 PDF"""
    return _upload_probe_file(page, code, title, ext=".pdf")


def _wait_review_needed(page):
    """切页等任务停在「待审查」（浮标文案即状态机的对外表现）"""
    page.goto(f"{BASE}/specs")
    assert _wait_floater(page), "真实任务切页后浮标未出现"
    assert poll_until(page, lambda: _floater_has(page, "待审查"), 15000), \
        f"真实任务未停在待审查：{page.locator(FLOATER).inner_text()!r}"


def f8_6_real_upload_seeds_tracker_and_survives_navigation(page):
    """**真实链路**（不桩任何东西）：上传 PDF → 任务号落到 sessionStorage → 切页后浮标仍在

    前五条用例都桩掉了 /json 端点：它们能证明浮标状态机正确，却**证明不了它真的会被启动**
    —— 「响应头 X-Import-Task-Id → sessionStorage → 浮标」这条接线整段没被走过。
    本条补齐这一段，也顺带验收「任何 PDF 都停在 review_needed」这个前提。
    """
    try:
        _upload_probe_pdf(page, "PROBE 1.0", "探针验收规范")
        # 切页：真实任务的进度必须还能看见。整页跳转会重建 JS，弹窗让位状态随之复位
        _wait_review_needed(page)
        assert page.locator(f'{FLOATER} a[href*="/import/review/"]').count() == 1, \
            f"待审查浮标缺少入口：{page.locator(FLOATER).inner_text()!r}"

        # 切页后再点「导入规范」：弹窗必须把这个**真实**任务的进度恢复出来。
        # 这正是用户报的原始路径（中途退出 → 切页 → 再点导入），走真实端点不桩任何东西。
        page.click("button:has-text('导入规范')")
        assert poll_until(page, lambda: page.locator(_IMPORT_STATUS).count() > 0, 8000), \
            "切页后打开弹窗未恢复真实任务的进度"
        assert page.locator(f'{_IMPORT_RESULT} a[href*="/import/review/"]').count() == 1, \
            "恢复出来的待审查进度缺少去审查入口"
    finally:
        page.evaluate("() => sessionStorage.removeItem('importTaskId')")


# ═══════════════════════════════════════════
# f9：`~` 不得被当成删除线（规范正文里的区间号）
# ═══════════════════════════════════════════
# 背景：marked v15 的 GFM del 规则写作 `(~~?)` —— 第二个波浪线**可选**，
# 于是「单个 ~」成对出现就被吃成 <del>。规范正文里 ~ 是区间号
# （5~10~20、1.10~1.25），库内 153 条 content 含 ~，实测 11 条真会画出删除线。
# 而 PaddleOCR 的表格是原始 HTML，被 marked 整体透传、内部不解析行内 Markdown，
# 所以只有**正文段落**里的 ~ 会中招（CJJ1 的 ~ 全在表内，故它只在审查页现形）。
#
# 断言方式：直接调用**生产函数** window.mdRender.renderHtml（真实 md-render.js、
# 真实浏览器），而不是断言源码里有没有某个字符串——后者判不了行为。
# 也不依赖「副本库里恰好哪条条文含 ~」，故不会随数据漂移。

_SENTINEL = "\ue000"          # 私有区哨兵：修复用它对孤立 ~ 做占位


def _render(page, md):
    """在真实页面上跑一遍生产渲染管线，取渲染后的 HTML 与纯文本"""
    return page.evaluate("""
        (md) => {
          const el = document.createElement('div');
          el.innerHTML = window.mdRender.renderHtml(md, '');
          return { html: el.innerHTML, text: el.textContent };
        }
    """, md)


def f9_1_range_tildes_stay_literal(page):
    """区间号不得被吃成删除线（单区间成对 / 同段多区间）"""
    page.goto(f"{BASE}/specs")        # 任意页面即可：base.html 全局加载 md-render.js

    r = _render(page, "钢筋直径 5~10~20 mm")
    assert "<del>" not in r["html"], f"单个 ~ 成对仍被渲染成删除线：{r['html']!r}"
    assert "5~10~20" in r["text"], f"区间号被改写：{r['text']!r}"

    r2 = _render(page, "1 混凝土配合比宜为 1.10~1.25，水胶比 0.4~0.6。")
    assert "<del>" not in r2["html"], f"同段多个区间仍被吃：{r2['html']!r}"
    assert "1.10~1.25" in r2["text"] and "0.4~0.6" in r2["text"], r2["text"]


def f9_2_real_strikethrough_still_works(page):
    """真删除线 ~~…~~ 必须照旧（修复不得连坐误伤）"""
    page.goto(f"{BASE}/specs")
    r = _render(page, "~~这一段是删除线~~")
    assert "<del>" in r["html"], f"~~…~~ 不再渲染成删除线：{r['html']!r}"
    assert "删除线" in r["text"]


def f9_3_html_table_untouched(page):
    """OCR 的 HTML 表格必须原样保留：表格结构与其内部的 ~ 都不得被改"""
    page.goto(f"{BASE}/specs")
    r = _render(page, '<table><tr><td>5~10~20</td><td rowspan="2">x</td></tr></table>')
    assert "<table" in r["html"], f"HTML 表格被破坏：{r['html']!r}"
    assert 'rowspan="2"' in r["html"], f"表格属性被破坏：{r['html']!r}"
    assert "<del>" not in r["html"]
    assert "5~10~20" in r["text"]


def f9_4_sentinel_never_leaks_and_katex_still_runs(page):
    """哨兵必须还原干净，且公式内的 ~ 不影响 KaTeX

    还原必须发生在 katexize **之前**——否则 KaTeX 看到的是哨兵而不是 ~，
    公式会渲染失败（这是本修复最容易写错的一处顺序约束）。
    """
    page.goto(f"{BASE}/specs")
    r = _render(page, "电阻 $a~b$ 与区间 5~10~20 mm")

    assert _SENTINEL not in r["html"], f"哨兵字符泄漏到页面：{r['html']!r}"
    assert _SENTINEL not in r["text"], f"哨兵字符泄漏到文本：{r['text']!r}"
    assert "katex" in r["html"], f"公式未渲染（KaTeX 未运行）：{r['html']!r}"
    assert "katex-error" not in r["html"], f"公式渲染报错：{r['html']!r}"


CASES["f9"] = [f9_1_range_tildes_stay_literal,
               f9_2_real_strikethrough_still_works,
               f9_3_html_table_untouched,
               f9_4_sentinel_never_leaks_and_katex_still_runs]


# ═══════════════════════════════════════════
# f10：审查页「确认并继续导入」后立即滚到底
# ═══════════════════════════════════════════
# 需求原话：滚动动作要排在进度条加载**之前**，避免内容展示不全造成误判。
# 所以这是**顺序**断言，不是「最终滚到底了没」——后者在响应返回后才滚动也照样满足。
# 做法：在页面里挂钩 window.fetch，记录「发出 /confirm 请求那一刻」的滚动位置，
# 与响应快慢无关，也不需要 sleep 去赌时序。
#
# ⚠ 滚动容器是 `.center-panel-v2`，**不是 window**：`.app-layout` 是
# `height:100vh; overflow:hidden`，window 永不滚动（scrollY 恒为 0）。
# 断言错对象会得到一个永远"通过"的空转用例。

def _make_review_task(page, code, title, ext=".md"):
    """造一个真实待审查任务并回到干净状态，返回 task_id。

    ⚠️ **默认 `.md`**：调用方（f10 滚动 / f11 搜索）验的都是**源码审查页**，
    而 `.md` 正是该页的入口（spec §20.2）。验**版面块页**的 f12 显式传 `ext=".pdf"`。
    """
    task_id = _upload_probe_file(page, code, title, ext=ext)
    _wait_review_needed(page)
    page.evaluate("() => sessionStorage.removeItem('importTaskId')")
    return task_id


def _hook_confirm_scroll(page):
    """记录 /confirm 请求**发出那一刻**滚动容器底边的位置"""
    page.evaluate("""
        () => {
          const orig = window.fetch;
          window.__confirmAt = null;
          window.__confirmMax = null;
          window.fetch = function (url, opts) {
            if (String(url).includes('/confirm') && window.__confirmAt === null) {
              const s = document.querySelector('.center-panel-v2');
              window.__confirmAt = s ? s.scrollTop + s.clientHeight : -1;
              window.__confirmMax = s ? s.scrollHeight : -1;
            }
            return orig.apply(this, arguments);
          };
        }
    """)


def _click_confirm_programmatically(page):
    """程序化点击「确认并继续导入」

    ⚠ **不能用 page.click**：真实鼠标点击会先把按钮 focus 进视口，
    浏览器那次自动滚动会把容器带到接近底端，于是「滚动排在请求之前」这条断言
    会在**未实现任何滚动逻辑**时也通过（实测踩过：假绿）。程序化 click 不触发
    focus 滚动，测到的才是被测代码自己的行为。
    """
    page.evaluate("""
        () => {
          const b = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('确认并继续导入'));
          if (!b) throw new Error('未找到确认按钮');
          b.click();
        }
    """)


def f10_1_confirm_scrolls_to_bottom_before_request(page):
    """点「确认并继续导入」：请求发出时滚动容器已在底端（即滚动排在请求之前）

    隐含要求：滚动必须是**瞬时**的。若实现改用 smooth，滚动位置在请求发出时尚未到位，
    本条即失败——这与本仓「自动滚动改瞬时」的既定口径一致。
    """
    task_id = _make_review_task(page, "PROBE-SCROLL 1.0", "探针滚动用规范")
    route_pat = f"**/import/review/{task_id}/confirm"
    try:
        # 视口压小，确保容器必定溢出——否则断言在"本来就全看得见"时无意义
        page.set_viewport_size({"width": 1280, "height": 520})
        page.goto(f"{BASE}/import/review/{task_id}")
        page.wait_for_selector(".review-panels", timeout=15000)
        geo = page.evaluate("""
            () => { const s = document.querySelector('.center-panel-v2');
                    return { sh: s.scrollHeight, ch: s.clientHeight, sy: s.scrollTop }; }
        """)
        assert geo["sh"] > geo["ch"], f"前置不成立：审查页未溢出，断言会空转 {geo}"
        assert geo["sy"] == 0, f"前置不成立：初始不在顶部 {geo}"

        _hook_confirm_scroll(page)
        page.route(route_pat, lambda route: route.fulfill(
            status=200, content_type="text/html", body="<p>审查完成，正在继续导入...</p>"))
        _click_confirm_programmatically(page)
        assert poll_until(page, lambda: page.evaluate("() => window.__confirmAt !== null"),
                          8000), "未观察到 /confirm 请求发出"

        at = page.evaluate("() => window.__confirmAt")
        mx = page.evaluate("() => window.__confirmMax")
        assert at >= mx - 5, \
            f"请求发出时容器底边({at})不在底端({mx})：滚动被排在了请求之后"
    finally:
        page.unroute(route_pat)
        page.set_viewport_size({"width": 1600, "height": 900})


def f10_2_confirm_keeps_progress_fully_visible(page):
    """确认后插入的进度区必须**整体**落在视口内（而不是只露出上半截）

    用户实测：滚动确实发生了，但只到"进度条上半部分"，下半部分与后续提示行被折叠线挡住，
    仍需手动滚。**根因**：滚动发生在**响应插入之前**——那一刻 #review-result 还是空的，
    滚到的是旧内容的底部；响应插入后容器变高、而 scrollTop 没跟着走，新内容就落在
    折叠线以下。即「滚动排在进度条加载之前」这条字面要求，与「让用户看见进度」这个
    真实目的相冲突 —— 两者都要做：点击时先滚（即时反馈），**插入后再滚一次**（保证可见）。

    断言的是**几何**（进度区底边 ≤ 滚动容器底边），不是"滚动函数被调用了"。
    """
    task_id = _make_review_task(page, "PROBE-SCROLL2 1.0", "探针滚动用规范")
    route_pat = f"**/import/review/{task_id}/confirm"
    frag_pat = f"**/import/progress/{task_id}"
    try:
        page.set_viewport_size({"width": 1280, "height": 520})
        page.goto(f"{BASE}/import/review/{task_id}")
        page.wait_for_selector(".review-panels", timeout=15000)
        geo = page.evaluate("""() => { const s = document.querySelector('.center-panel-v2');
                                   return {sh: s.scrollHeight, ch: s.clientHeight}; }""")
        assert geo["sh"] > geo["ch"], f"前置不成立：审查页未溢出，断言会空转 {geo}"
        # 前置：结果区必须**已预留**高度（.review-result-slot ≥ 80px）。
        # 这条钉住的是设计决策本身——把预留样式删掉，本用例就会红，而不是静默退化。
        slot_h = page.evaluate("() => Math.round(document.getElementById('review-result').getBoundingClientRect().height)")
        assert slot_h >= 80, f"结果区未预留高度（当前 {slot_h}px），进度片段换入后必然被裁"

        # 高度**确定**的进度片段（便于对几何下断言），并把它同时挂到轮询端点上，
        # 免得 2s 后的真轮询把高度换掉、测量对象中途变样
        # 高度取**实测真实值**：真实 done 态片段 83px（见 .review-result-slot 的注释）
        body = (f'<div id="import-status" hx-get="/import/progress/{task_id}"'
                f' hx-trigger="every 2s" hx-swap="outerHTML" style="height:83px">'
                f'<p>审查完成，正在继续导入...</p></div>')
        page.route(route_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=body))
        page.route(frag_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=body))
        _click_confirm_programmatically(page)

        node = page.locator("#import-status")
        assert poll_until(page, lambda: node.count() > 0, 8000), "进度片段未插入"
        page.wait_for_timeout(400)          # 等插入后的布局稳定

        sc = page.locator(".center-panel-v2").bounding_box()
        nb = node.bounding_box()
        assert nb["y"] >= sc["y"] - 1, f"进度区顶部被裁：node={nb} scroller={sc}"
        assert nb["y"] + nb["height"] <= sc["y"] + sc["height"] + 1, (
            f"进度区底部被裁（用户报的『只露出上半截』）："
            f"进度区底={nb['y'] + nb['height']:.0f} 视口底={sc['y'] + sc['height']:.0f}")
    finally:
        page.unroute(route_pat)
        page.unroute(frag_pat)
        page.set_viewport_size({"width": 1600, "height": 900})


def f10_3_poll_swap_keeps_grown_fragment_visible(page):
    """轮询换入**更高**的进度片段后，整块仍要可见（不能只露上半截）

    这是用户原话里「刷新行仍被隐藏」的那一刻，与 f10_2 是**两个不同时刻**：
    f10_2 = 确认响应插入（此刻插入的是 processing 片段）；
    本条 = 约 2s 后轮询把片段换成终态片段（多一行「导入完成！刷新页面…」⇒ 变高）。
    真实流程实测：后者底边被裁 25px，正是那一行被挡住。

    修法要点：换入前若**已在底部**（跟随态）则换完贴回底部；用户主动往上滚过就不打扰。
    """
    task_id = _make_review_task(page, "PROBE-SCROLL3 1.0", "探针滚动用规范")
    confirm_pat = f"**/import/review/{task_id}/confirm"
    frag_pat = f"**/import/progress/{task_id}"
    try:
        page.set_viewport_size({"width": 1280, "height": 520})
        page.goto(f"{BASE}/import/review/{task_id}")
        page.wait_for_selector(".review-panels", timeout=15000)
        assert page.evaluate("""() => { const s = document.querySelector('.center-panel-v2');
                                         return s.scrollHeight > s.clientHeight; }"""), \
            "前置不成立：审查页未溢出，断言会空转"

        # 确认时回**较矮**的片段；轮询回**更高**的终态片段（多出"刷新页面"那一行）
        # 贴合真实：确认响应是**一行提示**（实测 22px），轮询换入的是**真实进度片段**
        # （实测 83px，三段式进度条 + 提示行 + 「刷新页面」行）
        short_body = (f'<div id="import-status" hx-get="/import/progress/{task_id}"'
                      f' hx-trigger="every 2s" hx-swap="outerHTML" style="height:22px">'
                      f'<p>审查完成，正在继续导入...</p></div>')
        tall_body = ('<div id="import-status" style="height:83px">'
                     '<p>导入完成！<a href="/">刷新页面</a>查看新导入的规范。</p></div>')
        page.route(confirm_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=short_body))
        page.route(frag_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=tall_body))

        _click_confirm_programmatically(page)

        def _swapped():
            n = page.locator(_IMPORT_STATUS)
            return n.count() > 0 and "刷新页面" in n.inner_text()

        assert poll_until(page, _swapped, 12000), "轮询未换入更高的终态片段"
        page.wait_for_timeout(300)          # 等布局稳定

        sc = page.locator(".center-panel-v2").bounding_box()
        nb = page.locator(_IMPORT_STATUS).bounding_box()
        assert nb["y"] + nb["height"] <= sc["y"] + sc["height"] + 1, (
            f"轮询换入更高的片段后底部被裁（用户报的『刷新行被隐藏』）："
            f"底={nb['y'] + nb['height']:.0f} 视口底={sc['y'] + sc['height']:.0f}")
    finally:
        page.unroute(confirm_pat)
        page.unroute(frag_pat)
        page.set_viewport_size({"width": 1600, "height": 900})


def f10_4_poll_swap_does_not_yank_user_back(page):
    """用户主动上滚看内容后，轮询不得把他拽回底部

    「跟随底部」只应发生在用户本来就在底部时。否则 Phase 2 那 10 秒里每次轮询都把人
    拽回去 —— 比原来的问题更烦人。这条钉住 watchProgressAutoScroll 里那个
    beforeSwap 取的跟随判定。
    """
    task_id = _make_review_task(page, "PROBE-SCROLL4 1.0", "探针滚动用规范")
    confirm_pat = f"**/import/review/{task_id}/confirm"
    frag_pat = f"**/import/progress/{task_id}"
    try:
        page.set_viewport_size({"width": 1280, "height": 520})
        page.goto(f"{BASE}/import/review/{task_id}")
        page.wait_for_selector(".review-panels", timeout=15000)

        short_body = (f'<div id="import-status" hx-get="/import/progress/{task_id}"'
                      f' hx-trigger="every 2s" hx-swap="outerHTML" style="height:22px">'
                      f'<p>审查完成，正在继续导入...</p></div>')
        tall_body = ('<div id="import-status" style="height:83px">'
                     '<p>导入完成！<a href="/">刷新页面</a>查看新导入的规范。</p></div>')
        page.route(confirm_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=short_body))
        page.route(frag_pat, lambda r: r.fulfill(status=200, content_type="text/html", body=tall_body))

        _click_confirm_programmatically(page)
        assert poll_until(page, lambda: page.locator(_IMPORT_STATUS).count() > 0, 8000), \
            "前置：进度片段未插入"

        # 用户主动上滚（模拟"想看上面的审查内容"），并等轮询换入更高的片段
        page.evaluate("""() => { document.querySelector('.center-panel-v2').scrollTop = 0; }""")
        page.wait_for_timeout(300)
        assert poll_until(page, lambda: "刷新页面" in page.locator(_IMPORT_STATUS).inner_text(), 12000), \
            "轮询未换入更高的片段"
        page.wait_for_timeout(600)          # 给 afterSettle 的滚动（若有）留出时间

        top = page.evaluate("() => Math.round(document.querySelector('.center-panel-v2').scrollTop)")
        assert top <= 4, f"用户上滚后被轮询拽回了底部：scrollTop={top}（应保持 ≈0）"
    finally:
        page.unroute(confirm_pat)
        page.unroute(frag_pat)
        page.set_viewport_size({"width": 1600, "height": 900})


CASES["f10"] = [f10_1_confirm_scrolls_to_bottom_before_request,
                f10_2_confirm_keeps_progress_fully_visible,
                f10_3_poll_swap_keeps_grown_fragment_visible,
                f10_4_poll_swap_does_not_yank_user_back]


# ═══════════════════════════════════════════
# f11：审查页搜索不得摧毁预览（高亮必须在已渲染的 DOM 上做）
# ═══════════════════════════════════════════
# 旧实现 `_highlightInPreview()`：把**整份 Markdown 做 HTML 转义**、插 <mark>、
# 再喂回渲染器重渲染。而 OCR 的表格是**原始 HTML**（<table rowspan=…>），
# 那一刀转义会把 <table> 打回 &lt;table&gt; 字面文本 —— 也就是搜索框里每敲一个
# 字符，预览里的表格就塌一次。附带代价：每敲一键整篇重渲染（大规范 46 万字符会卡）。
#
# 用例把夹具内容直接写进编辑器（走真实 input 事件 → Alpine x-model → updatePreview），
# 而不是依赖上传的 PDF 里恰好有什么 —— 文本层 PDF 抽出的是纯文本，本就没有表格。
# 上传那一步只是为了拿到一个能打开审查页的真实任务。

_TABLE_MD = (
    "## 7.5.2 水准测量\n\n"
    '<table><tr><td rowspan="2">控制等级</td><td>1000</td></tr>'
    "<tr><td>2000</td></tr></table>\n\n"
    "钢尺量距相对误差 1.10~1.25。\n"
)
_SEARCH_BOX = ".review-toolbar input[type=search]"
_REPLACE_BOX = ".review-toolbar input[type=text]"


def _open_review_with_content(page, md, wait_selector="#review-preview table"):
    """造一个待审查任务 → 打开审查页 → 把编辑器内容换成 md → 确认夹具已渲染

    wait_selector 由调用方给出「这个夹具应该渲染出什么」：等它出现才说明
    编辑器内容真的进了渲染管线，否则后续断言是在空页面上做（前置不成立须报错，
    不能静默空跑）。
    """
    task_id = _make_review_task(page, "PROBE-SEARCH 1.0", "探针搜索用规范")
    page.goto(f"{BASE}/import/review/{task_id}")
    page.wait_for_selector(".review-editor", timeout=15000)
    page.fill(".review-editor", md)
    assert poll_until(page, lambda: page.locator(wait_selector).count() > 0, 8000), \
        f"前置不成立：夹具内容未在预览里渲染出 {wait_selector}"
    return task_id


def f11_1_search_keeps_preview_tables_and_adds_marks(page):
    """搜索后：表格还在，且预览里有高亮

    修好之前这里会失败在第一条断言上——表格被整篇转义打成字面文本。
    """
    _open_review_with_content(page, _TABLE_MD)

    page.fill(_SEARCH_BOX, "1000")

    assert poll_until(page, lambda: page.locator(
        "#review-preview mark.search-highlight").count() >= 1, 5000), "搜索后预览里没有高亮"
    assert page.locator("#review-preview table").count() == 1, \
        "搜索后预览里的表格没了：整篇 HTML 转义把 <table> 打成了字面文本"
    assert page.locator("#review-preview mark.search-highlight").first.inner_text().strip() == "1000"


def f11_2_clearing_query_keeps_tables(page):
    """清空搜索框：高亮撤掉，但表格仍是表格（不得借"恢复渲染"顺手重渲染一遍）"""
    _open_review_with_content(page, _TABLE_MD)
    page.fill(_SEARCH_BOX, "1000")
    assert poll_until(page, lambda: page.locator(
        "#review-preview mark.search-highlight").count() >= 1, 5000), "前置：未出现高亮"

    page.fill(_SEARCH_BOX, "")

    assert poll_until(page, lambda: page.locator(
        "#review-preview mark.search-highlight").count() == 0, 5000), "清空后高亮未撤掉"
    assert page.locator("#review-preview table").count() == 1, "清空搜索后表格没了"


def f11_3_replace_all_treats_replacement_literally(page):
    """「替换为」里的 $& / $1 必须按**字面**插入，不能被当成 JS 替换模式

    `String.replace(re, this.replaceText)` 会把替换串里的 `$&` 解释为"整个匹配"，
    于是用户输入 `$&X` 会插进「甲X」这种意料之外的内容。
    """
    _open_review_with_content(page, "甲种材料与乙种材料\n", wait_selector="#review-preview p")
    page.fill(_SEARCH_BOX, "甲")
    page.fill(_REPLACE_BOX, "$&X")
    page.click("button:has-text('全部替换')")

    val = page.input_value(".review-editor")
    assert "$&X" in val, f"替换值被当成替换模式展开：{val!r}"


def f11_4_next_prev_keep_counter_and_table(page):
    """上一个/下一个仍按源码偏移定位（计数更新），且不破坏预览

    这两个动作原先会顺带重渲染预览；改成 DOM 高亮后它们只滚动编辑框、不重渲染，
    顺带也去掉了「重复高亮会把 <mark> 再包一层」的隐患。这条把它们钉住，
    免得日后改高亮时顺手改坏翻页。
    """
    _open_review_with_content(page, _TABLE_MD)          # 表格单元格：1000 / 2000
    page.fill(_SEARCH_BOX, "00")                        # 两处命中
    info = page.locator(".review-toolbar small").first
    assert poll_until(page, lambda: "2 个匹配" in info.inner_text(), 5000), \
        f"匹配计数不对：{info.inner_text()!r}"

    page.click("button:has-text('下一个')")

    assert poll_until(page, lambda: info.inner_text().strip() == "1/2", 5000), \
        f"翻页计数未更新：{info.inner_text()!r}"
    assert page.locator("#review-preview table").count() == 1, "翻页把预览里的表格弄丢了"
    assert page.locator("#review-preview mark.search-highlight").count() >= 2, \
        "翻页后高亮数量不对（全部匹配应保持高亮）"


CASES["f11"] = [f11_1_search_keeps_preview_tables_and_adds_marks,
                f11_2_clearing_query_keeps_tables,
                f11_3_replace_all_treats_replacement_literally,
                f11_4_next_prev_keep_counter_and_table]


def _stub_progress_processing(page, pct=45, msg="正在 OCR 识别..."):
    _stub_import_progress(page, {"status": "processing", "progress": pct,
                                 "message": msg, "needs_review": False})


def f8_7_clicking_floater_opens_import_dialog(page):
    """点浮标（导入中状态）→ 打开导入弹窗并显示该任务进度

    用户原话：「点击浮窗无响应，没有进入导入界面」。浮标在导入中状态原先是个纯
    <span>，不可点 —— 而它恰恰是用户唯一的进度线索，必须给出去处。
    """
    _stub_progress_processing(page)
    try:
        _seed_task(page)
        assert _wait_floater(page), "前置：浮标未出现"
        page.click(FLOATER)
        assert poll_until(page, lambda: page.locator(_IMPORT_DIALOG).is_visible(), 5000), \
            "点浮标未打开导入弹窗"
        assert poll_until(page, lambda: page.locator(_IMPORT_STATUS).count() > 0, 8000), \
            "弹窗打开了但里面没有该任务的进度"
    finally:
        page.unroute(_JSON_ROUTE)


def _stub_progress_fragment(page, task_id, pct, message="正在 OCR 识别…"):
    """桩掉进度 **HTML 片段**端点（/import/progress/{id}）

    恢复进弹窗走的是这个端点，不是 /json —— 两者都要桩，否则未桩的那个打到真实服务
    会返回「未知任务」，用例就变成了在验服务端的"任务不存在"分支（实测踩到）。
    """
    body = (f'<div id="import-status" hx-get="/import/progress/{task_id}"'
            f' hx-trigger="every 2s" hx-swap="outerHTML"><p>{message}（{pct}%）</p></div>')
    page.route(f"**/import/progress/{task_id}",
               lambda r: r.fulfill(status=200, content_type="text/html", body=body))


def f8_8_opening_dialog_resumes_running_task(page):
    """切页后点「导入规范」→ 弹窗恢复进行中任务的进度，且**重新挂上轮询**

    这才是「中途退出导入界面 → 切页 → 再点导入」的原始路径：整页跳转销毁了弹窗里
    原有的 #import-status，不恢复的话用户只看到一个空表单，以为任务丢了。

    断言落在**行为**上（恢复出来的节点带着正确的 hx-get/hx-trigger，即轮询会继续），
    而不是"片段里出现某个数字"——后者验的是夹具自己写的字符串。
    """
    _stub_progress_processing(page)
    _stub_progress_fragment(page, "deadbeef", 45)
    try:
        _seed_task(page)
        assert _wait_floater(page), "前置：浮标未出现"
        page.click("button:has-text('导入规范')")
        node = page.locator(_IMPORT_STATUS)
        assert poll_until(page, lambda: node.count() > 0, 8000), \
            "打开弹窗后未恢复进行中任务的进度"
        assert node.get_attribute("hx-get") == "/import/progress/deadbeef", \
            f"恢复出来的节点指向错误的端点：{node.get_attribute('hx-get')!r}"
        assert node.get_attribute("hx-trigger") == "every 2s", "恢复出来的节点不会再轮询"
        assert "正在 OCR" in page.locator(_IMPORT_RESULT).inner_text(), "恢复的片段没落进弹窗"
    finally:
        page.unroute(_JSON_ROUTE)
        page.unroute("**/import/progress/deadbeef")


def f8_9_open_import_query_param_opens_dialog(page):
    """/?open_import=1 自动打开弹窗

    审查页隐藏了左栏（hide_tree），那里没有导入弹窗可点 → 浮标只能跳回首页再打开，
    本条覆盖该回退路径。
    """
    _stub_progress_processing(page)
    try:
        page.goto(f"{BASE}/specs")
        page.evaluate("(id) => sessionStorage.setItem('importTaskId', id)", "deadbeef")
        page.goto(f"{BASE}/?open_import=1")
        assert poll_until(page, lambda: page.locator(_IMPORT_DIALOG).is_visible(), 6000), \
            "带 open_import=1 打开首页未自动弹出导入弹窗"
    finally:
        page.unroute(_JSON_ROUTE)


CASES["f8"] = [f8_1_floater_survives_navigation,
               f8_2_review_needed_offers_entry_and_keeps_task,
               f8_3_terminal_state_clears_floater_and_task,
               f8_4_unknown_task_clears_floater,
               f8_5_floater_yields_to_open_import_dialog,
               f8_6_real_upload_seeds_tracker_and_survives_navigation,
               f8_7_clicking_floater_opens_import_dialog,
               f8_8_opening_dialog_resumes_running_task,
               f8_9_open_import_query_param_opens_dialog]


# ═══════════════════════════════════════════
# f12：版面块审查页（P1 只读对照）
# ═══════════════════════════════════════════
#
# ⚠️ `.md` / 非扫描件走 extract_text，**不产生 layout.json**（P0 的产物覆盖边界表），
#    而 `_make_review_task` 上传的正是文本 PDF ⇒ 天生没有 layout。
#    探针**不调 OCR**：用 `_make_review_task` 造真任务，再把参考夹具改写成它的
#    `md_text` + `layout.json`，并让原文件变成 2 页（使跨页联动可验）。
#    页尺寸取**夹具真实的 pages**（否则 bbox 落在图外，高亮框画不出来）。
#    夹具在 data/_ocr_reference/，不入库；缺则整组跳过。

_RVB = ".rvb-root"
# 取 10 页：前两页只有 11 个块（封面 + 编制名单），样本太小验不出列表行为；
# 10 页约 110 块（实测 57 页 650 块，约 11.4 块/页）。
_RVB_PAGES = 10


def _rvb_make_pdf(path, pages):
    """按给定页尺寸造多页文本 PDF（≥100 字符 ⇒ 非扫描件 ⇒ 不触发 OCR）"""
    import fitz
    doc = fitz.open()
    for p in pages:
        doc.new_page(width=p["width"], height=p["height"]).insert_text(
            (72, 100), f"Probe page {p['index']} " + "x" * 200, fontsize=10)
    doc.save(str(path))
    doc.close()


# f14 专用合成表：**md 侧带样式、块侧去样式**（复刻真实产物的两种序列化），
# 且**含一张内嵌图片** —— 出口判据「行列被当成图片塞进合并格」要用到它。
_RVBT_MD = ("\n\n<table border=1 style='margin: auto; word-wrap: break-word;'>"
            "<tr><td style='text-align: center;'>序号</td>"
            "<td style='text-align: center;'>图 形</td>"
            "<td style='text-align: center;'>说 明</td></tr>"
            "<tr><td style='text-align: center;'>1</td>"
            "<td style='text-align: center;'><img src=\"imgs/t.jpg\" /></td>"
            "<td style='text-align: center;'>探针表格</td></tr></table>")
_RVBT_CONTENT = ('<table><tr><td>序号</td><td>图 形</td><td>说 明</td></tr>'
                 '<tr><td>1</td><td><img src="imgs/t.jpg" /></td>'
                 '<td>探针表格</td></tr></table>')


def _rvb_seed(page, suffix, extra_md="", with_table=False):
    """造一个**带 layout.json** 的待审查任务并打开审查页；前置不成立返回 None。

    `extra_md` / `with_table` 均为 **opt-in**：默认与原先逐字一致（不影响 f12/f13 的夹具计数）。
    `with_table=True` 时追加一张**合成表格**（md 原文 + 对应块），供 f14 使用 ——
    真实夹具里唯一的 table 块在第 29 页，而本探针只切到 `_RVB_PAGES` 页，会被过滤掉。
    """
    import json as _json
    from pathlib import Path as _Path

    # 本探针其余各组只驱动浏览器、从不 import 项目模块；f12 要写 layout.json，
    # 故须自行把仓库根加进 sys.path（探针是以脚本方式跑的，没有包上下文）。
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from app.config import OUTPUT_DIR, UPLOAD_DIR
    from app.database import get_db
    from app.ocr.layout import (align_clauses, anchor_blocks, base_sha256,
                                extract_layout, save_layout)

    ref = _Path(__file__).resolve().parent.parent / "data" / "_ocr_reference"
    if not (ref / "raw.jsonl").is_file():
        return None

    md = (ref / "reference.md").read_text(encoding="utf-8")
    if extra_md:
        md += extra_md
    lay = extract_layout([_json.loads(l) for l in
                          (ref / "raw.jsonl").read_text(encoding="utf-8").split("\n")
                          if l.strip()])
    pages = [p for p in lay["pages"] if p["index"] <= _RVB_PAGES]
    anchored, _ = anchor_blocks(md, lay["blocks"])
    aligned, _ = align_clauses(md, anchored)
    aligned = [b for b in aligned if (b.get("page") or 1) <= _RVB_PAGES]
    # ★ 两块「门槛隔离」样本（Task 6 评审对真实夹具实测逼出，必须自造）：
    #   实测切片 76 块 = 66 个 白名单∧有 span + 10 个 number（既非白名单又无 span），
    #   **没有一块只违反一个条件** ⇒ 单独删掉 canEdit 任一条，f13_7 都照样绿。
    #   追加在**末尾** ⇒ 既有块下标不变、_rvb_first_editable 仍返回 0；
    #   bbox=None ⇒ 不画高亮框，不干扰左栏用例。
    aligned.append({"label": "text", "content": "白名单但无 span", "page": 1,
                    "bbox": None, "order": 9998, "global_block_id": 9998,
                    "start_line": None, "end_line": None, "prefix": "", "suffix": "",
                    "clause_no": None})
    # ⚠️ span 必须取 **(1, 2)**，不能取 (0, 1)（Task 8 实现者离线复现 + 控制者复核）：
    #   真实块 0（`## 前言`）的区间**也是** [0, 1) ⇒ 与之重叠 ⇒ `splice` 抛
    #   `ValueError: layout 区间重叠：[0, 1) 与游标 1` ⇒ **compose/preview 对该夹具一律 500**。
    #   第 1 行是空行，取 (1, 2) 实测：splice 通过、空 edits 逐字节等价、基线仍 138 条、
    #   该样本仍有 span 且非白名单 ⇒ 隔离语义不变。
    aligned.append({"label": "display_formula", "content": "非白名单但有 span", "page": 1,
                    "bbox": None, "order": 9999, "global_block_id": 9999,
                    "start_line": 1, "end_line": 2, "prefix": "", "suffix": "",
                    "clause_no": None})

    if with_table:
        md = md + _RVBT_MD
        _lines = md.split("\n")
        try:
            _ts = next(i for i, l in enumerate(_lines)
                       if l.lstrip().startswith("<table") and "探针表格" in l)
        except StopIteration:
            return None
        # gid 取 10000（比上面两个合成块大）⇒ 阅读顺序排在最后，与 md 行号递增一致
        aligned.append({"label": "table", "content": _RVBT_CONTENT, "page": 1,
                        "bbox": None, "order": 10000, "global_block_id": 10000,
                        "start_line": _ts, "end_line": _ts + 1, "prefix": "", "suffix": "",
                        "clause_no": None, "md_html": _lines[_ts]})

    task_id = _make_review_task(page, f"PROBE-RVB {suffix}", f"探针块审查 {suffix}",
                                ext=".pdf")
    # 原文件换成 2 页（页尺寸与 layout.pages 一致，否则 page/2.png 404 或框画歪）
    _rvb_make_pdf(_Path(UPLOAD_DIR) / f"{task_id}.pdf", pages)
    with get_db() as conn:
        conn.execute("UPDATE import_tasks SET md_text=?, file_name=? WHERE task_id=?",
                     (md, "probe_block.pdf", task_id))

    out = _Path(OUTPUT_DIR) / task_id
    out.mkdir(parents=True, exist_ok=True)
    save_layout({
        "version": 1,
        "pages": pages,
        "blocks": aligned,
        "base_sha256": base_sha256(md),
        "stats": {"anchorable": len(aligned), "anchored": len(aligned), "missed": 0},
    }, out)

    page.goto(f"{BASE}/import/review/{task_id}")
    assert poll_until(page, lambda: page.locator(_RVB).count() > 0, 10000), \
        "审查页未渲染出版面块组件（组件或端点有问题）"
    assert poll_until(page, lambda: page.locator(".rvb-block").count() > 0, 10000), \
        "块列表为空"
    return task_id


def f12_1_left_shows_page_image(page):
    """左栏渲染出页图，且尺寸为正（**前置**：没有图后面全是空跑）"""
    assert _rvb_seed(page, "1") is not None, "前置不成立：缺少参考夹具"
    img = page.locator(".rvb-img").first
    assert img.count() == 1, "左栏没有页图"
    box = img.bounding_box()
    assert box and box["width"] > 50 and box["height"] > 50, f"页图尺寸异常：{box}"


def f12_2_zoom_modes_change_zoom_and_reset_returns_to_fit(page):
    """4 档缩放各生效；**「重置」回到适应宽度**（不是刷新 —— spec §6.2 明确）"""
    assert _rvb_seed(page, "2") is not None, "前置不成立"
    fit_w = page.evaluate("() => document.querySelector('.rvb-canvas').offsetWidth")
    page.click("button:has-text('200%')")
    assert poll_until(page, lambda: page.evaluate(
        "() => document.querySelector('.rvb-canvas').offsetWidth") > fit_w * 1.5, 5000), \
        "点 200% 后画布没变宽"
    w200 = page.evaluate("() => document.querySelector('.rvb-canvas').offsetWidth")
    page.click("button:has-text('重置')")
    assert poll_until(page, lambda: abs(page.evaluate(
        "() => document.querySelector('.rvb-canvas').offsetWidth") - fit_w) < 5, 5000), \
        f"「重置」未回到适应宽度（200% 时 {w200}，fit {fit_w}）"


def f12_3_right_lists_blocks_with_type_and_clause(page):
    """右栏列出块、每块有**类型标签**；有归属的块标出**条文号**"""
    assert _rvb_seed(page, "3") is not None, "前置不成立"
    n = page.locator(".rvb-block").count()
    assert n > 50, f"块列表条数异常：{n}"
    assert page.locator(".rvb-block .rvb-tag").count() == n, "有块缺类型标签"
    # ⚠️ 必须按**可见性**数：`.rvb-clause` 用 x-show 控制 ⇒ 元素**永远在 DOM 里**，
    #    只数存在性会恒等于块数（空转恒绿，本仓 memory 记载过的陷阱）。
    labeled = page.locator(".rvb-block .rvb-clause:visible").count()
    assert 0 < labeled < n, f"条文号呈现异常：{labeled}/{n}（应「部分有、部分无」）"


def f12_4_clicking_block_highlights_it_on_the_left(page):
    """★ 双向联动（右→左）：点右栏块 ⇒ 右栏高亮 + 左栏出现**唯一**对应高亮框"""
    assert _rvb_seed(page, "4") is not None, "前置不成立"
    target = page.evaluate("""() => {
        const els = [...document.querySelectorAll('.rvb-block')];
        return els.findIndex(el => {
            const un = el.querySelector('.rvb-unlocated');
            return !un || getComputedStyle(un).display === 'none';
        });
    }""")
    assert target >= 0, "找不到**未标**「原文件未定位」的块"
    page.locator(".rvb-block").nth(target).click()

    assert poll_until(page, lambda: page.locator(".rvb-block--on").count() == 1, 5000), \
        "点块后右栏没有高亮"
    assert poll_until(page, lambda: page.locator(".rvb-box--on").count() == 1, 5000), \
        "点块后左栏没有对应高亮框"


def f12_5_clicking_box_scrolls_right_panel_to_that_block(page):
    """★ 双向联动（左→右）：点左栏框 ⇒ 右栏滚到该块并高亮。两侧体验**统一**。"""
    assert _rvb_seed(page, "5") is not None, "前置不成立"
    n = page.locator(".rvb-block").count()
    page.locator(".rvb-block").nth(n - 1).click()      # 先把右栏滚下去
    page.locator(".rvb-box").first.click()             # 再点左栏第一个框
    assert poll_until(page, lambda: page.evaluate("""() => {
        const on = document.querySelector('.rvb-block--on');
        if (!on) return false;
        const list = document.querySelector('.rvb-blocks');
        return on.offsetTop >= list.scrollTop - 5
            && on.offsetTop + on.offsetHeight <= list.scrollTop + list.clientHeight + 5;
    }"""), 5000), "点左栏框后，右栏未把对应块滚进视野"


def f12_6_unlocated_block_is_shown_and_labelled_not_hidden(page):
    """★ **未定位块必须显示且明说**（P0 实测：table/chart 各 1 个锚不上）。

    静默隐藏会让用户以为"这块不存在"；标出来他才知道要去原文件里手动核。
    """
    assert _rvb_seed(page, "6") is not None, "前置不成立"
    # ⚠️ 同上：按**可见性**数，只数存在性恒为块数（空转）
    assert page.locator(".rvb-unlocated:visible").count() >= 1, \
        "夹具里应有未定位的块（table/chart），却一个都没**可见地**标出"
    idx = page.evaluate("""() => {
        const els = [...document.querySelectorAll('.rvb-block')];
        return els.findIndex(el => {
            const un = el.querySelector('.rvb-unlocated');
            return un && getComputedStyle(un).display !== 'none';
        });
    }""")
    assert idx >= 0, "找不到可见地标了「原文件未定位」的块"
    body = page.locator(".rvb-block").nth(idx).locator(".rvb-block-bd").inner_text()
    assert body.strip(), "未定位块的内容是空的 —— 应照常渲染，只是不画框"


def f12_7_review_page_falls_back_when_no_layout(page):
    """★ 降级：没有 layout.json 时**不能崩**（spec §13）。

    用 `.pdf`（文本 PDF 走 `extract_text`）⇒ 会被分派到**版面块页**，
    但**天生没有 layout**，正是本用例要的现场。
    """
    task_id = _make_review_task(page, "PROBE-RVB 7", "探针无版面数据", ext=".pdf")
    page.goto(f"{BASE}/import/review/{task_id}")
    ok = poll_until(page, lambda: (
        page.locator(".rvb-error").count() > 0
        or page.locator(".review-editor").count() > 0), 10000)
    assert ok, "无 layout 时审查页既没提示也没回退 —— 直接崩了"


CASES["f12"] = [f12_1_left_shows_page_image,
                f12_2_zoom_modes_change_zoom_and_reset_returns_to_fit,
                f12_3_right_lists_blocks_with_type_and_clause,
                f12_4_clicking_block_highlights_it_on_the_left,
                f12_5_clicking_box_scrolls_right_panel_to_that_block,
                f12_6_unlocated_block_is_shown_and_labelled_not_hidden,
                f12_7_review_page_falls_back_when_no_layout]


# ═══════════════════════════════════════════
# f13：版面块审查页（P2 编辑能力）
# ═══════════════════════════════════════════
#
# 复用 f12 的 _rvb_seed（造带 layout.json 的待审查任务）。
# 本组用例一律**先进入编辑态再断言**；纯函数用例直接 evaluate window.mdInline。
#
# ⚠️ 本仓无 JS 测试设施 ⇒ 探针是纯函数唯一的宿主；改 md-inline.js 必须同步跑 f13。


def f13_1_md_inline_loaded_and_split_lines(page):
    """前置：md-inline.js 已加载（否则后面全是空跑）"""
    page.goto(BASE)
    assert page.evaluate("() => !!(window.mdInline && window.mdInline.splitLines)"), \
        "md-inline.js 未加载（base.html 未引入或版本号没递增）"
    assert page.evaluate("() => window.mdInline.splitLines('a\\nb\\nc').length") == 3


def f13_2_inline_round_trip_is_byte_exact(page):
    """★ 纯函数不变式：domToMd(lineToHtml(x)) === x（含强调、字面 \\n、孤立星号、&<>）"""
    page.goto(BASE)
    cases = [
        "3.0.1 普通一行",
        "**粗体**与*斜体*与~~删除线~~",
        "3.0.1 采用 * 号标注",          # 孤立星号：必须原样保留
        "甲" + chr(92) + "n乙",         # 字面反斜杠 n
        "a & b < c > d",                # 不做 HTML 转义 ⇒ 逐字节可逆
        "",                             # 空行：不得用 <br> 占位，否则回来变成字面 \n
    ]
    for x in cases:
        got = page.evaluate("""(x) => {
            const box = document.createElement('div');
            box.innerHTML = window.mdInline.lineToHtml(x);
            return window.mdInline.domToMd(box);
        }""", x)
        assert got == x, f"行内往返被改坏：{x!r} → {got!r}"
    # ★ **方向钉**（Task 2 评审的 Important 发现，已裁定采纳）：
    #   `domToMd(lineToHtml(x)) === x` 只证明两者**互为逆函数** —— 一对
    #   「lineToHtml 原样返回文本 / domToMd 取 textContent」的错实现同样恒绿，
    #   即整条标记方向可以完全失守而本用例不动。spec §15.3 明确要求这张网**非空**
    #   （摘掉能力必须变红），故必须再直接钉住两个方向。
    html = page.evaluate("() => window.mdInline.lineToHtml('**粗** 和 ~~删~~ 与 *斜*')")
    assert "<strong>" in html and "<s>" in html and "<i>" in html, \
        f"标记没有转成真实标签（行内 md → HTML 方向失守）：{html!r}"
    back = page.evaluate("""() => {
        const box = document.createElement('div');
        box.innerHTML = '<strong>a</strong><s>b</s><i>c</i>';
        return window.mdInline.domToMd(box);
    }""")
    assert back == "**a**~~b~~*c*", f"手写 HTML 未转回标记（DOM → md 方向失守）：{back!r}"
    # Chrome 会在空的可编辑行里留占位 <br>：整行只有一个 <br> 必须被当成**空行**
    got = page.evaluate("""() => {
        const box = document.createElement('div'); box.innerHTML = '<br>';
        return window.mdInline.domToMd(box);
    }""")
    assert got == "", f"占位 <br> 被当成了字面换行：{got!r}"


def f13_3_alignment_wrap_unwrap_round_trip(page):
    """对齐包裹/解包往返；且包裹**不新增行**（M9：绝不引入换行符）"""
    page.goto(BASE)
    got = page.evaluate("""() => {
        const M = window.mdInline;
        const line = M.wrap('图 8.0.2 (mm)', 'center');
        const parsed = M.parseLine(line);
        return {line: line, align: parsed.align, inner: parsed.inner};
    }""")
    assert got["align"] == "center", f"解包未取回对齐档：{got}"
    assert got["inner"] == "图 8.0.2 (mm)", f"解包未取回内容：{got}"
    assert "\n" not in got["line"], "对齐包裹引入了换行 —— 会改变切分"
    assert "text-align:center" in got["line"]


# ⚠️ **编辑框在 `x-for` 内** ⇒ 每个块各有一份 `.rvb-editor` / `.rvb-lines`（靠 `x-show` 隐藏）。
#    所以断言**必须按块作用域**取：`page.locator(".rvb-line")` 会拿到**第 0 个块**的编辑
#    （不论是否可见），点它的按钮 = 点一个 display:none 的元素，Playwright 直接失败。

def _rvb_block(page, idx):
    return page.locator(".rvb-block").nth(idx)


def _rvb_edit(page, idx):
    """进入第 idx 个块的编辑态；返回**该块**的编辑框 locator"""
    blk = _rvb_block(page, idx)
    blk.locator(".rvb-fix").click()
    assert poll_until(page, lambda: blk.locator(".rvb-lines").is_visible(), 5000), \
        "进入编辑态后编辑框未出现"
    return blk.locator(".rvb-lines")


def _rvb_type_line(page, idx, text):
    """把编辑中块的**第一行**整体换成 text，并全选该行内容（execCommand 需要选区）。

    ⚠️ **必须显式补发 `input` 事件**（Task 7 实现者实测逼出）：`el.textContent = t` 是
    **程序化赋值**，浏览器**不触发 `input`**；而该行在 `enterEdit` 里已经 `focus()` 过、
    本来就是 `activeElement`，故这里的 `el.focus()` 与随后的 Playwright `.click()`
    **都不再触发 `focus`**。后果：组件靠 focus/input 维护的 `focusIsClause` 会**永远停在
    enterEdit 时算出的值** ⇒ 断言「编号行上对齐按钮禁用」的用例（f13_10）**恒红**，
    而且与实现对不对**无关**（它失去分辨力，变异证据也随之失效）。
    真实用户的键盘输入是会触发 `input` 的 ⇒ 补发才是**忠实模拟**，不是为过用例而放宽。
    """
    page.evaluate("""([i, t]) => {
        const el = document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line');
        el.textContent = t;
        el.dispatchEvent(new Event('input', {bubbles: true}));   // 忠实模拟键盘输入
        el.focus();
        const r = document.createRange(); r.selectNodeContents(el);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    }""", [idx, text])


def _rvb_first_editable(page):
    """返回第一个**可编辑**块的下标（未定位/非文本类块没有「纠正」按钮）"""
    idx = page.evaluate("""() => [...document.querySelectorAll('.rvb-block')]
        .findIndex(el => {
            const b = el.querySelector('.rvb-fix');
            return b && getComputedStyle(b).display !== 'none';
        })""")
    assert idx >= 0, "一个可编辑块都没有 —— 白名单或 span 判据有问题"
    return idx


def f13_4_entering_edit_keeps_line_count(page):
    """★ spec R10：进入编辑态前后**行数必须一致**（否则一进一出就多/少一行）

    ★ 同时钉住「**进入编辑态不得抛异常**」。为什么必须单独钉：`$nextTick` 回调里的
    throw 被 Alpine 吞掉，而编辑行**已经 append 进去了** ⇒ 只数行数的断言**看不见**它
    （Task 6 实现者实测：计划原稿把 `isClauseLine` 留到下一个 Task 定义，就会造成
    这种「编辑器坏了但四条用例全绿」的静默失效）。故挂一个 pageerror 收集器。
    """
    task_id = _rvb_seed(page, "4")
    assert task_id is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    # 原行数从**后端下发的 layout** 取（不读页内 Alpine 内部状态，避免断言对象漂移）
    expect = page.evaluate("""async ([tid, i]) => {
        const data = await (await fetch('/import/review/' + tid + '/layout')).json();
        return (data.blocks[i].content || '').split('\\n').length;
    }""", [task_id, idx])
    blk = _rvb_block(page, idx)

    errs = []

    def _on_err(e):
        errs.append(str(e))

    page.on("pageerror", _on_err)
    try:
        _rvb_edit(page, idx)
        got = blk.locator(".rvb-line").count()
    finally:
        page.remove_listener("pageerror", _on_err)
    assert not errs, f"进入编辑态时页面抛了异常（会被 Alpine 吞掉，只有这条断言看得见）：{errs}"
    assert got == expect, f"进出编辑态行数不一致：原文 {expect} 行，编辑态 {got} 行"


def f13_5_edit_and_save_updates_the_block(page):
    """改一行 → 保存 → 块内容变 + 出现「已修改」标"""
    assert _rvb_seed(page, "5") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE-EDIT-锚点")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000), \
        "保存后编辑框未收起"
    assert "PROBE-EDIT-锚点" in blk.locator(".rvb-block-bd").inner_text(), "保存后渲染内容没变"
    assert "已修改" in blk.locator(".rvb-block-meta").inner_text(), "保存后没有「已修改」标"


def f13_6_cancel_discards_the_edit(page):
    """取消 ⇒ 内容**一字不动**（不能只收起编辑框）"""
    assert _rvb_seed(page, "6") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    before = blk.locator(".rvb-block-bd").inner_text()
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "不该留下")
    blk.locator("button:has-text('取消')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    assert blk.locator(".rvb-block-bd").inner_text() == before, "取消后内容被改动"


def f13_7_unlocated_and_non_text_blocks_offer_no_fix_button(page):
    """★ `canEdit` 是**两个条件的合取**（有 span ∧ 白名单）⇒ 两条门槛必须**各自**被钉住。

    ⚠️ 为什么不能只断言「存在被锁的块」（Task 6 评审实测）：真实夹具切片里 76 块恰好是
    「66 个 白名单∧有 span」+「10 个 `number`（既非白名单、又无 span）」，**没有一块只违反
    其中一个条件** ⇒ 单独删掉任一条件，原断言照样绿（它实际只证明 canEdit 不是恒真）。
    为此 `_rvb_seed` 补了两块**门槛隔离**样本（见上）。
    """
    assert _rvb_seed(page, "7") is not None, "前置不成立"
    # ⚠️ 一律按**可见性**数：x-show 控制的元素永远在 DOM 里（只数存在性 = 空转恒绿）
    assert page.locator(".rvb-locked:visible").count() > 0, "没有任何块**可见地**标出「不可编辑」"
    assert page.locator(".rvb-fix:visible").count() < page.locator(".rvb-block").count(), \
        "所有块都有「纠正」按钮 —— canEdit 判据没生效"
    # ★ 门槛隔离：两块合成样本的「纠正」都必须不可见
    gate = page.evaluate("""() => {
        const out = {};
        for (const el of document.querySelectorAll('.rvb-block')) {
            const bd = el.querySelector('.rvb-block-bd');
            const t = bd ? bd.textContent.trim() : '';
            if (t === '白名单但无 span' || t === '非白名单但有 span') {
                const fx = el.querySelector('.rvb-fix');
                out[t] = !!(fx && getComputedStyle(fx).display !== 'none');
            }
        }
        return out;
    }""")
    assert '白名单但无 span' in gate and '非白名单但有 span' in gate, \
        f"找不到门槛隔离样本块（`_rvb_seed` 没补上？）：{gate}"
    assert gate['白名单但无 span'] is False, \
        "白名单但**无 span** 的块给了「纠正」⇒ span 门槛被删了（编辑会静默丢）"
    assert gate['非白名单但有 span'] is False, \
        "**非白名单**但有 span 的块给了「纠正」⇒ 白名单门槛被删了"


# `_rvb_type_line` / `_rvb_block` / `_rvb_edit` / `_rvb_first_editable` 已在 Task 6 Step 5 定义

def f13_8_bold_survives_save_and_reopen(page):
    """★ 行内富文本往返：选中 → B → 保存 ⇒ md 里出现 **…**；再进编辑仍显示 <strong>"""
    assert _rvb_seed(page, "8") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE 加粗 x")
    # ⚠️ **必须按「行编辑那排」作用域取**（Task 6 加了表格工具条，其 B 的 title 也叫「粗体」）：
    #    块级定位器 `button[title='粗体']` 会解析出 2 个元素（表格那排被 x-show 隐藏、但仍在 DOM）
    #    ⇒ Playwright 1.62 strict 模式报错。`.rvb-line-tools` 是行编辑专属容器（Task 4 起）。
    blk.locator(".rvb-line-tools button[title='粗体']").click()
    md = page.evaluate("""(i) => window.mdInline.domToMd(
        document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line'))""", idx)
    assert "**" in md, f"点 B 之后没有产生粗体标记：{md!r}"
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    _rvb_edit(page, idx)
    assert blk.locator(".rvb-line strong").count() >= 1, "重进编辑后粗体丢失（往返不闭环）"


def f13_9_alignment_wraps_line_and_does_not_add_lines(page):
    """★ 对齐：非编号行可加居中；产物是对齐包裹，且**行数不变**（M9：绝不引入换行）"""
    assert _rvb_seed(page, "9") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    n_before = blk.locator(".rvb-line").count()
    _rvb_type_line(page, idx, "PROBE 图题居中")     # 非编号行 ⇒ 对齐按钮可用
    # ⚠️ 同 f13_8：表格工具条也有「中」（格内居中）⇒ 必须按行编辑容器取，否则 strict 模式撞 2 个。
    blk.locator(".rvb-line-tools button:has-text('中')").click()
    line = page.evaluate("""(i) => {
        const el = document.querySelectorAll('.rvb-block')[i].querySelector('.rvb-line');
        return window.mdInline.wrap(window.mdInline.domToMd(el), el.dataset.align || null);
    }""", idx)
    assert "text-align:center" in line, f"对齐未落到行上：{line!r}"
    assert "\n" not in line, "对齐包裹新增了换行 —— 会改变切分"
    assert blk.locator(".rvb-line").count() == n_before, "对齐改变了编辑行数"


def f13_10_alignment_is_disabled_on_clause_number_lines(page):
    """★ 编号行上的对齐必须**禁用**（Global Constraint 4）"""
    assert _rvb_seed(page, "10") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "3.0.9 编号行不得被包裹")   # 内含补发 input，见其 docstring
    blk.locator(".rvb-line").first.click()
    # ⚠️ 同上：表格工具条也有「中」⇒ 按行编辑容器取（否则 strict 模式撞 2 个）。
    btn = blk.locator(".rvb-line-tools button:has-text('中')")
    # 禁用状态由**响应式状态** `focusIsClause` 决定，而它由 focus/input 事件维护 ⇒
    # 既要等 Alpine 重算完（poll），也要保证 `_rvb_type_line` 真的触发了 input（否则恒红）
    assert poll_until(page, lambda: btn.is_disabled(), 5000), "编号行上对齐按钮应禁用"
    assert "编号行" in (btn.get_attribute("title") or ""), "禁用原因必须给出说明（title）"


def f13_11_enter_splits_into_a_new_line_not_a_br(page):
    """★ Enter ⇒ **新增一个编辑行**（真实换行），绝不产生行内 <br>（spec §8.2 的建模范式）"""
    assert _rvb_seed(page, "11") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    n = blk.locator(".rvb-line").count()
    _rvb_type_line(page, idx, "第一行")
    blk.locator(".rvb-line").first.press("Enter")
    assert poll_until(page, lambda: blk.locator(".rvb-line").count() == n + 1, 5000), \
        "Enter 没有新增编辑行"
    assert blk.locator(".rvb-line br").count() == 0, "Enter 产生了行内 <br>（会与字面 \\n 混淆）"


def f13_12_preview_lists_clauses_of_the_document(page):
    """预览面板列出当前切分；条数与块列表无关（它是整篇的切分）"""
    assert _rvb_seed(page, "12") is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000), \
        "切分预览没有任何条文"
    n = page.locator(".rvb-clause-list li").count()
    assert n > 50, f"夹具切片条数异常：{n}"


def _rvb_clause_block_idx(page, task_id):
    """找一个「**编辑后真能改变切分条数**」的可编辑块（用与端点同源的 preview 现地试选）。

    ⚠️ **不能按内容正则静态挑**（Task 8 实现者离线复现 + 本机实测，计划原稿的判据在此夹具上失效）：
      - 计划原稿的 `^(#{1,6}\\s|\\d+(\\.\\d+)*...)` **首个命中是目录行** `1 总则 ..... 1`
        （下标 14）——它不是真条文 ⇒ 追加编号行 **+0**、抹掉编号 **+0**，两条用例都不动；
      - 版式受解析器的「标题吸收」等怪癖支配（下标 24 `1.0.1` 抹掉编号是 **+0 而非 −1**，
        因为 `## 1 总 则` 被并进来补位），「看着像编号行」远不足以预测条数变化。
    故只能**拿本块真的造两种编辑、问 preview 结果**，挑同时满足
    「追加编号行 ⇒ +1」与「抹掉首行编号 ⇒ −1」的第一个块（本夹具实证为下标 25 `1.0.2`）。
    这不构成循环：本 helper 只做**前置挑选**；若端点坏了，下面的断言会以「找不到」变红。
    """
    idx = page.evaluate("""async ([tid]) => {
        const L = await (await fetch('/import/review/' + tid + '/layout')).json();
        const editable = new Set(L.editable_labels || []);
        const prev = async (edits) => {
            const r = await fetch('/import/review/' + tid + '/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({edits})});
            if (!r.ok) throw new Error('preview 返回 ' + r.status);
            return (await r.json()).count;
        };
        const base = await prev({});
        for (let i = 0; i < L.blocks.length; i++) {
            const b = L.blocks[i];
            if (b.start_line === null || b.start_line === undefined) continue;
            if (!editable.has(b.label)) continue;
            const ls = (b.content || '').split('\\n');
            if (!/^\\d+(\\.\\d+)*\\s*[.、．]?\\s*\\S/.test((ls[0] || '').trim())) continue;
            const plus = await prev({[i]: ls.concat(['9.9.9 PROBE 新增条文']).join('\\n')});
            if (plus !== base + 1) continue;
            const minus = await prev({[i]: ['PROBE 编号被抹掉'].concat(ls.slice(1)).join('\\n')});
            if (minus === base - 1) return i;
        }
        return -1;
    }""", [task_id])
    assert idx >= 0, "找不到「追加编号行 +1 且抹掉编号 −1」的可编辑块（夹具或端点有问题）"
    return idx


def _rvb_append_line(page, idx, text):
    """在编辑中块的**首行末尾**按 Enter 新增一行，并**真实键入** text（忠实模拟用户操作）。

    ⚠️ 不要拿 `_rvb_type_line` 的「替换首行」来实现「新增一条」：那是**替换**语义，
    换文本不改条数（实测 138→138）。新增必须真按 Enter（组件的 `_onLineKey` 会新建编辑行），
    再用 `.type()` 键入 —— 键入会真触发 `input`，不像程序化赋值。
    """
    blk = _rvb_block(page, idx)
    first = blk.locator(".rvb-line").first
    first.click()
    first.press("End")
    first.press("Enter")
    assert poll_until(page, lambda: blk.locator(".rvb-line").count() >= 2, 3000), \
        "Enter 没有新增编辑行（_onLineKey 失效？）"
    blk.locator(".rvb-line").nth(1).type(text)


def f13_13_editing_a_clause_line_changes_the_preview(page):
    """★ 预览的价值所在：在编号行后补一条**带编号的新行** ⇒ 预览条数 +1 且标出新增"""
    task_id = _rvb_seed(page, "13")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    base = page.locator(".rvb-clause-list li").count()
    idx = _rvb_clause_block_idx(page, task_id)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_append_line(page, idx, "9.9.9 PROBE 新增条文")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() == base + 1, 15000), \
        f"补写编号行后预览条数未 +1（{base} → {page.locator('.rvb-clause-list li').count()}）"
    assert page.locator(".rvb-clause-new").count() >= 1, "新增条文未被标出"


def f13_14_swallowing_a_clause_line_shows_as_removed(page):
    """★ 反向：把编号行改成普通文本 ⇒ 该条**消失**并标红（「把相邻条吞掉」这个失效）"""
    task_id = _rvb_seed(page, "14")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    base = page.locator(".rvb-clause-list li").count()
    idx = _rvb_clause_block_idx(page, task_id)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "PROBE 编号被抹掉")   # **替换**首行 ⇒ 编号没了 ⇒ 少一条
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() < base, 15000), \
        f"抹掉编号后预览条数未减少（仍为 {base}）"
    assert "消失" in page.locator(".rvb-cut-hd").inner_text(), "没有给出「消失 N 条」的提示"


def _rvb_wait_done(page, task_id, timeout_ms=180000):
    """轮询隔离实例的进度 JSON，直到任务离开 processing（真实提交会跑 Phase 2）"""
    import json as _json
    import time as _time
    t0 = _time.time()
    while (_time.time() - t0) * 1000 < timeout_ms:
        txt = page.evaluate("""async (tid) => (await fetch('/import/progress/' + tid + '/json')).text()""",
                            task_id)
        try:
            st = _json.loads(txt).get("status")
        except Exception:
            st = None
        if st in ("done", "error"):
            return st
        page.wait_for_timeout(1000)
    return "timeout"


def f13_15_editing_a_block_does_not_let_submit_while_editing(page):
    """★ 编辑态下提交必须**被拦**并给出可执行的提示（否则用户的改动被判丢）

    ⚠️ 断言取 `#rvb-result + p`（**槽外的兄弟** `<p>`），不是槽内：
    Task 9 评审的 Important 修复把 `submitError` 的 `<p>` 移出了 `#rvb-result`
    （槽内会被 `box.innerHTML = html` 整体换掉）⇒ 提示现在渲染在槽的**外面**。
    计划稿的模板已改、此处断言最初仍指向槽内（自相矛盾）⇒ 断言跟着模板走。
    """
    assert _rvb_seed(page, "15") is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    _rvb_edit(page, idx)
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: "请先" in page.locator("#rvb-result + p").inner_text(), 5000), \
        "编辑态下点提交没有被拦（会静默丢掉未保存的改动）"


def f13_16_submit_sends_the_edited_text_to_phase2(page):
    """★ 端到端：改一行 → 提交 → 任务进入 done，且**库里条文含改后的文本**。

    这是「编辑后导入结果与手改 md 等价」的**端到端证据**：走的正是 compose → confirm → Phase 2
    这条真实链路，不是桩。
    """
    task_id = _rvb_seed(page, "16")
    assert task_id is not None, "前置不成立"
    idx = _rvb_first_editable(page)
    blk = _rvb_block(page, idx)
    _rvb_edit(page, idx)
    _rvb_type_line(page, idx, "9.9.8 PROBE 端到端条文")
    blk.locator("button:has-text('保存')").click()
    assert poll_until(page, lambda: blk.locator(".rvb-editor").is_hidden(), 5000)
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000), \
        "提交后结果区没有回显（说明 confirm 没被调到）"
    st = _rvb_wait_done(page, task_id)
    assert st == "done", f"端到端导入未成功：{st}"
    # 库里真的落进了编辑后的文本 ⇒ 编辑没有停在浏览器里
    assert _rvb_imported_has_text("探针块审查 16", "PROBE 端到端条文"), \
        "编辑后的文本没有入库（compose/confirm 链路断了）"


def f13_17_submit_without_edits_still_imports(page):
    """未编辑直接提交 ⇒ 也能正常导入（规范化不会把内容改坏到切不出条）"""
    task_id = _rvb_seed(page, "17")
    assert task_id is not None, "前置不成立"
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000)
    st = _rvb_wait_done(page, task_id)
    assert st == "done", f"未编辑提交未成功：{st}"


def _rvb_imported_spec_id(title_fragment):
    """定位本次导入的规范 id。

    种子带唯一后缀（code `PROBE-RVB 16` / title `探针块审查 16`），但导入侧会对
    code/title 做归一化（`_parse_filename_to_code_title` 的剥噪声）⇒ **两列都试**，
    再退回「最新一条」（探针刚导完，最新那条就是它；探针运行期间没有别的写入者）。
    """
    from app.database import get_db
    frag = f"%{title_fragment}%"
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM specifications WHERE title LIKE ? OR code LIKE ? "
            "ORDER BY id DESC LIMIT 1", (frag, frag)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM specifications ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _rvb_imported_clause_count(title_fragment):
    """数这个任务**真正入库**的条文数。

    直查隔离库而不是查接口：要断言的正是「预览条数 == 入库条数」，
    中间再隔一层接口（它有自己的过滤）会把差异盖掉。
    """
    from app.database import get_db
    sid = _rvb_imported_spec_id(title_fragment)
    if sid is None:
        return 0
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM clauses WHERE spec_id=?", (sid,)).fetchone()[0]


def _rvb_imported_has_text(title_fragment, needle):
    """库里该规范的任一条文含 needle 即真。

    ⚠️ **必须同时查 `title` 列**（本 Task 实测逼出，计划原稿只查 `content` 会恒红）：
    `parse_markdown` 把**编号行**「`9.9.8 PROBE 端到端条文`」拆成
    `clause_no='9.9.8'` + `title='PROBE 端到端条文'`，而 `content` 只装其后的正文行。
    f13_16 编辑的正是编号行 ⇒ 文本落进 `title`。原稿只查 `content` 与它自己的
    docstring（「任一条文含 needle 即真」）矛盾，且会让一条**链路完全正常**的用例恒红。
    查两列**不削弱分辨力**：真把编辑丢了（compose 未带上改后文本），两列都不会有 needle。
    """
    from app.database import get_db
    sid = _rvb_imported_spec_id(title_fragment)
    if sid is None:
        return False
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM clauses WHERE spec_id=? "
            "AND (content LIKE ? OR title LIKE ?)",
            (sid, f"%{needle}%", f"%{needle}%")).fetchone()[0]
    return n > 0


def f13_18_preview_count_equals_imported_clause_count(page):
    """★★ spec §18 的 P2 出口判据：**切分预览与导入后实际条文数一致**。

    这条是整个 P2 的验收铰链 —— 预览若不准，用户拿它当凭据就是被误导。
    ⚠️ 若首跑不相等，**先查 Phase 2 是否有本条未镜像的过滤**（`_filter_cover_clauses`
    之外的任何一步），把同一条规则补进 preview，**不得为了让它变绿而放宽断言**。
    """
    task_id = _rvb_seed(page, "18")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000), \
        "切分预览为空，无法对账"
    preview_n = page.locator(".rvb-clause-list li").count()
    page.locator("button:has-text('确认并继续导入')").click()
    assert poll_until(page, lambda: page.locator("#rvb-result").inner_text().strip() != "", 10000)
    assert _rvb_wait_done(page, task_id) == "done", "端到端导入未成功"
    imported_n = _rvb_imported_clause_count("探针块审查 18")
    assert imported_n > 0, "库里一条都没入库 —— 断言会退化成 0 == 0 的空转"
    assert preview_n == imported_n, \
        f"切分预览与入库条数不一致：预览 {preview_n} / 入库 {imported_n}"


def f13_19_page_image_is_rendered_at_device_pixel_ratio(page):
    """★★ T39：页图的**位图分辨率**必须跟上屏幕 dpr —— 不是看它「清不清楚」，
    而是直接量 `naturalWidth / clientWidth ≈ devicePixelRatio`。

    ⚠️ **必须自带一个 dpr=2 的页面**：探针主循环建页时没有传
    `device_scale_factor`，Playwright 默认 1 ⇒ 在那一页上量 `ratio ≈ dpr`
    是 **1 ≈ 1 的恒真空转**，修复前后都绿。故本用例自行开一个 2 倍页。
    """
    task_id = _rvb_seed(page, "19")
    assert task_id is not None, "前置不成立"

    browser = page.context.browser
    assert browser is not None, "取不到 browser，无法建 dpr=2 的页面"
    hp = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    try:
        login(hp)                                  # 新页面 = 新 context，必须重新登录
        hp.goto(f"{BASE}/import/review/{task_id}")
        assert poll_until(
            hp,
            lambda: hp.evaluate(
                "() => { const i = document.querySelector('.rvb-img');"
                " return !!i && i.naturalWidth > 0; }"),
            10000), "页图未加载出位图"
        dpr = hp.evaluate("() => window.devicePixelRatio || 1")
        assert abs(dpr - 2) < 0.01, f"dpr=2 的页面没建成（实测 {dpr}）—— 本用例会退化成空转"
        ratio = hp.evaluate(
            "() => { const i = document.querySelector('.rvb-img');"
            " return i.naturalWidth / i.clientWidth; }")
        assert abs(ratio - dpr) < 0.15, \
            f"页图位图密度 {ratio:.2f} 与屏幕 dpr {dpr:.2f} 不符 —— 仍在 1:1 栅格化"
    finally:
        hp.close()


def f13_20_panes_are_three_equal_height_columns(page):
    """★ 三栏：左 PDF / 中 版面编辑 / 右 切分，**等高**且顺序固定。

    此前切分面板嵌在右栏下部（`.rvb-cut` 是 `.rvb-right` 的**子**节点），
    220px 的横条把编辑区挤扁，左中对照被割裂。
    """
    task_id = _rvb_seed(page, "20")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-cut").count() > 0, 10000), "第三栏未出现"
    order = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => e.className.split(' ')[0])")
    assert order == ["rvb-left", "rvb-right", "rvb-cut"], f"栏序不对：{order}"
    hs = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => Math.round(e.getBoundingClientRect().height))")
    assert max(hs) - min(hs) <= 2, f"三栏不等高：{hs}"
    tops = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => Math.round(e.getBoundingClientRect().top))")
    assert max(tops) - min(tops) <= 2, f"三栏不顶对齐：{tops}"
    # ★ 切分栏是**固定宽**（`flex: 0 0 280px`），不是 1:1:1 三等分。
    #   实测：只断言「栏序 / 等高 / 顶对齐」时，把 `flex: 0 0 280px` 换成
    #   `flex: 1 1 50%` **三条全绿** ⇒ 必须直接量宽度，否则用例钉不住固定宽这条设计。
    ws = page.evaluate(
        "() => [...document.querySelector('.rvb-panes').children]"
        ".map(e => Math.round(e.getBoundingClientRect().width))")
    assert abs(ws[2] - 280) <= 2, f"切分栏未固定宽（应为 ~280px）：{ws}"


# 追加到参考 md **末尾**的两条同号条文：制造重复 clause_no 以触发 key 碰撞。
# 控制器实测（2026-10-05）：条数 138→140、重复号 ['A.1']、**既有块 start_line 不变**。
_DUP_CLAUSE_MD = ("\n\n## 附录 C 探针甲\n\nA.1 设备要求应符合下列规定。\n\n"
                  "## 附录 D 探针乙\n\nA.1 设备要求应符合下列规定。\n")


def f13_21_clicking_a_clause_scrolls_to_its_block(page):
    """★ 点切分栏的条号 ⇒ 滚动到并高亮对应块（此前 `openClause` 恒 findIndex=-1，
    因为块上根本没有 clause_no —— Task 1 接线后才可能成立）。"""
    task_id = _rvb_seed(page, "21")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000)
    first = page.locator(".rvb-clause-list li").first
    no = first.locator(".rvb-cn").inner_text().strip()
    first.click()
    assert poll_until(page, lambda: page.locator(".rvb-block--on").count() == 1, 3000), \
        f"点条号 {no} 后没有块被高亮"
    on = page.locator(".rvb-block--on .rvb-clause").first.inner_text().strip()
    assert on == no, f"高亮的块条号 {on!r} 与被点的 {no!r} 不符"
    # ★ **落点必须真的滚进可视区**（原用例只断言"高亮了"，没管位置）。
    #   ⚠️ 这条是 `content-visibility: auto` 的**必配护栏**：屏幕外的块只有**估算高度**
    #      （`contain-intrinsic-size`），而 `_scrollInto` 是「量一次 rect → 设 scrollTop」的**单趟**做法
    #      ⇒ 估偏就会把目标块滚到可视区**之外** —— 用户点了条号却看不见那块，比"没高亮"更难发现。
    #   判据用**像素**而不是比例：块有高有矮（表格块可能比容器还高），比例阈值会误伤。
    box = page.locator(".rvb-blocks")

    def _visible_px():
        bb = box.bounding_box()
        ob = page.locator(".rvb-block--on").bounding_box()
        if not bb or not ob:
            return 0.0
        return max(0.0, min(bb["y"] + bb["height"], ob["y"] + ob["height"])
                   - max(bb["y"], ob["y"]))

    assert poll_until(page, lambda: _visible_px() >= 40, 3000), \
        f"点条号后目标块**没滚进可视区**（可见 {_visible_px():.0f}px）—— " \
        "块高度的估算（contain-intrinsic-size）与实际差太多，_scrollInto 的单趟定位落空了"


def f13_22_focused_block_highlights_its_clause_in_the_cut_column(page):
    """★ 反向联动：聚焦块的条号在切分栏里高亮"""
    task_id = _rvb_seed(page, "22")
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-block .rvb-clause").count() > 0, 10000)
    page.locator(".rvb-block").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li.rvb-clause-on").count() == 1, 3000), \
        "聚焦块后切分栏没有对应条号高亮"


def f13_23_cut_column_collapses_and_gives_width_back(page):
    """★ 窄屏出口：第三栏可折叠，折叠后左右两栏分掉腾出来的宽度"""
    task_id = _rvb_seed(page, "23")
    assert task_id is not None, "前置不成立"
    left_w = lambda: page.evaluate(
        "() => Math.round(document.querySelector('.rvb-right').getBoundingClientRect().width)")
    before = left_w()
    # ⚠️ 用**类名**定位，不要用文字：按钮文案会变（现为 QA 会话栏那种 `切分 ▸` / `◂`），
    #    按文字找的用例会随文案一起烂掉。
    page.locator(".rvb-cut-toggle").click()
    # 用 is_visible 而非 count：x-show 折叠后节点仍在 DOM 里，count 恒为 1（会假绿）
    assert poll_until(page, lambda: not page.locator(".rvb-cut .rvb-clause-list").is_visible(), 3000), \
        "未折叠"
    assert left_w() > before, "折叠后中栏没有变宽"
    # 反向：还得能再展开 —— 只验折叠会放过「单向陷阱」（点开后收不回，宽度也还不回去）
    page.locator(".rvb-cut-toggle").click()
    assert poll_until(page, lambda: page.locator(".rvb-cut .rvb-clause-list").is_visible(), 3000), \
        "折叠后无法再展开（单向陷阱）"
    assert left_w() <= before, "展开后中栏没有把宽度还回去"


def f13_24_duplicate_clause_numbers_render_one_row_each(page):
    """★★ Task 2 那个 `:key` 修复的**运行时护栏**（原本只有源码文本断言）。

    触发条件（控制器 2026-10-05 实测，本 Task 复核）：**重复的 `clause_no` 是可达的，
    且能穿过 Task 4 的编号误判修复** —— parser 的「同号重复拒绝」只管**纯数字**号，
    字母/附录号不受它管：`A.1` 出现两次 ⇒ `['A.1','A.1']`。对照：纯数字 `## 1 …`
    出现两次只会产出一条。

    ⚠️ **判据必须是「渲染行数 == 后端条数」，不能拿首屏行数当自己的基线**：
    用 `:key="'pc' + c.clause_no"` 时 Alpine 的 keyed diff **首次渲染就把重复项并掉**
    （本 Task 实测：后端 140 条 ⇒ DOM 只有 139 行），而「变更前后都 139」照样自洽 ⇒
    拿首屏当基线会**空转恒绿**（本仓 T37 教训）。真要分辨，只能与后端 `count` 对账。
    """
    task_id = _rvb_seed(page, "24", extra_md=_DUP_CLAUSE_MD)
    assert task_id is not None, "前置不成立"
    assert poll_until(page, lambda: page.locator(".rvb-clause-list li").count() > 0, 10000), \
        "切分预览为空"
    # 后端口径：条数 +「确有重复号」这个前置（否则本用例退化成空转）
    api = page.evaluate("""async ([tid]) => {
        const r = await fetch('/import/review/' + tid + '/preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({edits: {}})});
        const d = await r.json();
        const nos = d.clauses.map((c) => c.clause_no);
        const m = {}; nos.forEach((n) => { m[n] = (m[n] || 0) + 1; });
        return {count: d.count, has_dup: Object.values(m).some((v) => v > 1)};
    }""", [task_id])
    assert api["has_dup"], "夹具未造出重复条号 —— key 碰撞无从触发，本用例会空转"
    backend_n = api["count"]
    before_n = page.locator(".rvb-clause-list li").count()
    assert before_n == backend_n, \
        f"重复条号被并掉：渲染 {before_n} 行 / 后端 {backend_n} 条"
    # 列表变更后仍不得塌（症状就是「重算后才折叠」）
    page.locator(".rvb-block .rvb-fix").first.click()
    page.locator("button:has-text('取消')").first.click()
    assert poll_until(
        page,
        lambda: page.locator(".rvb-clause-list li").count() == backend_n, 5000), \
        (f"列表变更后行数塌了：变更前 {before_n} / 变更后 "
         f"{page.locator('.rvb-clause-list li').count()} / 后端 {backend_n} —— key 碰撞未修好")


CASES["f13"] = [f13_1_md_inline_loaded_and_split_lines,
                f13_2_inline_round_trip_is_byte_exact,
                f13_3_alignment_wrap_unwrap_round_trip,
                f13_4_entering_edit_keeps_line_count,
                f13_5_edit_and_save_updates_the_block,
                f13_6_cancel_discards_the_edit,
                f13_7_unlocated_and_non_text_blocks_offer_no_fix_button,
                f13_8_bold_survives_save_and_reopen,
                f13_9_alignment_wraps_line_and_does_not_add_lines,
                f13_10_alignment_is_disabled_on_clause_number_lines,
                f13_11_enter_splits_into_a_new_line_not_a_br,
                f13_12_preview_lists_clauses_of_the_document,
                f13_13_editing_a_clause_line_changes_the_preview,
                f13_14_swallowing_a_clause_line_shows_as_removed,
                f13_15_editing_a_block_does_not_let_submit_while_editing,
                f13_16_submit_sends_the_edited_text_to_phase2,
                f13_17_submit_without_edits_still_imports,
                f13_18_preview_count_equals_imported_clause_count,
                f13_19_page_image_is_rendered_at_device_pixel_ratio,
                f13_20_panes_are_three_equal_height_columns,
                f13_21_clicking_a_clause_scrolls_to_its_block,
                f13_22_focused_block_highlights_its_clause_in_the_cut_column,
                f13_23_cut_column_collapses_and_gives_width_back,
                f13_24_duplicate_clause_numbers_render_one_row_each]


# ═══════════════════════════════════════════
# f14：P3 表格编辑器
# ═══════════════════════════════════════════
# 本组**不走 UI**，而是用 `page.evaluate` 直接对 `window.tableEditor` 做单元式断言 ——
# 本仓没有 JS 单测基建（无 package.json），这是给 JS 纯函数定下的验证方式。
# 每例自带前置：先断言模块已被页面加载，否则整组会以"函数未定义"这种误导性的方式红。

_TBL_HTML = ("<table border=1 style='margin: auto; word-wrap: break-word;'>"
             "<tr><td style='text-align: center;'>序号</td>"
             "<td style='text-align: center;'>图 形</td>"
             "<td style='text-align: center;'>说 明</td></tr>\n"
             "<tr><td style='text-align: center;'>1</td>\n"
             "<td style='text-align: center;'><img src=\"imgs/t.jpg\" /></td>\n"
             "<td style='text-align: center;'>紧急避难设施</td></tr></table>")


def f14_1_round_trip_is_byte_identical(page):
    """★ 地基：**未做任何编辑 ⇒ 序列化结果与原文逐字节相同**（结构上恒真，不是对拍出来的）。

    **为什么必须缓存源串**：浏览器解析必然归一化（插 `<tbody>`、属性引号统一为双引号、
    丢自闭合 `/>`），DOM 往返做不到字节相等；缓存 `_rvbSource` 是「未改动 ⇒ 原样返回」
    的唯一途径（详见 `static/components/table-editor.js` 的 parse/serialize 注释）。"""
    page.goto(f"{BASE}/")                       # 任意应用页即可（base.html 已加载模块）
    has = page.evaluate("() => typeof window.tableEditor === 'object'")
    assert has, "table-editor.js 没被页面加载（检查 base.html 的 script 引用）"
    got = page.evaluate("""(html) => window.tableEditor.serialize(window.tableEditor.parse(html))""",
                        _TBL_HTML)
    assert got == _TBL_HTML, f"往返不逐字节：\n原文 {_TBL_HTML[:80]}\n得到 {got[:80]}"
    # 源串缓存守卫：入参**不是**「单张表」时（此处带尾随 `<p>`）不得缓存源串，
    # 否则未改动时 serialize 会把整个入参串当块内容回吐（静默写坏 md，且往返一致型断言
    # 对它恒绿抓不到）。此处断言 serialize 只吐出那张表，尾随文本不得出现。
    got2 = page.evaluate(
        """(html) => window.tableEditor.serialize(window.tableEditor.parse(html + '<p>tail</p>'))""",
        _TBL_HTML)
    assert isinstance(got2, str), f"serialize 返回非字符串（源串缓存 / 判空守卫失效）：{got2!r}"
    assert 'tail' not in got2, f"带尾随文本的入参被整串回吐（源串缓存守卫失效）：{got2[:120]}"
    assert got2.startswith('<table'), f"多元素入参未退回归一化表 HTML：{got2[:120]}"


def f14_2_serialize_strips_editor_artifacts(page):
    """★ 序列化必须剥掉**编辑器痕迹**（contenteditable / 选中态 class / 高亮包裹），
    否则它们会跟着写进 md。"""
    got = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        t.setAttribute('contenteditable', 'true');
        t.querySelector('td').classList.add('rvb-cell-sel');
        t.querySelector('td').setAttribute('contenteditable', 'true');
        return window.tableEditor.serialize(t);
    }""", _TBL_HTML)
    assert 'contenteditable' not in got, f"编辑器痕迹漏进序列化：{got[:120]}"
    assert 'rvb-cell-sel' not in got, f"选中态 class 漏进序列化：{got[:120]}"


def f14_3_validate_rejects_malformed_tables(page):
    """★ 校验必须挡住**每一种**已声明的非法结构 —— 保存前拦住，绝不写坏 md。

    覆盖 spec §2.4 的三条判据（每条都断言 `ok is False` 且 `reason` 非空）：
      1) `rowspan` 越界（`r + rs > 行数`）
      2) 各行宽度不等
      3) 网格重叠（`colspan` 撞上上方 `rowspan` 的占位）

    ⚠️ 第 4 类「空洞」**零覆盖且造不出**（如实留白，不硬凑）：`_grid` 每行总把自有单元格
    填到**最左空位**，占位因此恒为「0..k 连续无洞」；实测穷举 2~3 行 × 1~3 列、span∈{1,2,3}
    共 **270672** 张表，**0 例**落到「空洞」分支（其余全被「宽度不等 / 越界 / 重叠」先行拦下）。
    """
    # 1) rowspan 越界：把首格 rowspan 设成 99（表只有 2 行）
    got = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        t.querySelector('td').setAttribute('rowspan', '99');
        return window.tableEditor.validate(t);
    }""", _TBL_HTML)
    assert got["ok"] is False and got["reason"], f"越界 rowspan 没被拦住：{got}"
    # 2) 各行宽度不等：删掉第 2 行的一个 td（3 列 → 2 列）
    got = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        const tr = t.querySelectorAll('tr')[1];
        tr.removeChild(tr.querySelector('td'));
        return window.tableEditor.validate(t);
    }""", _TBL_HTML)
    assert got["ok"] is False and got["reason"], f"行宽不等没被拦住：{got}"
    # 3) 网格重叠：第 2 行的 colspan=2 撞上第 1 行 rowspan=2 的占位
    got = page.evaluate("""() => {
        const t = window.tableEditor.parse(
            '<table><tr><td>a</td><td rowspan="2">B</td><td>c</td></tr>' +
            '<tr><td colspan="2">d</td></tr></table>');
        return window.tableEditor.validate(t);
    }""")
    assert got["ok"] is False and got["reason"], f"重叠格没被拦住：{got}"


def f14_4_validate_accepts_well_formed_table(page):
    """★ 反向：合法表必须放行（否则校验会把所有保存都挡掉）。"""
    got = page.evaluate("""(html) => window.tableEditor.validate(window.tableEditor.parse(html))""",
                        _TBL_HTML)
    assert got["ok"] is True, f"合法表被误拒：{got}"


def f14_5_entering_edit_on_a_table_shows_an_editable_table(page):
    """★ 表格块点「纠正」⇒ 块内出现**可编辑表格**，且单元格是可编辑的。

    先钉前置：夹具里**确有**一个带 span 的 table 块 —— 否则本组会以"没有表格块"这种
    误导性的方式红（真实夹具的 table 块在第 29 页、会被页数切片过滤掉，故必须走 with_table）。
    """
    task_id = _rvb_seed(page, "14a", with_table=True)
    assert task_id is not None, "前置不成立（合成表没进去）"
    tbl_blocks = page.evaluate(
        "() => Alpine.$data(document.querySelector('.rvb-root')).blocks"
        ".filter(b => b.label === 'table' && b.start_line !== null).length")
    assert tbl_blocks >= 1, f"夹具没有可编辑的表格块（{tbl_blocks}）"
    fix = page.locator('[data-block-idx]', has_text="探针表格")
    fix.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000), \
        "点纠正后没有出现可编辑表格"
    cells = page.locator(".rvb-table-edit table td")
    assert cells.count() == 6, f"单元格数不对：{cells.count()}"
    # ⚠️ 判据用**渲染态**的 `isContentEditable`，不用 `get_attribute('contenteditable')`：
    #    属性挂在哪（每个 `<td>`）是**实现细节**，而「格能不能编辑」才是契约
    #    ⇒ 换实现（例如改成挂到 `<table>` 上）时，这条断言不该跟着假红。
    assert page.evaluate(
        "() => document.querySelector('.rvb-table-edit table td').isContentEditable"), \
        "单元格不可编辑（contenteditable 没挂上，或没生效）"
    # ★ 行编辑专属控件（B/I/S、对齐、「+ 行」/「- 行」）对表格块**必须不可见**：表格块的
    #    `.rvb-lines` 是空的，这些控件对它是死的（「+ 行」/「- 行」会把 null 传进
    #    `_insertLineAfter`/`_deleteLine` —— 已加 null 守卫兜底，但按钮本就不该出现）。
    #    ⚠️ **自 Task 4 起改用容器 `.rvb-line-tools` 口径 + `count() == 1`**：Task 4 的表格工具条里
    #    也有一个可见的 `+ 行`，而 Playwright 1.62 的 `is_visible()` 以 **strict 模式**解析 ——
    #    `button:has-text('+ 行')` 在同一表格块内会解析出 **2 个**元素 ⇒ strict mode violation
    #    报错（Task 3 收紧到按钮级的写法被 Task 4 引爆）。容器口径天然避开同名冲突，附带的
    #    `count() == 1` 则补回「把 `<span>` 包裹整体去掉 ⇒ 容器定位器 0 个元素 ⇒ `is_visible()`
    #    为假 ⇒ 恒绿」那个空隙（那正是 Task 3 修过的一类空隙）。
    #    （`.rvb-line-tools` 是 `inline-flex` 真实盒子，刻意避开 `display:contents` 那类
    #     「永远 0×0 ⇒ 断言恒绿」的写法。）
    #    ⚠️ 控件组在 `x-for` 内**每块一份** ⇒ 必须按**块作用域**取。
    lt = fix.locator(".rvb-line-tools")
    assert lt.count() == 1, f"行编辑专属控件组应有且仅有一份（实际 {lt.count()} 份）"
    assert not lt.is_visible(), "表格块进入了编辑态，行编辑专属控件却可见（死按钮）"
    # ★ **反向守卫**：「取消」/「保存」对表格块必须**仍然可见** —— 它们是表格编辑态**唯一**的
    #    保存出口（Task 4 的表格工具条只有结构操作按钮、不含保存）。把整排 `.rvb-tools` 一起
    #    门控掉会让表格**根本无法保存**（本轮实测踩过：f14_6/f14_7 的保存点击 30s 超时）。
    assert fix.locator("button:has-text('保存')").is_visible(), \
        "表格编辑态下「保存」按钮不可见 —— 表格将无法保存"
    # ★ 选区手势**提示必须可见**（用户实测：只有 Shift+点 能用、界面又没提示 ⇒
    #   「选不中多格 ⇒ 合并点不动」；这段文案就是唯一的可发现性来源，删了就等于把坑挖回去）。
    hint = fix.locator(".rvb-hint")
    assert hint.count() == 1 and hint.is_visible(), \
        "表格编辑态没有选区手势提示（用户找不到多选手势）"
    # ★ 提示**不得挤占工具条**：`.rvb-tools` 是 `flex-wrap: nowrap; overflow-x: auto`，
    #   把提示塞进那一排会溢出、并把按钮**压扁成竖排**（实测踩过：`+行` 变两行、「合并」竖排）。
    #   判据用**按钮高度是否一致** —— 被压扁时个别按钮会变高，而横向溢出量测不出来
    #   （按钮 shrink 之后 `scrollWidth == clientWidth`，那个判据抓不到）。
    hts = fix.locator(".rvb-tools").last.locator("button").evaluate_all(
        "els => els.map(e => Math.round(e.getBoundingClientRect().height))")
    assert len(set(hts)) == 1, \
        f"表格工具条按钮高度不一致（{sorted(set(hts))}）—— 有按钮被挤成竖排（提示抢了同一行？）"
    # ★ 选区模型（Task 5）：点 = 单格；**Shift+点 = 以锚点到当前格为对角**扩成矩形（spec §2.3）。
    #   ⚠️ 判据必须能分辨「锚点固定」与「锚点漂移到上次点击处」——**只数选中格数分辨不出**
    #      （两次 Shift+点按漂移写法一样能得到 3 格）⇒ 必须断言**具体是哪几格**。
    #      夹具是 2×3（文档序 0..5：第 0 行 0/1/2，第 1 行 3/4/5）。
    #      点 0 → Shift 点 5（锚点 0 ⇒ 整表 6 格）→ Shift 点 2：
    #        · 锚点固定 ⇒ [0..2] = {0,1,2}（第 0 行整行）
    #        · 锚点漂移 ⇒ 锚点已变成 5 ⇒ 行 0..1 × 列 2 ⇒ {2,5}   ← 两者**不同**，可证伪
    sel = lambda: page.evaluate("""() => [...document.querySelectorAll('.rvb-table-edit td')]
        .map((c, i) => c.classList.contains('rvb-cell-sel') ? i : -1).filter((i) => i >= 0)""")
    cells.nth(0).click()
    assert poll_until(page, lambda: sel() == [0], 3000), \
        f"点单元格未选中它（选区模型没接上）：{sel()}"
    cells.nth(5).click(modifiers=["Shift"])
    assert poll_until(page, lambda: sel() == [0, 1, 2, 3, 4, 5], 3000), \
        f"Shift+点没有扩成矩形（期望整表 6 格）：{sel()}"
    cells.nth(2).click(modifiers=["Shift"])
    assert poll_until(page, lambda: sel() == [0, 1, 2], 3000), \
        (f"锚点漂移了：以锚点(0)为对角应得 {{0,1,2}}，实得 {sel()} —— "
         "若得到 {2,5} 即锚点被上次点击(5)带走（spec §2.3 要求锚点固定）")
    # ★ **拖拽框选**（用户实测：拖拽是选区的**自然手势**，原先完全没实现 ⇒ 拖完选中数为 0
    #   ⇒ 点「合并」只得「该操作在当前选区不可用」，用户读作"不能同时选中多个单元格"）。
    #   ⚠️ 必须用**真实鼠标**（move → down → move → up），`locator.click` 造不出拖拽过程。
    #   ⚠️ 松手后浏览器**必然**补一个 `click` —— 实现里必须抑制它，否则刚拖出的矩形会**当场塌成一格**
    #      （这正是判据能分辨的两档：期望 {3,4,5}；得 `[]` = 拖拽没实现；得 `[5]` = click 没抑制）。
    bb3 = cells.nth(3).bounding_box()
    bb5 = cells.nth(5).bounding_box()
    assert bb3 and bb5, "取不到单元格坐标（前置不成立）"
    page.mouse.move(bb3["x"] + bb3["width"] / 2, bb3["y"] + bb3["height"] / 2)
    page.mouse.down()
    page.mouse.move(bb5["x"] + bb5["width"] / 2, bb5["y"] + bb5["height"] / 2, steps=8)
    page.mouse.up()
    # ⚠️ 本条**只**证明「拖拽能框选」这一件事。实测（MUTANT-1 把 `onTableCellOver` 整个短路掉）：
    #    失败时选区**停在 `{0,1,2}`** —— 即上一次的选区原封未动，拖拽路径根本没跑。
    #    ⚠️ **不要**把失败消息写成「得到空数组」：那是**未实测**的说法（实测得到的是上一次的选区）。
    assert poll_until(page, lambda: sel() == [3, 4, 5], 3000), \
        (f"拖拽没有框选出整行：期望 {{3,4,5}}，实得 {sel()} —— "
         "实测：拖拽处理被短路掉时选区会**停在上一次的 {{0,1,2}}**（原封未动），"
         "因为拖拽路径根本没跑；若实得是其它值说明拖拽跑了一半")
    # ★ **删掉锚点格之后，选区必须能自愈**（Task 5 复审的 out-of-scope 发现，Task 6 带走）。
    #   症状：选中某格 → 点「- 行」删掉它所在行 ⇒ `_selAnchor` 指向**已分离**的节点 ⇒
    #   下一次 Shift+点走 `_selectTable(cell, true)` ⇒ `anchor` 是分离节点 ⇒ `selectRange` 里
    #   `rows.indexOf(anchor.closest('tr'))` = -1 ⇒ `g.grid[-1].indexOf` 抛 **TypeError**。
    #   ⚠️ 该抛出发生在 `@click` 处理器里、**不在** `tableOp` 的 try/catch 内 ⇒ 用户视角是
    #      「Shift+点毫无反应」+ 控制台报错（本仓既定口径：不得有未捕获的交互异常）。
    #   判据：出事的写法下 `selectRange` 会**先清空所有 `rvb-cell-sel` 再抛** ⇒ 声明为**空数组**；
    #        修好后应恰好选中被点的那个格 ⇒ 用 `sel()` 的**内容**分辨（非空 vs 空）。
    cells.nth(0).click()                      # 锚点落在第 0 格
    # ⚠️ 必须按**表格那排**工具条取按钮：行编辑工具条里也有一个同名 `- 行`（被 x-show 隐藏但仍在 DOM），
    #    页面级/块级定位器会解析出 2 个元素 ⇒ Playwright 1.62 的 strict 模式直接报错。
    #    `.rvb-tools` 在块内有**两**份（行编辑那排在前、表格那排在后）⇒ `.last` 才是表格工具条。
    fix.locator(".rvb-tools").last.locator("button:has-text('- 行')").click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit td").count() == 3, 3000), \
        "「- 行」没删掉那一行"
    left_cells = page.locator(".rvb-table-edit td")
    left_cells.nth(2).click(modifiers=["Shift"])
    assert poll_until(page, lambda: sel() == [2], 3000), \
        (f"删掉锚点格后再 Shift+点没有自愈（应选中被点的格）：{sel()} —— "
         "空数组即 `selectRange` 抛了 TypeError（锚点仍指向已分离的节点）")
    # ★ **结构操作新造的格也必须可编辑**（用户实测缺陷：拆分后只有原来带内容的格能编辑）。
    #   根因：`contenteditable` 原先**逐个挂在当时已存在的格**上，而 `insertRow`/`insertCol`/
    #   `splitCell` 都用 `document.createElement('td')` 造新格 ⇒ 新格**永远挂不上**。
    #   现改为：**每个**会造新格的操作之后重挂一遍（`_markTableEditable`）⇒ 判据就是
    #   「**新造**的格也 `isContentEditable`」。
    #   ⚠️ 必须走**真实组件**（点 `+ 行`）而非纯函数：属性挂载发生在组件里（`enterEdit`/
    #      `_restoreTable`/`tableOp` 成功分支），纯函数根本不碰它；且 `isContentEditable` 对
    #      **分离**节点恒为 false（纯函数夹具是分离的）⇒ 在纯函数用例里断言会**假红**。
    n_before = page.locator(".rvb-table-edit td").count()
    fix.locator(".rvb-tools").last.locator("button:has-text('+ 行')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit td").count() > n_before, 3000), \
        "「+ 行」没有插入新行（前置不成立）"
    n_bad = page.evaluate("""() => [...document.querySelectorAll('.rvb-table-edit td')]
        .filter((c) => !c.isContentEditable).length""")
    assert n_bad == 0, \
        (f"结构操作新造的格不可编辑（{n_bad} 个）—— 造新格的操作之后没有重挂 `contenteditable`；"
         "用户实测的「拆分后只有原来带内容的格能编辑」就是这条")


def f14_6_saving_an_edited_table_writes_the_md_region(page):
    """★ 保存 ⇒ 该表在 md 里的那段被**整段替换**成序列化结果（写回路径打通）。

    断言到**结果**：保存后块的 `renderBlock` 里出现改后的文字，且**开标签属性仍在**
    （`border=1` / `margin: auto` —— 丢了就等于把表格的边框与居中弄没了）。
    """
    task_id = _rvb_seed(page, "14b", with_table=True)
    assert task_id is not None, "前置不成立"
    # ⚠️ 块定位必须用**下标**（不能用 has_text）：保存后格内文字被改成「改过的说明」，
    #    has_text 会当场失去匹配 ⇒ locator 解析成 0 个元素，poll 里的 inner_text() 直接超时。
    #    且 `.rvb-editor` 在 `x-for` 内**每块一份**、按钮靠 `x-show` 隐藏 ⇒ 保存按钮也必须
    #    **按块作用域**取（页面级 `.first` 会点到第 0 块那个不可见的 `保存`，点击超时）。
    idx = page.evaluate("""() => [...document.querySelectorAll('.rvb-block')]
        .findIndex(el => el.textContent.includes('探针表格'))""")
    assert idx >= 0, "前置不成立：找不到合成表格块"
    blk = page.locator(f'[data-block-idx="{idx}"]')
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    cell = page.locator(".rvb-table-edit table td").nth(5)          # 「探针表格」那格
    cell.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("改过的说明")
    blk.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: "改过的说明" in blk.inner_text(), 5000), "保存后块内容没变"
    md_html = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        return d.edits[d.blocks.findIndex(b => b.label === 'table' && b.start_line !== null)];
    }""")
    assert md_html and "改过的说明" in md_html, f"edits 里没有改后的表：{str(md_html)[:80]}"
    # ⚠️ 断言必须按**属性值**核，**不能**按源串字面核 `"border=1"` —— 表格一经改动，
    #    序列化走归一化形态（`border=1` → `border="1"`、引号统一、自动插 `<tbody>`），
    #    字面断言会**假红**。（本用例改了格内文字 ⇒ 一定走归一化分支。）
    probe = page.evaluate("""(html) => {
        const t = window.tableEditor.parse(html);
        return { border: t.getAttribute('border'), style: t.getAttribute('style') || '' };
    }""", md_html)
    assert probe["border"] == "1", f"border 属性丢了（表格会没边框）：{probe}"
    assert "margin: auto" in probe["style"] or "margin:auto" in probe["style"], \
        f"margin:auto 丢了（表格会左对齐）：{probe}"
    assert "contenteditable" not in md_html, "编辑器痕迹漏进了 md"
    # ★ **保存后再进编辑，必须看到改后的内容**（Task 6 复审的 Important 修复，折进本用例、不新增编号）。
    #   `enterEdit` 原取 `b.md_html`（**导入时**的原表）⇒ 「改→保存→再点纠正」会从原始表重建编辑器，
    #   此时再保存就把 `edits[i]` 覆盖回原始表 ⇒ **第一次编辑静默丢失**（两步用户动作可达）。
    #   判据必须落在**编辑器里显示什么**（那句"再进编辑看到原始表"正是可分辨的失效形态）+
    #   **二次保存后 md 里仍在**（覆盖发生在上面的写入路径上）。
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000), \
        "二次进入编辑态失败"
    shown = page.evaluate("""() => [...document.querySelectorAll('.rvb-table-edit td')]
        .map(td => td.textContent).join('|')""")
    assert "改过的说明" in shown, \
        (f"重进编辑看到的是**原始表** ⇒ 再保存会把用户的编辑静默覆盖回原文：{shown}")
    blk.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 0, 5000), \
        "二次保存后没退出编辑态"
    md_html2 = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        return d.edits[d.blocks.findIndex(b => b.label === 'table' && b.start_line !== null)];
    }""")
    assert "改过的说明" in md_html2, \
        (f"二次保存把第一次的编辑覆盖回原始表了（静默数据丢失）：{str(md_html2)[:120]}")


def f14_7_save_refuses_a_broken_table_and_keeps_editing(page):
    """★ 校验**在保存路径上**也要挡住（不是只在纯函数层挡）：非法结构 ⇒ 不写入 edits、
    留在编辑态、块内出现可读报错。这是 spec §2.4「绝不写坏 md」的可观测形式。"""
    task_id = _rvb_seed(page, "14c", with_table=True)
    assert task_id is not None, "前置不成立"
    blk = page.locator('[data-block-idx]', has_text="探针表格")
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        d._tableEl().querySelector('td').setAttribute('rowspan', '99');
    }""")
    # 保存按钮**必须按块作用域**取：`.rvb-editor` 在 `x-for` 内每块一份，
    # 页面级 `.first` 命中的是第 0 块那个被 `x-show` 隐藏的「保存」（点击超时）。
    blk.locator("button:has-text('保存')").first.click()
    # ⚠️ 报错 `<p>` 在 `x-for` 内**每块一份**（`x-show` 控制显隐、元素恒在 DOM）⇒
    #    页面级 `.rvb-table-err` 会命中全部块、触发 strict 模式；必须按块作用域取。
    assert poll_until(page, lambda: blk.locator(".rvb-table-err").is_visible(), 3000), \
        "非法结构没有给出块内报错"
    state = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        return { editing: d.editingIdx, edits: Object.keys(d.edits).length };
    }""")
    assert state["editing"] >= 0, "校验失败后不该退出编辑态（会丢失用户改动）"
    assert state["edits"] == 0, f"非法结构竟然写进了 edits：{state}"


def f14_8_insert_and_delete_row_keep_the_rectangle(page):
    """★ 插入/删除行后表仍是**矩形**（校验通过），且**插在选区所在行之前**。

    ⚠️ **必须钉住「位置」而不只是「数量」**：`before=true` 若被忽略（恒插在之后），
    行数仍是 3、随后删首格所在行仍回到 2 行且合法 ⇒ 只断数量的写法对该变异**恒绿**
    （docstring 承诺了「之前」却没人验证＝假承诺）。故断言：插后 `rows[0]` 是**空新行**、
    原首行文字**下移到 `rows[1]`**。
    """
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const firstCell = t.querySelector('td');
        const firstRowText = [...t.querySelectorAll('tr')[0].children]
            .map(c => c.textContent.trim()).join('|');
        T.insertRow(t, firstCell, true);              // 在第 1 行之前插一行
        const rows = [...t.querySelectorAll('tr')];
        const rows1 = rows.length;
        const newRowEmpty = [...rows[0].children].every(c => c.textContent.trim() === '');
        const oldNowAt1 = [...rows[1].children].map(c => c.textContent.trim()).join('|');
        const v1 = T.validate(t);
        T.deleteRows(t, [t.querySelector('td')]);     // 删掉刚插的那行的首格所在行
        return { rows1, ok1: v1.ok, reason1: v1.reason, newRowEmpty, oldNowAt1, firstRowText,
                 rows2: t.querySelectorAll('tr').length, ok2: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["rows1"] == 3, f"插入后行数不对：{got}"
    assert got["ok1"] is True, f"插入后结构非法：{got['reason1']}"
    assert got["newRowEmpty"], f"新插入的行不是空行：{got}"
    assert got["oldNowAt1"] == got["firstRowText"], \
        f"原首行未下移到第 2 行（`before=true` 被忽略？）：{got}"
    assert got["rows2"] == 2 and got["ok2"] is True, f"删除后不对：{got}"


def f14_9_insert_and_delete_column_keep_the_rectangle(page):
    """★ 列同理：插列后每行宽度 +1、**新列落在下标 1 且为空**、原第 2 格右移，删列后恢复且始终通过校验。

    ⚠️ 同 f14_8：位置（`before=false` 的语义）必须钉住，不能只断列数。
    ⚠️ 另含**回滚承诺**断言：rowspan 表上调 `insertColAfter` 会**抛 `NotFoundError`**
    （第 i 行取到的 `ref` 是上一行 rowspan 格的 DOM、不属于本 `<tr>`）⇒ `tableOp` 必须
    整体回滚 + 置 `tableError`（否则用户看到锯齿表却毫无提示）。用桩替换
    `_tableEl`/`_tableHost` 直接驱动真实的 `tableOp`。
    """
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        T.insertCol(t, t.querySelector('td'), false);   // 在第 1 格**之后**插一列
        const row0 = [...t.querySelectorAll('tr')[0].children];
        const cols1 = row0.length;
        const ok1 = T.validate(t).ok;
        const newColEmpty = row0[1].textContent.trim() === '';
        const shifted = row0[2].textContent.trim();     // 原第 2 格「图 形」应右移到下标 2
        T.deleteCols(t, [t.querySelectorAll('tr')[0].children[1]]);
        return { cols1, ok1, newColEmpty, shifted,
                 cols2: t.querySelectorAll('tr')[0].children.length, ok2: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["cols1"] == 4 and got["ok1"] is True, f"插列不对：{got}"
    assert got["newColEmpty"], f"新插入的列不是空列：{got}"
    assert got["shifted"] == "图 形", f"原第 2 格未右移（`before=false` 被忽略？）：{got}"
    assert got["cols2"] == 3 and got["ok2"] is True, f"删列不对：{got}"

    # ★ 回滚承诺：rowspan 表上会抛的结构操作 ⇒ 结构回到操作前 + tableError 非空。
    assert page.evaluate("() => !!document.querySelector('.rvb-root')"), \
        "前置不成立：不在审阅页（无 .rvb-root），无法驱动 tableOp"
    rb = page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        const RS = '<table border="1"><tr><td rowspan="2">序号</td><td>图形</td></tr>'
                 + '<tr><td>说明</td></tr></table>';
        const t = window.tableEditor.parse(RS);
        const host = document.createElement('div');
        host.appendChild(t);
        const before = t.outerHTML;
        // ⚠️ **桩必须还原**（Task 5 修）：这里是**活页** Alpine 状态，本组后续用例（f14_10 起）
        //    与真实交互都依赖真的 `_tableEl`/`_tableHost`；残留的桩会把它们指向本用例的临时表
        //    ⇒ 后续用例假红/假绿。原写法**仅因本用例恒为末条**才侥幸安全，一旦往后追加用例即引爆。
        const orig = { el: d._tableEl, host: d._tableHost,
                       err: d.tableError, snaps: d._tableSnapshots };
        try {
            // 用桩把待测表接进 tableOp（真实逻辑不变，只改 DOM 取址）
            d._tableEl = () => t;
            d._tableHost = () => host;
            d.tableError = '';
            d._tableSnapshots = [];
            d.tableOp('insertColAfter');        // rowspan 表上必抛 NotFoundError
            const out = host.querySelector('table');
            // ⚠️ 还原后 `_restoreTable` 会**故意**给每格补回 `contenteditable="true"`（编辑态需要），
            //    故逐字比较前必须剥掉它，否则会误判成「未还原」（实测踩过）。
            const strip = (s) => s.replace(/ contenteditable="true"/g, '').replace(/\\s+/g, ' ');
            return { restored: !!out && strip(out.outerHTML) === strip(before),
                     rows: out ? out.querySelectorAll('tr').length : -1,
                     cells: out ? out.querySelectorAll('td').length : -1,
                     rowspan: out ? (out.querySelector('td').getAttribute('rowspan')) : null,
                     err: d.tableError, snaps: d._tableSnapshots.length };
        } finally {
            // 无论成功/抛出/断言读取，都把活页状态恢复原样（后续用例不再被污染）
            d._tableEl = orig.el;
            d._tableHost = orig.host;
            d.tableError = orig.err;
            d._tableSnapshots = orig.snaps;
        }
    }""")
    assert rb["err"], f"抛异常的结构操作没有给出块内报错：{rb}"
    assert rb["snaps"] == 0, f"失败的操作竟压入了快照（撤销栈会被污染）：{rb}"
    assert rb["restored"], f"抛异常后表未回到操作前（用户被留在半坏的表里）：{rb}"
    assert rb["rows"] == 2 and rb["cells"] == 3 and rb["rowspan"] == "2", \
        f"回滚后的表结构不对：{rb}"

    # ★ 补两条 Task 4 的**欠覆盖分支**（Task 4 报告 + 其评审指出：f14_8/f14_9 用的都是普通表，
    #   这两条只有人工佐证）。合并格夹具天然适合 —— 折进本用例，不新增编号（否则顺延 Task 6）。
    #   ① `insertCol` 的**合并格拒绝**分支：插入边界落在跨列格内部 ⇒ 返回 false、结构不变；
    #      且同一张表上非合并边界必须**放行**（防「一律拒绝」的过宽判据 —— Task 4 的 `!==` 覆辙）。
    #   ② `deleteCols` 的 **colspan 递减**分支：跨列格被删掉覆盖范围内的一列 ⇒ `colspan` 减 1
    #      （**整格不删**），其余行照常删该格 ⇒ 仍是矩形表。
    br = page.evaluate("""() => {
        const T = window.tableEditor;
        const HTML = '<table border="1"><tr><td colspan="2">A</td><td>B</td></tr>'
                   + '<tr><td>C</td><td>D</td><td>E</td></tr></table>';
        // ① 拒绝：边界落在 A（colspan=2，占第 0/1 列）内部
        const t1 = T.parse(HTML);
        const a1 = t1.querySelector('td');
        const before1 = t1.querySelectorAll('td').length;
        const rej = T.insertCol(t1, a1, false);
        const after1 = t1.querySelectorAll('td').length;
        const rejOk = T.validate(t1).ok;
        // ①b 放行：同一张表上的非合并边界（B 在第 2 列之后 ⇒ at=3，不落在任何跨列格内）
        //     ⚠️ 行内**子元素数** ≠ 网格列数（A 跨 2 列 ⇒ row0 只有 2 个子元素）⇒ 取 children[1]=B。
        const t2 = T.parse(HTML);
        const b2 = t2.querySelectorAll('tr')[0].children[1];
        const acc = T.insertCol(t2, b2, false);
        const kids2 = [...t2.querySelectorAll('tr')].map(r => r.children.length);
        const accOk = T.validate(t2).ok;
        // ② 递减：格删「第 0 列」（用第 2 行的 C 定位 ⇒ cols 恰好 = {0}），A 的 colspan 应 2→1 且整格保留
        const t3 = T.parse(HTML);
        const c3 = t3.querySelectorAll('tr')[1].children[0];
        const ok3 = T.deleteCols(t3, [c3]);
        const a3 = t3.querySelector('td');
        return { rej, before1, after1, rejOk, acc, kids2, accOk,
                 ok3, cs3: a3 && a3.getAttribute('colspan'), a3Text: a3 && a3.textContent.trim(),
                 kids3: [...t3.querySelectorAll('tr')].map(r => r.children.length),
                 ok3v: T.validate(t3).ok };
    }""")
    assert br["rej"] is False and br["after1"] == br["before1"] and br["rejOk"] is True, \
        f"落在合并格内部的插列没被拒绝（或改动了结构）：{br}"
    assert br["acc"] is True and br["kids2"] == [3, 4] and br["accOk"] is True, \
        f"非合并边界的插列被误拒 / 未插格（判据过宽？）：{br}"
    assert br["ok3"] is True and br["cs3"] == "1", \
        f"跨列格未被 span 递减（或整格被删）：{br}"
    assert br["a3Text"] == "A", f"跨列格被整格删除了（应只减 colspan、保留格）：{br}"
    assert br["kids3"] == [2, 2] and br["ok3v"] is True, f"span 递减后不是矩形表：{br}"


def f14_10_merge_sets_spans_and_drops_swallowed_cells(page):
    """★ 合并：左上格设 rowspan/colspan，**其余格删除**，结构仍合法。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const rows = t.querySelectorAll('tr');
        const cells = [...rows[0].children, ...rows[1].children];   // 整表 2×3
        const ok = T.mergeCells(t, cells);
        const tl = rows[0].children[0];
        return { ok, rs: tl.getAttribute('rowspan'), cs: tl.getAttribute('colspan'),
                 childRows: [...t.querySelectorAll('tr')].map(r => r.children.length),
                 valid: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"合并结果不合法：{got}"
    assert got["rs"] == "2" and got["cs"] == "3", f"span 不对：{got}"
    assert got["childRows"] == [1, 0], f"被吞格没删干净：{got}"


def f14_11_split_restores_empty_cells(page):
    """★ 拆分：按 colspan/rowspan 补出**空格**（内容不还原 —— spec §8.3 用户已接受）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const rows = [...t.querySelectorAll('tr')];
        T.mergeCells(t, [...rows[0].children, ...rows[1].children]);
        const tl = rows[0].children[0];
        const before = tl.getAttribute('colspan');
        const ok = T.splitCell(t, tl);
        return { before, ok, rs: tl.getAttribute('rowspan'), cs: tl.getAttribute('colspan'),
                 rows: [...t.querySelectorAll('tr')].map(r => r.children.length),
                 valid: T.validate(t).ok, firstText: rows[0].children[0].textContent.trim() };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"拆分结果不合法：{got}"
    assert got["rs"] is None and got["cs"] is None, f"span 属性没清掉：{got}"
    assert got["rows"] == [3, 3], f"行列没恢复：{got}"
    assert got["firstText"] == "序号", f"左上格内容应保留：{got}"


def f14_12_merge_on_a_single_cell_is_a_no_op(page):
    """★ 反向：只选一格时合并不应有副作用（否则用户误点就把表改了）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const before = T.serialize(t);
        const ok = T.mergeCells(t, [t.querySelector('td')]);
        return { ok, changed: T.serialize(t) !== before };
    }""", _TBL_HTML)
    assert got["ok"] is False and got["changed"] is False, f"单格合并产生了副作用：{got}"


def f14_13_remove_images_in_selected_cells(page):
    """★ 删图：只删**选区格内**的 `<img>`，其余格不动，且**文件不删**（spec §19）。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const imgCell = t.querySelector('img').closest('td');
        const n = T.removeImages([imgCell]);
        return { n, imgs: t.querySelectorAll('img').length, valid: T.validate(t).ok };
    }""", _TBL_HTML)
    assert got["n"] == 1 and got["imgs"] == 0, f"删图结果不对：{got}"
    assert got["valid"] is True, "删图把结构弄坏了"


def f14_14_align_applies_to_cell_text_only(page):
    """★ 对齐作用于**格内文字**（spec §8.6）；**表格整体恒居中**，不提供按钮。"""
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const cell = t.querySelector('td');
        T.applyAlign([cell], 'right');
        const out = T.serialize(t);
        return { style: cell.style.textAlign, hasTableAlign: /<table[^>]*text-align/.test(out) };
    }""", _TBL_HTML)
    assert got["style"] == "right", f"格内对齐没生效：{got}"
    assert got["hasTableAlign"] is False, "对齐写到 <table> 上了（应为格内文字）"


def f14_15_exit_criterion_image_row_can_be_removed(page):
    """★★ **出口判据（spec §18）**：「行列被当成图片塞进合并格」能被**完整修好**。

    走一遍真实修法：真实产物里「图 形」整列都是 `<img>` ⇒ 选中该列任一格 → 删列
    ⇒ 该列消失、表结构仍合法、且**没有残留的空格**。
    """
    got = page.evaluate("""(html) => {
        const T = window.tableEditor;
        const t = T.parse(html);
        const imgCell = t.querySelector('img').closest('td');      // 「图 形」列
        const ok = T.deleteCols(t, [imgCell]);
        const v = T.validate(t);
        const widths = [...t.querySelectorAll('tr')].map(r => r.children.length);
        return { ok, valid: v.ok, reason: v.reason, widths,
                 imgs: t.querySelectorAll('img').length,
                 text: t.textContent.replace(/\\s+/g, '') };
    }""", _TBL_HTML)
    assert got["ok"] is True and got["valid"] is True, f"删列不合法：{got}"
    assert got["widths"] == [2, 2], f"列没删干净/留了空格：{got}"
    assert got["imgs"] == 0, f"图片列没清掉：{got}"
    assert "序号" in got["text"] and "紧急避难设施" in got["text"], f"误删了内容：{got}"


def f14_16_saving_a_table_edit_does_not_change_the_clause_set(page):
    """★ spec §2.6：**表格编辑不改变切分** —— 保存前后条号集合必须相同。

    切分预览与入库条数的一致性（上游 §18 的 P2 铰链）靠这条延续：表格 HTML 不产生
    新条文号，所以「编辑表格」不该让任何条文凭空出现或消失。

    ⚠️ 直接打 `/preview` 取口径（不读 UI 的预览列表）—— 避开 400ms 防抖带来的时序假红。
    """
    task_id = _rvb_seed(page, "14d", with_table=True)
    assert task_id is not None, "前置不成立"

    def _clause_nos(edits):
        return page.evaluate("""async ([tid, edits]) => {
            const r = await fetch('/import/review/' + tid + '/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({edits})});
            return (await r.json()).clauses.map((c) => c.clause_no).sort();
        }""", [task_id, edits])

    before = _clause_nos({})
    assert before, "前置：预览为空，无法对账"

    blk = page.locator('[data-block-idx]', has_text="探针表格")
    blk.locator("button:has-text('纠正')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 1, 5000)
    # ★ **删图必须可撤销**（spec §2.4 把「删图」列进结构操作，且要求快照压在操作**前**）。
    #   ⚠️ 必须走**组件入口**（`tableRemoveImages` / `tableUndo`）而非纯函数 —— 「压快照」只发生在组件里，
    #      纯函数根本不碰快照栈。判据要能分辨「压操作前」与「压操作后」：
    #        · 压**操作前** ⇒ 撤销把图片找回来（1 张）
    #        · 压**操作后** ⇒ restore 回自己 ⇒ 撤销**空转**（仍 0 张）   ← 两者可分辨
    #   ⚠️ 先把**图片格**点上：`tableRemoveImages` 的选区为空时回落到**首格**（序号格，没有图）⇒
    #      不点就是 n=0、只写一条 `tableError`，本段会以"没生效"的样子红，看不出是快照口径的问题。
    n_img = lambda: page.locator(".rvb-table-edit table img").count()
    assert n_img() == 1, "前置：夹具表里应有 1 张图"
    page.locator(".rvb-table-edit table td").nth(4).click()      # 2×3 夹具里图片在第 5 格
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).tableRemoveImages()")
    assert poll_until(page, lambda: n_img() == 0, 3000), "「删图」没生效（选区或入口没接上）"
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).tableUndo()")
    assert poll_until(page, lambda: n_img() == 1, 3000), \
        ("撤销没把图片找回来 —— 快照压在了操作**之后**（restore 回自己 ⇒ 撤销空转）；"
         "spec §2.4 要求操作**前**压")
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).tableRemoveImages()")
    assert poll_until(page, lambda: n_img() == 0, 3000), "再删一次失败"
    # 再做一次**结构变更**供保存对账用。⚠️ 不能再用「图片格」定位（上面已把图片删掉 ⇒ `img` 为 null）；
    # 删首列即可 —— 本条只关心「保存前后条号集合不变」，与删哪一列无关。
    page.evaluate("""() => {
        const d = Alpine.$data(document.querySelector('.rvb-root'));
        window.tableEditor.deleteCols(d._tableEl(), [d._tableEl().querySelector('td')]);
    }""")
    blk.locator("button:has-text('保存')").first.click()
    assert poll_until(page, lambda: page.locator(".rvb-table-edit table").count() == 0, 5000), \
        "保存后没退出编辑态"

    # ★ `after` 必须带上**本次保存真正产出的编辑**（Task 6 复审的 Important 修复）：
    #   原写法两次都 POST `{edits: {}}` ⇒ 比较的是两个**完全相同**的请求，由端点确定性结构性保证，
    #   对「表格编辑是否改变切分」**零覆盖**（把上面整段表格编辑删掉，本行照样绿 —— 空转）。
    #   ⇒ 现在从组件里读回保存出来的 edits，用它当 after 的口径。
    stored = page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).edits")
    orig_html = page.evaluate("""() => Alpine.$data(document.querySelector('.rvb-root'))
        .blocks.find(b => b.label === 'table' && b.start_line !== null).md_html""")
    keys = list(stored.keys())
    # ★ **空转守卫**：编辑必须**真的存在**且**与原始表不同** —— 否则一旦保存路径改坏
    #   （e.g. 存了个跟原文一样的表），本用例又会退化成"两次同口径比较"而恒绿。
    assert len(keys) == 1, f"前置：保存后应恰好有一条编辑，实得 {keys}"
    edited_html = stored[keys[0]]
    assert edited_html and edited_html != orig_html, \
        f"前置：保存出来的表与原始表相同（表格编辑没生效 ⇒ 对账会空转）：{str(edited_html)[:120]}"
    after = _clause_nos(stored)
    assert after == before, f"表格编辑改变了切分：{before} -> {after}"


CASES["f14"] = [f14_1_round_trip_is_byte_identical,
                f14_2_serialize_strips_editor_artifacts,
                f14_3_validate_rejects_malformed_tables,
                f14_4_validate_accepts_well_formed_table,
                f14_5_entering_edit_on_a_table_shows_an_editable_table,
                f14_6_saving_an_edited_table_writes_the_md_region,
                f14_7_save_refuses_a_broken_table_and_keeps_editing,
                f14_8_insert_and_delete_row_keep_the_rectangle,
                f14_9_insert_and_delete_column_keep_the_rectangle,
                f14_10_merge_sets_spans_and_drops_swallowed_cells,
                f14_11_split_restores_empty_cells,
                f14_12_merge_on_a_single_cell_is_a_no_op,
                f14_13_remove_images_in_selected_cells,
                f14_14_align_applies_to_cell_text_only,
                f14_15_exit_criterion_image_row_can_be_removed,
                f14_16_saving_a_table_edit_does_not_change_the_clause_set]


# ═══════════════════════════════════════════
# f15：左栏拖框取图（P4 第一刀 · 原型）
# ═══════════════════════════════════════════
#
# ⚠️ 本仓无 JS 测试设施 ⇒ 探针是纯函数唯一的宿主；改 `image-picker.js` 必须同步跑 f15。
# 几何换算（选框的 CSS 像素 → crop 端点的 OCR 页面栅格）唯一的现场证明就在这里。
#
# ⚠️ 本组用例**一律钉确切值**，不写「反向等于正向」这类**对称性质** ——
#   一个恒返回 `{x:0,y:0,w:0,h:0}` 的实现同样满足对称性（假绿），
#   与 f13_2 的「方向钉」同一个理由。


def f15_1_image_picker_loaded(page):
    """前置：image-picker.js 已加载（否则后面全是空跑）"""
    page.goto(BASE)
    assert page.evaluate("() => !!(window.imagePicker && window.imagePicker.toGrid)"), \
        "image-picker.js 未加载（base.html 未引入，或 ?v=N 没递增）"


def f15_2_normalize_handles_reverse_drag(page):
    """★ 反向拖（右下→左上）归一化成正矩形；并钉住正向拖的确切值"""
    page.goto(BASE)
    fwd = page.evaluate("() => window.imagePicker.normalize(10, 20, 30, 50)")
    assert fwd == {"x": 10, "y": 20, "w": 20, "h": 30}, f"正向拖归一化不对：{fwd}"
    rev = page.evaluate("() => window.imagePicker.normalize(30, 50, 10, 20)")
    assert rev == fwd, f"反向拖未被归一化：{rev} != {fwd}"


def f15_3_clamp_rect_keeps_it_inside_the_page(page):
    """越界夹取：左/上出界、右/下出界、完全出界（⇒ 零尺寸，调用方据此丢弃）"""
    page.goto(BASE)
    a = page.evaluate("() => window.imagePicker.clampRect({x:-10,y:-5,w:50,h:40}, 200, 100)")
    assert a == {"x": 0, "y": 0, "w": 40, "h": 35}, f"左上出界夹取不对：{a}"
    b = page.evaluate("() => window.imagePicker.clampRect({x:180,y:90,w:50,h:40}, 200, 100)")
    assert b == {"x": 180, "y": 90, "w": 20, "h": 10}, f"右下出界夹取不对：{b}"
    c = page.evaluate("() => window.imagePicker.clampRect({x:300,y:300,w:50,h:40}, 200, 100)")
    assert c == {"x": 200, "y": 100, "w": 0, "h": 0}, f"完全出界应得零尺寸：{c}"


def f15_4_hit_handle_picks_corner_over_edge(page):
    """8 个手柄 + move + null；**角优先于边/框内**（否则贴着角拖会退化成拉边）"""
    page.goto(BASE)
    rect = {"x": 100, "y": 100, "w": 200, "h": 100}      # 右缘 300、下缘 200、中心 (200,150)

    def hit(px, py):
        return page.evaluate(
            "([r, px, py]) => window.imagePicker.hitHandle(r, px, py, 6)", [rect, px, py])

    assert hit(100, 100) == "nw"
    assert hit(300, 100) == "ne"
    assert hit(300, 200) == "se"
    assert hit(100, 200) == "sw"
    assert hit(200, 100) == "n"
    assert hit(300, 150) == "e"
    assert hit(200, 200) == "s"
    assert hit(100, 150) == "w"
    assert hit(200, 150) == "move", "框内应判为 move（可整体拖动）"
    assert hit(106, 106) == "nw", "角附近仍应是角（tol 为 ±tol 的方框，且角优先于 move）"
    assert hit(200, 40) is None, "框外（离上缘 60px）应判为 null"


def f15_5_resize_moves_only_the_dragged_edges(page):
    """各手柄只动它管的那条边；`move` 必须特判（`'move'.includes('e')` 为真的子串陷阱）"""
    page.goto(BASE)
    rect = {"x": 100, "y": 100, "w": 200, "h": 100}

    def rz(handle, px, py):
        return page.evaluate(
            "([r, h, px, py]) => window.imagePicker.resize(r, h, px, py)", [rect, handle, px, py])

    # 拉右缘：px 生效、py 是干扰量（故意给 999，若被用上就爆）
    assert rz("e", 350, 999) == {"x": 100, "y": 100, "w": 250, "h": 100}, rz("e", 350, 999)
    # 拉上缘：py 生效、px 是干扰量
    assert rz("n", 999, 60) == {"x": 100, "y": 60, "w": 200, "h": 140}, rz("n", 999, 60)
    # 左下角：左缘与下缘一起动
    assert rz("sw", 40, 260) == {"x": 40, "y": 100, "w": 260, "h": 160}, rz("sw", 40, 260)
    assert rz("move", 500, 500) == rect, "move 被当成了手柄（含 'e' 的子串陷阱）"


def f15_6_to_grid_converts_css_px_to_page_raster(page):
    """★ 几何的核心：选框 CSS 像素 → crop 端点的 OCR 栅格坐标。

    方向钉：元素宽 = 栅格宽的一半时，同样长度在栅格里必须**翻倍**
    —— 恒等映射与恒零映射都会被这条打红。"""
    page.goto(BASE)
    g = page.evaluate(
        "() => window.imagePicker.toGrid({x:50,y:25,w:100,h:50}, 400, 200, 800, 400)")
    assert g == {"x": 100, "y": 50, "w": 200, "h": 100}, f"2:1 换算不对：{g}"
    g1 = page.evaluate(
        "() => window.imagePicker.toGrid({x:50,y:25,w:100,h:50}, 800, 400, 800, 400)")
    assert g1 == {"x": 50, "y": 25, "w": 100, "h": 50}, f"1:1 不得改变数值：{g1}"
    assert page.evaluate(
        "() => window.imagePicker.toGrid({x:1,y:1,w:1,h:1}, 0, 0, 800, 400)") is None, \
        "元素尺寸为 0（页图未加载）应返回 null，而不是 NaN/除零"


def f15_7_is_usable_rejects_tiny_or_empty_rects(page):
    """碎拖与空框判否（否则会往 crop 端点送一个 1×1 的请求）"""
    page.goto(BASE)

    def ok(r):
        return page.evaluate("(r) => window.imagePicker.isUsable(r)", r)

    assert ok({"x": 10, "y": 10, "w": 40, "h": 30}) is True
    assert ok({"x": 10, "y": 10, "w": 2, "h": 30}) is False, "宽不够应判否"
    assert ok({"x": 10, "y": 10, "w": 40, "h": 2}) is False, "高不够应判否"
    assert ok({"x": 10, "y": 10, "w": 0, "h": 0}) is False
    assert ok(None) is False


# ── T2/T3：交互（需要 _rvb_seed 造夹具） ──────────────────────────────


def _rvb_pick(page):
    """取图相关状态快照"""
    return page.evaluate("""() => { const d = Alpine.$data(document.querySelector('.rvb-root'));
        return { mode: d.pickMode, rect: d.pickRect, preview: d.pickPreview,
                 msg: d.pickMsg }; }""")


def _rvb_setup_pick(page, suffix):
    """造夹具 + 放大到 200% 让视口**真的可滚** + 进入取图模式。

    ⚠️ 放大不是装饰：在「适应宽度」下视口往往**没有溢出** ⇒ 平移无处可去，
    「拖拽没有平移」这条断言会**空转假绿**。故本组一律先制造溢出并**断言溢出存在**。
    """
    tid = _rvb_seed(page, suffix)
    assert tid is not None, "前置不成立：缺少参考夹具"
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).setScaleMode(2.0)")
    over = page.evaluate("""() => { const v = document.querySelector('.rvb-viewport');
        return [v.scrollWidth - v.clientWidth, v.scrollHeight - v.clientHeight]; }""")
    assert over[0] > 10 or over[1] > 10, f"视口无溢出，本组用例会假绿：{over}"
    page.locator(".rvb-pick").click()
    assert _rvb_pick(page)["mode"] is True, "「取图」按钮没进入模式"
    return tid


def _drag(page, x0, y0, x1, y1):
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=5)
    page.mouse.up()


def f15_8_pick_drag_draws_rect_and_blocks_pan(page):
    """进取图模式后拖拽 = 画框，**且不得触发平移**（两者共用同一个 mousedown）"""
    _rvb_setup_pick(page, "p8")
    page.evaluate("""() => { const v = document.querySelector('.rvb-viewport');
        v.scrollLeft = 300; v.scrollTop = 0; }""")
    before = page.evaluate("""() => { const v = document.querySelector('.rvb-viewport');
        return [v.scrollLeft, v.scrollTop]; }""")
    vb = page.locator(".rvb-viewport").bounding_box()
    ib = page.locator(".rvb-img").first.bounding_box()
    sx = vb["x"] + vb["width"] * 0.50
    sy = vb["y"] + 60
    ex = vb["x"] + vb["width"] * 0.30
    ey = vb["y"] + 130
    _drag(page, sx, sy, ex, ey)
    st = _rvb_pick(page)
    assert st["rect"] is not None, "拖拽没有产生选框"
    # 选框以**页图左缘**为原点（与 crop 端点的栅格坐标同源）。
    # ⚠️ 取 min/max 而不是直接拿起点：本用例是**从右往左**拖的（0.50 → 0.30），
    #    归一化后左缘来自**终点**。直接拿起点会得到「原点错了」的假红。
    # 容差 2px：Chrome 把鼠标事件的 clientX/clientY **截断到整数**，亚像素拖不出来。
    assert abs(st["rect"]["x"] - (min(sx, ex) - ib["x"])) < 2, \
        f"选框左缘不对：{st['rect']} vs 图左缘 {ib['x']}"
    assert abs(st["rect"]["y"] - (min(sy, ey) - ib["y"])) < 2, \
        f"选框上缘不对：{st['rect']} vs 图顶 {ib['y']}"
    assert abs(st["rect"]["w"] - abs(ex - sx)) < 2, st["rect"]
    assert abs(st["rect"]["h"] - abs(ey - sy)) < 2, st["rect"]
    after = page.evaluate("""() => { const v = document.querySelector('.rvb-viewport');
        return [v.scrollLeft, v.scrollTop]; }""")
    assert after == before, f"取图模式下不应平移：{before} → {after}"


def f15_9_drag_still_pans_outside_pick_mode(page):
    """回归：非取图模式下拖拽**仍然**平移（别把原手势改坏）"""
    tid = _rvb_seed(page, "p9")
    assert tid is not None, "前置不成立"
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).setScaleMode(2.0)")
    assert page.evaluate("""() => { const v = document.querySelector('.rvb-viewport');
        return v.scrollWidth - v.clientWidth; }""") > 10, "视口不可横向滚动，本用例会假绿"
    # 高亮框会抢走 mousedown（startPan 在块上 early-return）⇒ 只测平移，先把框关掉，
    # 否则「起点恰好落在块上」会假红（那是 f12_4/f12_5 的覆盖面，不是本条）。
    page.evaluate("() => document.querySelectorAll('.rvb-box').forEach(e => e.style.display = 'none')")
    page.evaluate("() => { document.querySelector('.rvb-viewport').scrollLeft = 300; }")
    vb = page.locator(".rvb-viewport").bounding_box()
    _drag(page, vb["x"] + vb["width"] * 0.60, vb["y"] + 60,
          vb["x"] + vb["width"] * 0.40, vb["y"] + 60)
    sl = page.evaluate("() => document.querySelector('.rvb-viewport').scrollLeft")
    assert sl > 300, f"非取图模式下拖拽应平移（scrollLeft 应增大）：{sl}"
    assert _rvb_pick(page)["rect"] is None, "非取图模式不应留下选框"


def f15_10_reverse_drag_normalizes_and_tiny_drag_is_dropped(page):
    """边界：反向拖（右下→左上）与正向拖等价；碎拖**丢弃**（不留零尺寸框）"""
    _rvb_setup_pick(page, "p10")
    vb = page.locator(".rvb-viewport").bounding_box()
    ax, ay = vb["x"] + vb["width"] * 0.30, vb["y"] + 60
    bx, by = vb["x"] + vb["width"] * 0.55, vb["y"] + 150
    _drag(page, ax, ay, bx, by)
    fwd = _rvb_pick(page)["rect"]
    assert fwd is not None and fwd["w"] > 5, f"正向拖没出框：{fwd}"
    # 清空后反向再拖
    page.locator(".rvb-pick").click()      # 退出（顺带清空）
    page.locator(".rvb-pick").click()      # 再进
    _drag(page, bx, by, ax, ay)
    rev = _rvb_pick(page)["rect"]
    assert rev is not None, "反向拖没有产生选框"
    assert abs(rev["x"] - fwd["x"]) < 2 and abs(rev["y"] - fwd["y"]) < 2, f"反向拖原点没归一化：{rev} vs {fwd}"
    assert abs(rev["w"] - fwd["w"]) < 2 and abs(rev["h"] - fwd["h"]) < 2, f"反向拖尺寸没归一化：{rev} vs {fwd}"
    # 碎拖（2px）必须被丢弃
    page.locator(".rvb-pick").click()
    page.locator(".rvb-pick").click()
    _drag(page, ax, ay, ax + 2, ay + 2)
    assert _rvb_pick(page)["rect"] is None, "碎拖应被丢弃（零尺寸框会送 1×1 请求给 crop 端点）"


def f15_11_resize_handle_moves_only_its_edge(page):
    """调框：拉右下角 ⇒ 左上不动、右下跟着走"""
    _rvb_setup_pick(page, "p11")
    vb = page.locator(".rvb-viewport").bounding_box()
    ax, ay = vb["x"] + vb["width"] * 0.25, vb["y"] + 50
    bx, by = vb["x"] + vb["width"] * 0.50, vb["y"] + 120
    _drag(page, ax, ay, bx, by)
    r0 = _rvb_pick(page)["rect"]
    assert r0 is not None, "前置：没画出选框"
    ib = page.locator(".rvb-img").first.bounding_box()
    # 右下角在页面上的位置 = 图左缘 + r0.x + r0.w
    cx, cy = ib["x"] + r0["x"] + r0["w"], ib["y"] + r0["y"] + r0["h"]
    _drag(page, cx, cy, cx + 60, cy + 40)
    r1 = _rvb_pick(page)["rect"]
    assert r1 is not None, "拉角后选框消失了"
    assert abs(r1["x"] - r0["x"]) < 2 and abs(r1["y"] - r0["y"]) < 2, f"拉右下角不该动左上：{r0} → {r1}"
    assert r1["w"] > r0["w"] + 40 and r1["h"] > r0["h"] + 20, f"右下没跟着走：{r0} → {r1}"


def f15_12_toggling_pick_mode_clears_selection(page):
    """退出模式必须清空选框与预览（否则下次进来还挂着上一次的框）"""
    _rvb_setup_pick(page, "p12")
    vb = page.locator(".rvb-viewport").bounding_box()
    _drag(page, vb["x"] + vb["width"] * 0.3, vb["y"] + 60,
          vb["x"] + vb["width"] * 0.5, vb["y"] + 140)
    assert _rvb_pick(page)["rect"] is not None, "前置：没画出选框"
    page.locator(".rvb-pick").click()
    st = _rvb_pick(page)
    assert st["mode"] is False, "没退出模式"
    assert st["rect"] is None, f"退出后选框应清空：{st['rect']}"
    assert not st["preview"], "退出后预览应清空"


def _q(url):
    """把预览 URL 的 query 解析成 float 字典"""
    from urllib.parse import parse_qs, urlparse
    return {k: float(v[0]) for k, v in parse_qs(urlparse(url).query).items()}


def f15_13_confirm_maps_geometry_and_preview_really_loads(page):
    """★ 把几何**验穿**的那一条：**100% 档下 1 CSS 像素 ≡ 1 栅格像素**
    （因 `.rvb-canvas` 的 CSS 宽 ≡ 页栅格宽）⇒ 框 300×80、偏移 (40,30)，
    crop 端点收到的就该是同一组数。

    锚点为什么可靠：它**不依赖实现里的任何换算公式**，只用「100% 档 ⇒ 页图 CSS 宽 = 栅格宽」
    这个可由 DOM 直接量到的事实。换算若被写反/写错比例，这里立刻对不上。

    随后再验**端到端**：浮层里的图真的加载出来，且**输出尺寸 = 请求尺寸 × scale(1.5)**
    —— 端点是否真按我们给的矩形裁，只有这一步能证。"""
    tid = _rvb_seed(page, "p13")
    assert tid is not None, "前置不成立：缺少参考夹具"
    page.evaluate("() => Alpine.$data(document.querySelector('.rvb-root')).setScaleMode(1.0)")
    page.locator(".rvb-pick").click()
    page.evaluate("() => { const v = document.querySelector('.rvb-viewport'); v.scrollLeft = 0; v.scrollTop = 0; }")
    ib = page.locator(".rvb-img").first.bounding_box()
    pg = page.evaluate("""() => { const d = Alpine.$data(document.querySelector('.rvb-root'));
        const q = d.pages.find((x) => x.index === d.curPage); return {w: q.width, h: q.height}; }""")
    assert abs(ib["width"] - pg["w"]) < 1.5, \
        f"前置不成立：100% 档页图 CSS 宽应 ≡ 栅格宽（{ib['width']} vs {pg['w']}）"
    ox, oy = ib["x"] + 40, ib["y"] + 30
    _drag(page, ox, oy, ox + 300, oy + 80)
    page.mouse.click(ox + 150, oy + 40, button="right")
    # 取图会**先 fetch 验一次**（见 `_loadPreview`）⇒ 预览 URL 不再瞬时落地，必须等它出结果。
    # 判据要能区分「还在加载」与「已失败并给了提示」—— 否则会把失败误报成超时。
    assert poll_until(page, lambda: bool(_rvb_pick(page)["preview"])
                                  or bool(_rvb_pick(page)["msg"]), 15000), \
        "右键确认后既没有预览也没有提示（异步取图没有落地）"
    st = _rvb_pick(page)
    assert st["preview"], f"右键确认没有产出预览 URL：msg={st['msg']!r}"
    q = _q(st["preview"])
    # 容差 2：Chrome 把鼠标坐标截断到整数，亚像素拖不出来
    assert abs(q["x"] - 40) < 2 and abs(q["y"] - 30) < 2, f"原点折算不对：{q}（应约 40/30）"
    assert abs(q["w"] - 300) < 2 and abs(q["h"] - 80) < 2, f"尺寸折算不对：{q}（应约 300/80）"
    assert abs(q["scale"] - 1.5) < 1e-6, f"scale 应透传 1.5：{q}"
    # 端到端：浮层里的图真的加载出来（不是只有一个 URL）
    assert poll_until(page, lambda: page.evaluate(
        "() => { const im = document.querySelector('.rvb-pick-pop img');"
        " return !!im && im.naturalWidth > 0; }"), 15000), \
        "预览图没有加载出来（端点没返回有效 PNG，或浮层没接上）"
    nat = page.evaluate("""() => { const im = document.querySelector('.rvb-pick-pop img');
        return [im.naturalWidth, im.naturalHeight]; }""")
    assert abs(nat[0] - q["w"] * 1.5) <= 4 and abs(nat[1] - q["h"] * 1.5) <= 4, \
        f"输出尺寸与请求不符：{nat} vs 请求 {q['w']}×{q['h']} × 1.5"


def f15_14_no_selection_warns_and_mode_gates_contextmenu(page):
    """异常与边界：没框就确认 → 给提示而不是出预览；**非取图模式右键不被接管**。"""
    _rvb_setup_pick(page, "p14")
    vb = page.locator(".rvb-viewport").bounding_box()
    page.mouse.click(vb["x"] + vb["width"] * 0.4, vb["y"] + 80, button="right")
    st = _rvb_pick(page)
    assert not st["preview"], f"没有选框不该出预览：{st['preview']}"
    assert st["msg"], "没有选框应给出提示（否则用户不知道什么都没发生）"
    # 非取图模式：右键**不该被接管** —— 用 defaultPrevented 直接观测，
    # 而不是靠「没出预览」反推（那样把「没绑定」和「绑定了但没生效」混为一谈）。
    page.locator(".rvb-pick").click()          # 退出取图模式
    page.evaluate("""() => { window.__ctxDefault = null;
        document.addEventListener('contextmenu', (e) => { window.__ctxDefault = e.defaultPrevented; },
                                  { once: true }); }""")
    page.mouse.click(vb["x"] + vb["width"] * 0.4, vb["y"] + 80, button="right")
    page.wait_for_timeout(250)
    assert page.evaluate("() => window.__ctxDefault") is False, \
        "非取图模式下右键被 preventDefault 了（浏览器菜单被吃掉）"


def f15_15_crop_failure_is_surfaced_not_silent(page):
    """★ 2026-10-07 事故的回归：**任务在会话中途被清掉后，取图必须给可读原因，而不是一个白框**。

    当时的实测症状：取图成功一次之后，后续每次只剩「图片占位符」，界面**一个字都不说**
    （`pickMsg` 是空串）⇒ 用户误判成原型坏了。真相是任务被 TTL 清扫器删了，crop 端点
    返回 **404 `{"detail":"该页没有版面尺寸"}`**，`<img>` 拿不到图就只剩破图占位。

    这里用与清扫器**同一套**应用函数（`delete_task` + `_cleanup_task_artifacts`）制造该状态，
    而不是自己删文件 —— 避免"造出来的故障"与线上那一种不是同一回事。"""
    tid = _rvb_setup_pick(page, "p15")
    vb = page.locator(".rvb-viewport").bounding_box()
    ax, ay = vb["x"] + vb["width"] * 0.30, vb["y"] + 60

    # 前置：任务还在时取图正常（否则下面的断言可能因为别的原因通过）
    _drag(page, ax, ay, ax + 200, ay + 120)
    page.mouse.click(ax + 100, ay + 60, button="right")
    assert poll_until(page, lambda: page.evaluate(
        "() => { const im = document.querySelector('.rvb-pick-pop img');"
        " return !!im && im.naturalWidth > 0; }"), 15000), "前置不成立：任务还在时取图就没成功"

    # 模拟清扫器：删台账行 + 清磁盘产物
    from app.routes.import_routes import _cleanup_task_artifacts, delete_task
    _cleanup_task_artifacts(tid)
    delete_task(tid)

    _drag(page, ax, ay + 200, ax + 200, ay + 320)
    page.mouse.click(ax + 100, ay + 260, button="right")
    assert poll_until(page, lambda: bool(_rvb_pick(page)["msg"]), 10000), \
        "取图失败时**没有任何提示**（白框 —— 这正是 2026-10-07 事故的复现）"
    st = _rvb_pick(page)
    assert "404" in st["msg"], f"提示里应带上端点给的原因：{st['msg']!r}"
    assert not st["preview"], f"失败时不该显示预览 URL：{st['preview']}"
    assert page.locator(".rvb-pick-pop").is_hidden(), "失败时不该残留上一张预览浮层"


CASES["f15"] = [f15_1_image_picker_loaded,
                f15_2_normalize_handles_reverse_drag,
                f15_3_clamp_rect_keeps_it_inside_the_page,
                f15_4_hit_handle_picks_corner_over_edge,
                f15_5_resize_moves_only_the_dragged_edges,
                f15_6_to_grid_converts_css_px_to_page_raster,
                f15_7_is_usable_rejects_tiny_or_empty_rects,
                f15_8_pick_drag_draws_rect_and_blocks_pan,
                f15_9_drag_still_pans_outside_pick_mode,
                f15_10_reverse_drag_normalizes_and_tiny_drag_is_dropped,
                f15_11_resize_handle_moves_only_its_edge,
                f15_12_toggling_pick_mode_clears_selection,
                f15_13_confirm_maps_geometry_and_preview_really_loads,
                f15_14_no_selection_warns_and_mode_gates_contextmenu,
                f15_15_crop_failure_is_surfaced_not_silent]


def _require_isolated_paths():
    """开跑前自检**四条隔离路径真的生效**，否则**拒绝运行**。

    ⚠️ **这不是洁癖，是已经踩过的坑**：本套件会**真实上传文件、真实写 layout.json**。
    隔离变量没生效时它会直接写进**真实** `data/` ——
      · 2026-10-05 探针夹具 `2d64b993.pdf` 落进真实 `data/uploads` 与 `data/outputs`（事后人工清理）；
      · 2026-10-06 同一原因导致 `/layout` **404**（探针写 A 目录、服务端读 B 目录），
        整组用例以「版面列表为空」这种**误导性**方式红，排查了三轮才定位。
    成因是**命令写法**：把 `export ... && nohup uvicorn ... &` 写成一行时，`&` 作用在**整条链**上
    ⇒ 那些变量只进了**后台子 shell**，探针进程本身没有 ⇒ 回落到默认路径。
    ⇒ 与其靠人记住，不如在这里硬拦。
    """
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in sys.path:            # 本脚本是以**脚本**方式跑的，仓库根不在 sys.path 上
        sys.path.insert(0, _root)
    import app.config as cfg
    found = {"DATABASE_PATH": cfg.DATABASE_PATH, "UPLOAD_DIR": cfg.UPLOAD_DIR,
             "OUTPUT_DIR": cfg.OUTPUT_DIR, "LANCE_DB_PATH": cfg.LANCE_DB_PATH}
    bad = [f"{k} = {v}" for k, v in found.items() if "_probe_" not in str(v)]
    if bad:
        raise SystemExit(
            "探针隔离未生效，**拒绝运行**（否则会写进真实 data/）：\n  "
            + "\n  ".join(bad)
            + "\n请确认四条 export 在**前台**执行 —— 不要写成 `export ... && nohup uvicorn ... &`（`&` 会把整条链丢进后台子 shell）。\n"
            + "正确写法：先单独 export，再单独启动服务，最后单独跑本脚本。")


if __name__ == "__main__":
    _require_isolated_paths()
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
