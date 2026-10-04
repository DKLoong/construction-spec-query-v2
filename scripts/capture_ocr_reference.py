"""一次性（可重跑）抓取 P0 参考夹具：OCR 原始 JSONL（**脱敏后**）+ 基准 markdown。

⚠️ 会调用**外部付费 OCR API**。仅在需要建立/刷新夹具时手动运行：

    D:/Python/python.exe scripts/capture_ocr_reference.py <pdf路径> [--pages 1,2,5]

⚠️ **落盘前必须脱敏**：`inputImage` / `preprocessedImages` / `outputImages` 都是
**带凭据的签名 URL**（`?authorization=bce-auth-v1...`）。`.gitignore` 只防提交，
不防磁盘、备份与分发打包 ⇒ 在**内存中**完成断言与替换，再写盘。

产出（`data/` 已 gitignore ⇒ 不入库）：
    data/_ocr_reference/raw.jsonl      已脱敏的原始 JSONL
    data/_ocr_reference/reference.md   基准 markdown（**未清洗**，见计划 §0）
"""
import argparse
import asyncio
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import fitz  # noqa: E402

from app.ocr.paddle_api import PaddleVLClient, create_ocr_client  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "data" / "_ocr_reference"

# 字符类排除引号；故匹配在 JSON 字符串的收尾引号**之前**停下，
# 替换成裸占位符后 JSON 里仍是合法的 "<redacted-signed-url>"。
_SIGNED_URL = re.compile(r'https?://[^"\s\\]*authorization=[^"\s\\]*')


def _scrub(raw: str) -> tuple[str, int]:
    """把所有签名 URL 替换为占位符，返回 (脱敏文本, 替换次数)。"""
    return _SIGNED_URL.sub("<redacted-signed-url>", raw), len(_SIGNED_URL.findall(raw))


def _slice(src: pathlib.Path, pages: list[int]) -> pathlib.Path:
    """把指定页（1-based）抽成多页 PDF；`pages` 为空则用整本。"""
    if not pages:
        return src
    doc = fitz.open(str(src))
    try:
        out = fitz.open()
        for p in pages:
            if not 1 <= p <= len(doc):
                raise SystemExit(f"页号越界：{p}（该 PDF 共 {len(doc)} 页）")
            out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        tmp = OUT_DIR / "_slice.pdf"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(tmp))
        out.close()
        return tmp
    finally:
        doc.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="", help="1-based 页号，逗号分隔；留空=整本")
    args = ap.parse_args()

    src = pathlib.Path(args.pdf)
    pages = [int(x) for x in args.pages.split(",") if x.strip()]
    client = create_ocr_client()
    if not isinstance(client, PaddleVLClient):
        raise SystemExit(f"当前 OCR 后端不是 PaddleOCR-VL：{type(client).__name__}")

    target = _slice(src, pages)
    print(f"来源 {src.name}，页数={'整本' if not pages else len(pages)}")

    job = await client._submit_task(str(target))
    print(f"job_id = {job}（等待中…，会调外部 API）")
    data = await client._poll_result(job, max_wait=900)
    json_url = (data.get("resultUrl") or {}).get("jsonUrl", "")
    if not json_url:
        raise SystemExit("OCR 结果缺少 resultUrl.jsonUrl")

    import httpx
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(json_url)
        resp.raise_for_status()
        raw = resp.text

    # ① 先在**内存中**证明拿到了真响应（有签名 URL），再脱敏落盘
    scrubbed, n_signed = _scrub(raw)
    if n_signed == 0:
        raise SystemExit("响应里没有签名 URL —— 夹具不是真响应，停下来报告")
    if "authorization=" in scrubbed:
        raise SystemExit("脱敏不彻底，拒绝落盘")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "raw.jsonl").write_text(scrubbed, encoding="utf-8")

    # ② 基准 markdown：用**现有生产规则**拼接（逐行 layoutParsingResults[].markdown.text）
    parts = []
    for line in scrubbed.strip().split("\n"):
        if not line.strip():
            continue
        for lp in (json.loads(line).get("result") or {}).get("layoutParsingResults") or []:
            t = (lp.get("markdown") or {}).get("text")
            if t:
                parts.append(t)
    reference = "\n".join(parts)
    (OUT_DIR / "reference.md").write_text(reference, encoding="utf-8")

    print(f"✅ 已脱敏 {n_signed} 处签名 URL；reference.md = {len(reference)} 字符")
    if not pages:
        (OUT_DIR / f"{src.stem}.md").write_text(reference, encoding="utf-8")
        print(f"✅ 另存一份可直接导入的 md：{OUT_DIR / (src.stem + '.md')}")


asyncio.run(main())
