"""P0 门禁验收：对真实产物检查**锚定覆盖率 + 逐字节相等 + 基准指纹**。

用法：D:/Python/python.exe scripts/verify_layout_anchor.py

退出码：
    0 = 全部达标
    1 = 有不达标产物
    2 = **一份可检查产物都没有**（fail-closed：没验证过就不算通过）

判据（与 tests/test_layout_anchor.py::test_anchor_real_fixture_gate 同一阈值）：
    - 未编辑 ⇒ 逐字节相等（结构性保证）
    - 残留下未命中率 ≤ 2%
    - `base_sha256` 与实际文本指纹一致（防「旧 span + 新文本」错配）
"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.ocr.layout import base_sha256, load_layout, splice  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUTPUTS = REPO / "data" / "outputs"
MAX_MISS_RATE = 0.02


def main() -> int:
    checked = failed = 0
    for d in sorted(OUTPUTS.glob("*/")):
        layout = load_layout(d)
        if layout is None:
            continue
        mds = list(d.glob("*.md"))
        if len(mds) != 1:
            print(f"跳过 {d.name}：期望恰好 1 个 .md，实有 {len(mds)}")
            continue

        checked += 1
        base = mds[0].read_text(encoding="utf-8")
        blocks = layout.get("blocks") or []
        st = layout.get("stats") or {}
        anchorable = st.get("anchorable", 0)
        missed = st.get("missed", 0)

        identical = splice(base, blocks, {}) == base
        rate = (missed / anchorable) if anchorable else 1.0
        fp_ok = layout.get("base_sha256") == base_sha256(base)
        ok = identical and fp_ok and anchorable > 0 and rate <= MAX_MISS_RATE

        print(("✅ " if ok else "❌ ")
              + f"{d.name}  可锚定 {anchorable} 未命中 {missed}（{rate:.2%}）"
                f" 逐字节相等={identical} 指纹={fp_ok}")
        if not ok:
            failed += 1

    print(f"\n共检查 {checked} 份，失败 {failed} 份")
    if checked == 0:
        print("❌ 没有可校验的成对产物 —— 先跑一次真实导入产生 layout.json。"
              "（门禁 fail-closed：未验证不算通过）")
        return 2
    return 1 if failed else 0


sys.exit(main())
