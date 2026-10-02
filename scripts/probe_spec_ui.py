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
# 但一旦整页跳转，文档销毁、task_id 无处可寻：服务端 progress_store 还在，
# 客户端却再没有把手，于是「导入期间不能干别的事」。
# 修复：base.html 全局加载 import-tracker.js，用 sessionStorage 记住 task_id，
# 在任意页面恢复为常驻小浮标。
#
# ⚠ 用例一律用 page.route 桩掉 /json 端点。真实导入是分钟级且依赖 OCR/向量模型，
# 无法在用例里稳定制造「进行中」那一瞬间；而被测对象本就是浮标的状态机。
# 桩掉后四个状态可确定性驱动。四条用例全部以「跨页」为现场（先落到 /specs 预置
# task_id，再跳到别页），因为跨页恢复正是本组的主题。

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
    """未知任务（服务重启后 progress_store 清空）属**正常**路径：浮标须自清

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


def _upload_probe_pdf(page, code, title):
    """经导入弹窗**真实**上传一份带文本层的极小 PDF（Phase 1 会停在 review_needed）

    抽成公用：f8_6（验响应头接线）与 f10（验审查页滚动）都要先造一个真实待审查任务。
    非文本层 PDF 会触发真实 OCR 请求，探针不能碰——见 _make_text_pdf 的说明。
    """
    import tempfile
    from pathlib import Path as _Path
    tmp_pdf = _Path(tempfile.mkdtemp()) / f"{code.replace(' ', '-')}.pdf"
    _make_text_pdf(str(tmp_pdf))
    _open_import_dialog(page)
    page.set_input_files("input[name=file]", str(tmp_pdf))
    page.fill("input[name=code]", code)
    page.fill("input[name=title]", title)
    page.click(".dialog-box--closable button[type=submit]")
    # 这一步即「响应头 X-Import-Task-Id → import-tracker → sessionStorage」整条接线
    assert poll_until(page, lambda: page.evaluate(
        "() => !!sessionStorage.getItem('importTaskId')"), 15000), \
        "上传后任务号未落到 sessionStorage（响应头 X-Import-Task-Id 未接通？）"
    return page.evaluate("() => sessionStorage.getItem('importTaskId')")


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

def _make_review_task(page, code, title):
    """造一个真实待审查任务并回到干净状态，返回 task_id"""
    task_id = _upload_probe_pdf(page, code, title)
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


CASES["f10"] = [f10_1_confirm_scrolls_to_bottom_before_request]


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


CASES["f8"] = [f8_1_floater_survives_navigation,
               f8_2_review_needed_offers_entry_and_keeps_task,
               f8_3_terminal_state_clears_floater_and_task,
               f8_4_unknown_task_clears_floater,
               f8_5_floater_yields_to_open_import_dialog,
               f8_6_real_upload_seeds_tracker_and_survives_navigation]


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
