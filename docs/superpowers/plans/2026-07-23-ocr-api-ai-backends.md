# OCR API 化 + AI 多后端 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 OCR 从本地 PaddleOCR 改为百度 AI Studio 云端 API，AI 问答支持 CLI + 豆包/DeepSeek/GLM/Kimi/自定义多后端，新增统一设置弹窗。

**Architecture:** 策略模式扩展 —— OCR 新增 `PaddleStudioAPI` HTTP 客户端，AI 新增 `APIBackend(CLIBackend)` 兼容 OpenAI 接口，设置模块用新 `settings` 表 + REST API + Alpine.js 弹窗。

**Tech Stack:** FastAPI + httpx + SQLite + Alpine.js + HTMX

## Global Constraints

- Python 3.14，路径 `D:/Python/python.exe`
- 所有测试命令: `D:/Python/python.exe -m pytest tests/ -v`
- 移除此依赖: `PaddleOCR>=3.0.0`, `paddlepaddle>=3.0.0`
- `httpx>=0.28.0` 已在 requirements.txt 中，可直接使用
- settings 表 key-value 格式，key 为 TEXT PRIMARY KEY
- APIBackend 必须继承 CLIBackend，保持接口一致
- 设置弹窗复用现有 Alpine.js + HTMX 模式，不与 QA/条文弹窗冲突

---

### Task 1: 数据库 — 新增 settings 表

**Files:**
- Modify: `app/database.py:135-143`

**Interfaces:**
- Produces: `settings` 表 (key TEXT PK, value TEXT)，通过 `init_db()` 自动创建

- [ ] **Step 1: 在 SCHEMA_SQL 中添加 settings 表定义**

```python
# app/database.py — 在 SCHEMA_SQL 字符串末尾、users 表之后添加：

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT DEFAULT ''
);
```

- [ ] **Step 2: 运行数据库初始化验证表创建成功**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -c "
from app.database import init_db, get_db
init_db()
with get_db() as conn:
    conn.execute(\"INSERT OR REPLACE INTO settings (key, value) VALUES ('test', 'ok')\")
    r = conn.execute(\"SELECT value FROM settings WHERE key='test'\").fetchone()
    print('PASS' if r['value'] == 'ok' else 'FAIL')
"
```

Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add app/database.py
git commit -m "feat: 新增 settings 表"
```

---

### Task 2: OCR API 模块 — PaddleStudioAPI 客户端

**Files:**
- Create: `app/ocr/paddle_api.py`
- Delete: `app/ocr/paddle_ocr.py`
- Modify: `tests/test_ocr.py`
- Modify: `requirements.txt:9-10`

**Interfaces:**
- Consumes: `settings` 表 (key=`ocr.access_token`)，`app.ocr.pdf_extract` (不变)
- Produces: `PaddleStudioAPI(access_token)` 类，方法 `ocr_image(img_path) -> str`、`ocr_pdf_to_md(pdf_path, output_dir=None) -> str`
- Settings helper: `get_setting(key: str) -> str` 从 settings 表读取

- [ ] **Step 1: 编写 settings 读取辅助函数测试**

```python
# tests/test_ocr.py — 替换全部内容：

def test_get_setting_returns_value(monkeypatch, tmp_path):
    """get_setting 从数据库读取值"""
    db_path = tmp_path / "test_settings.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'abc123')")

    from app.ocr.paddle_api import get_setting
    assert get_setting("ocr.access_token") == "abc123"


def test_get_setting_missing_key_returns_empty(monkeypatch, tmp_path):
    """不存在的 key 返回空字符串"""
    db_path = tmp_path / "test_settings_empty.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    from app.ocr.paddle_api import get_setting
    assert get_setting("nonexistent") == ""


def test_paddle_studio_api_ocr_image(monkeypatch, tmp_path):
    """ocr_image 调用 AI Studio API 并返回文本"""
    db_path = tmp_path / "test_api.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'test-token')")

    from app.ocr.paddle_api import PaddleStudioAPI
    api = PaddleStudioAPI(access_token="test-token")

    # 用 monkeypatch 模拟 httpx 响应
    import httpx

    class MockResponse:
        status_code = 200
        def json(self):
            return {"words_result": [{"words": "第一条"}, {"words": "第二条"}],
                    "words_result_num": 2}
        def raise_for_status(self):
            pass

    async def mock_post(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    import asyncio
    text = asyncio.run(api._ocr_image_async("/fake/path.png"))
    assert "第一条" in text
    assert "第二条" in text


def test_pdf_extract_import():
    """pdf_extract 模块不受影响"""
    from app.ocr.pdf_extract import extract_text, is_scanned
    assert callable(extract_text)
    assert callable(is_scanned)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_ocr.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.ocr.paddle_api'`

- [ ] **Step 3: 创建 `app/ocr/paddle_api.py`**

```python
"""百度 AI Studio OCR API 客户端"""
import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 单页处理上限
_MAX_PAGES = 100


def get_setting(key: str) -> str:
    """从 settings 表读取配置值"""
    from app.database import get_db
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else ""
    except Exception:
        return ""


class PaddleStudioAPI:
    """百度 AI Studio OCR API 客户端"""

    BASE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1"

    def __init__(self, access_token: str):
        self.access_token = access_token

    async def _ocr_image_async(self, img_path: str) -> str:
        """对单张图片执行 OCR（accurate_basic），返回识别文本"""
        import httpx

        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        url = f"{self.BASE_URL}/accurate_basic"
        params = {"access_token": self.access_token}
        payload = {
            "image": img_b64,
            "language_type": "CHN_ENG",
            "detect_direction": "true",
            "paragraph": "false",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params=params, data=payload)
            resp.raise_for_status()
            data = resp.json()

        if "error_code" in data:
            error_msg = data.get("error_msg", "未知错误")
            logger.error("OCR API 错误 (code=%s): %s", data["error_code"], error_msg)
            raise RuntimeError(f"OCR API 返回错误: {error_msg}")

        words_result = data.get("words_result", [])
        lines = [item.get("words", "") for item in words_result]
        return "\n".join(lines)

    def ocr_image(self, img_path: str) -> str:
        """同步封装（供后台任务调用）"""
        import asyncio
        return asyncio.run(self._ocr_image_async(img_path))

    def ocr_pdf_to_md(self, pdf_path: str, output_dir: str | None = None) -> str:
        """PDF 逐页渲染 PNG → 每页调 OCR API → 拼 Markdown

        最多处理前 _MAX_PAGES 页，超出部分忽略。
        """
        import fitz
        from app.config import OUTPUT_DIR

        if output_dir is None:
            output_dir = str(Path(OUTPUT_DIR) / Path(pdf_path).stem)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        md_path = str(Path(output_dir) / f"{Path(pdf_path).stem}.md")
        doc = fitz.open(pdf_path)
        total_pages = min(len(doc), _MAX_PAGES)
        if len(doc) > _MAX_PAGES:
            logger.warning(
                "PDF 共 %d 页，超过 %d 页上限，仅处理前 %d 页",
                len(doc), _MAX_PAGES, _MAX_PAGES,
            )

        md_parts = []
        for i in range(total_pages):
            page = doc[i]
            pix = page.get_pixmap(dpi=200)
            img_path = str(Path(output_dir) / f"page_{i + 1:04d}.png")
            pix.save(img_path)
            try:
                text = self.ocr_image(img_path)
                md_parts.append(f"## 第{i + 1}页\n\n{text}\n")
            except Exception as e:
                md_parts.append(f"## 第{i + 1}页\n\n_[OCR 失败: {e}]_\n")
            finally:
                Path(img_path).unlink(missing_ok=True)

        doc.close()

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_parts))

        return md_path
```

- [ ] **Step 4: 删除旧 OCR 模块**

```bash
rm app/ocr/paddle_ocr.py
```

- [ ] **Step 5: 移除 requirements.txt 中的 PaddleOCR 依赖**

```python
# requirements.txt — 删除这两行：
# PaddleOCR>=3.0.0     ← 删除
# paddlepaddle>=3.0.0  ← 删除
```

- [ ] **Step 6: 运行测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_ocr.py -v
```

Expected: 4 tests PASS

- [ ] **Step 7: Commit**

```bash
git add app/ocr/paddle_api.py tests/test_ocr.py requirements.txt
git rm app/ocr/paddle_ocr.py
git commit -m "feat: PaddleOCR 本地模型 → 百度 AI Studio API"
```

---

### Task 3: 改造导入流程使用新 OCR API

**Files:**
- Modify: `app/routes/import_routes.py:93-95`

**Interfaces:**
- Consumes: `PaddleStudioAPI` from `app.ocr.paddle_api`，`get_setting` from `app.ocr.paddle_api`
- Produces: 无新增接口, 内部替换 OCR 调用路径

- [ ] **Step 1: 修改 `_process_import()` 中的 OCR 调用**

将第 93-96 行：
```python
progress_store[task_id].update(progress=20, message="正在 OCR 识别...")
from app.ocr.paddle_ocr import ocr_pdf_to_md
md_path = ocr_pdf_to_md(file_path)
md_text = Path(md_path).read_text(encoding="utf-8")
```

改为：
```python
progress_store[task_id].update(progress=20, message="正在 OCR 识别...")
from app.ocr.paddle_api import PaddleStudioAPI, get_setting
access_token = get_setting("ocr.access_token")
if not access_token:
    progress_store[task_id].update(
        status="error", progress=0,
        message="OCR API 令牌未配置，请在设置页面配置百度 AI Studio access_token"
    )
    return
api = PaddleStudioAPI(access_token=access_token)
md_path = api.ocr_pdf_to_md(file_path)
md_text = Path(md_path).read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行导入相关测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_import.py tests/test_ocr_review.py -v
```

Expected: 已有测试 PASS (这些测试可能不直接触发 OCR 路径，但应确保无 import 错误)

- [ ] **Step 3: 验证 import 链完整**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -c "
from app.ocr.paddle_api import PaddleStudioAPI, get_setting
print('Import OK')
"
```

Expected: `Import OK`

- [ ] **Step 4: Commit**

```bash
git add app/routes/import_routes.py
git commit -m "feat: 导入流程改用 PaddleStudioAPI"
```

---

### Task 4: AI API 客户端 + 厂商预置

**Files:**
- Create: `app/ai/api_client.py`
- Create: `app/ai/provider_presets.py`
- Create: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `CLIBackend` from `app.ai.cli_client`，`settings` 表
- Produces: `APIBackend(CLIBackend)` 类，`PROVIDERS` 字典

- [ ] **Step 1: 编写测试**

```python
# tests/test_api_client.py
import pytest


def test_api_backend_import():
    """APIBackend 可导入"""
    from app.ai.api_client import APIBackend
    assert APIBackend is not None


def test_api_backend_inherits_cli_backend():
    """APIBackend 继承 CLIBackend"""
    from app.ai.api_client import APIBackend
    from app.ai.cli_client import CLIBackend
    assert issubclass(APIBackend, CLIBackend)


def test_api_backend_is_available_with_key():
    """有 api_key 时 is_available() 返回 True"""
    from app.ai.api_client import APIBackend
    backend = APIBackend(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )
    assert backend.is_available() is True


def test_api_backend_is_not_available_without_key():
    """无 api_key 时 is_available() 返回 False"""
    from app.ai.api_client import APIBackend
    backend = APIBackend(
        base_url="https://api.example.com/v1",
        api_key="",
        model="test-model",
    )
    assert backend.is_available() is False


def test_api_backend_ask_makes_http_call(monkeypatch):
    """ask() 发送 HTTP POST 到 /chat/completions"""
    from app.ai.api_client import APIBackend

    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "choices": [{
                    "message": {"content": "根据规范，模板应能承受混凝土侧压力。"}
                }]
            }
        def raise_for_status(self):
            pass

    import httpx
    captured_url = []
    captured_json = []
    captured_headers = []

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json=None, headers=None, timeout=None):
            captured_url.append(url)
            captured_json.append(json)
            captured_headers.append(headers)
            return MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    backend = APIBackend(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-chat",
    )

    import asyncio
    resp = asyncio.run(backend.ask(
        prompt="模板设计要求",
        context="[GB 50204 5.1.1] 模板应能承受混凝土侧压力。",
    ))

    assert resp.success is True
    assert "模板" in resp.content
    assert captured_url[0] == "https://api.deepseek.com/chat/completions"
    assert captured_json[0]["model"] == "deepseek-chat"
    assert captured_headers[0]["Authorization"] == "Bearer sk-test"


def test_provider_presets():
    """PROVIDERS 包含所有预置厂商"""
    from app.ai.provider_presets import PROVIDERS
    assert "doubao" in PROVIDERS
    assert "deepseek" in PROVIDERS
    assert "glm" in PROVIDERS
    assert "kimi" in PROVIDERS
    assert PROVIDERS["doubao"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert PROVIDERS["glm"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert PROVIDERS["kimi"]["base_url"] == "https://api.moonshot.cn/v1"


def test_api_backend_handles_429_error(monkeypatch):
    """429 错误返回友好的错误信息"""
    from app.ai.api_client import APIBackend

    class MockErrorResponse:
        status_code = 429
        def json(self):
            return {"error": {"message": "Rate limit exceeded"}}
        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("429", request=None, response=self)

    import httpx
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return MockErrorResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)

    backend = APIBackend("https://api.test.com", "sk-test", "model")
    import asyncio
    resp = asyncio.run(backend.ask(prompt="test"))
    assert resp.success is False
    assert "429" in resp.error or "超限" in resp.error
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_api_client.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 `app/ai/provider_presets.py`**

```python
"""AI 厂商预置配置"""

PROVIDERS = {
    "doubao": {
        "name": "豆包 (字节跳动)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-1.5-pro-32k",
        "setting_key_prefix": "ai.doubao",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "setting_key_prefix": "ai.deepseek",
    },
    "glm": {
        "name": "GLM (智谱)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "setting_key_prefix": "ai.glm",
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "setting_key_prefix": "ai.kimi",
    },
}
```

- [ ] **Step 4: 创建 `app/ai/api_client.py`**

```python
"""OpenAI 兼容 API 后端"""
import logging
from app.ai.cli_client import CLIBackend, CLIResponse

logger = logging.getLogger(__name__)


class APIBackend(CLIBackend):
    """OpenAI 兼容 API 后端 — 支持豆包/DeepSeek/GLM/Kimi/自定义"""

    command = "api"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        """HTTP POST 调用 /chat/completions"""
        import time
        import httpx

        full_prompt = prompt
        if context:
            full_prompt = (
                f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
            )

        messages = [
            {
                "role": "system",
                "content": "你是建筑施工规范查询助手。只根据提供的上下文回答，不要编造规范条文。如果上下文中没有相关信息，请如实告知。回答请使用中文。",
            },
            {"role": "user", "content": full_prompt},
        ]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": 2048,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                duration = (time.time() - start) * 1000
                return CLIResponse(
                    success=True, content=content, duration_ms=duration,
                )
        except httpx.HTTPStatusError as e:
            duration = (time.time() - start) * 1000
            status_code = e.response.status_code
            if status_code == 429:
                return CLIResponse(
                    success=False, content="",
                    error="API 调用超限 (429)，请检查当日配额或稍后重试",
                    duration_ms=duration,
                )
            return CLIResponse(
                success=False, content="",
                error=f"API 返回错误 (HTTP {status_code})",
                duration_ms=duration,
            )
        except httpx.TimeoutException:
            duration = (time.time() - start) * 1000
            return CLIResponse(
                success=False, content="",
                error="API 调用超时",
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return CLIResponse(
                success=False, content="",
                error=f"API 调用失败: {e}",
                duration_ms=duration,
            )

    def classify_batch_sync(self, clauses, dimension) -> list:
        """同步分类 — 不走 API，回退到基类默认"""
        from app.ai.cli_client import ClassifyResult

        items = "\n".join(
            f"{i+1}. [ID:{c['clause_id']}] {c['content'][:200]}"
            for i, c in enumerate(clauses)
        )
        dim_labels = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}
        dim_label = dim_labels.get(dimension, dimension)

        import asyncio
        resp = asyncio.run(self.ask(
            prompt=f"请以 JSON 格式返回分类结果: "
                   f'[{{"clause_id": <id>, "label": "<分类标签>", "confidence": <0.0-1.0>}}]',
            context=f"你是施工规范分类助手。为以下条文标注{dim_label}维度。\n{items}",
        ))

        if resp.success:
            import json
            try:
                text = resp.content
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    return [
                        ClassifyResult(
                            clause_id=item["clause_id"],
                            label=item.get("label", ""),
                            confidence=item.get("confidence", 0.5),
                        )
                        for item in data
                    ]
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"API 返回解析失败: {e}")
        return []
```

- [ ] **Step 5: 运行测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_api_client.py -v
```

Expected: 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/ai/api_client.py app/ai/provider_presets.py tests/test_api_client.py
git commit -m "feat: AI 云 API 后端 + 四家厂商预置"
```

---

### Task 5: 改造 get_backend() 工厂支持多后端

**Files:**
- Modify: `app/ai/cli_client.py:125-128`

**Interfaces:**
- Consumes: `PROVIDERS` from `app.ai.provider_presets`，`APIBackend` from `app.ai.api_client`，`settings` 表
- Modifies: `get_backend(name)` → `get_backend(name=None)` 支持所有后端

- [ ] **Step 1: 编写测试**

```python
# tests/test_cli_client.py — 在文件末尾追加：

def test_get_backend_claude():
    """默认返回 ClaudeCodeCLI"""
    from app.ai.cli_client import get_backend, ClaudeCodeCLI
    backend = get_backend("claude")
    assert isinstance(backend, ClaudeCodeCLI)


def test_get_backend_codex():
    """codex 返回 CodexCLI"""
    from app.ai.cli_client import get_backend, CodexCLI
    backend = get_backend("codex")
    assert isinstance(backend, CodexCLI)


def test_get_backend_api(monkeypatch, tmp_path):
    """doubao 等返回 APIBackend"""
    db_path = tmp_path / "test_api_backend.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.doubao.api_key', 'sk-test')"
        )

    from app.ai.cli_client import get_backend
    from app.ai.api_client import APIBackend
    backend = get_backend("doubao")
    assert isinstance(backend, APIBackend)
    assert backend.model == "doubao-1.5-pro-32k"


def test_get_backend_custom(monkeypatch, tmp_path):
    """custom 读取自定义配置"""
    db_path = tmp_path / "test_custom.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.base_url', 'https://my.api.com/v1')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.api_key', 'sk-custom')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.custom.model', 'my-model')"
        )

    from app.ai.cli_client import get_backend
    from app.ai.api_client import APIBackend
    backend = get_backend("custom")
    assert isinstance(backend, APIBackend)
    assert backend.base_url == "https://my.api.com/v1"
    assert backend.model == "my-model"


def test_get_backend_default_from_settings(monkeypatch, tmp_path):
    """不传 name 时从 settings 读取 ai.backend"""
    db_path = tmp_path / "test_default.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('ai.backend', 'codex')")

    from app.ai.cli_client import get_backend, CodexCLI
    backend = get_backend()  # 不传参数
    assert isinstance(backend, CodexCLI)
```

- [ ] **Step 2: 修改 `get_backend()`**

```python
# app/ai/cli_client.py — 替换 get_backend 函数：

def get_backend(name: str | None = None) -> CLIBackend:
    """获取 AI 后端实例

    Args:
        name: 后端名称 (claude/codex/doubao/deepseek/glm/kimi/custom)
              为空时从 settings 表读取 ai.backend
    """
    from app.ocr.paddle_api import get_setting

    if name is None:
        name = get_setting("ai.backend") or "claude"

    if name == "codex":
        return CodexCLI()
    elif name == "claude":
        return ClaudeCodeCLI()
    elif name == "custom":
        from app.ai.api_client import APIBackend
        return APIBackend(
            base_url=get_setting("ai.custom.base_url"),
            api_key=get_setting("ai.custom.api_key"),
            model=get_setting("ai.custom.model") or "gpt-3.5-turbo",
        )
    else:
        # 预置厂商 (doubao/deepseek/glm/kimi)
        from app.ai.provider_presets import PROVIDERS
        from app.ai.api_client import APIBackend

        preset = PROVIDERS.get(name)
        if preset:
            api_key = get_setting(f"{preset['setting_key_prefix']}.api_key")
            return APIBackend(
                base_url=preset["base_url"],
                api_key=api_key,
                model=preset["default_model"],
            )
        # 未知后端，回退到默认
        return ClaudeCodeCLI()
```

- [ ] **Step 3: 运行测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_cli_client.py -v
```

Expected: 原有测试 + 5 个新测试 PASS

- [ ] **Step 4: Commit**

```bash
git add app/ai/cli_client.py tests/test_cli_client.py
git commit -m "feat: get_backend() 工厂支持全部 AI 后端"
```

---

### Task 6: 改造 QA 路由适配多后端

**Files:**
- Modify: `app/routes/qa_routes.py:97`
- Modify: `app/models.py:193-195`

**Interfaces:**
- Consumes: `get_backend()` (新签名)，`QaRequest.backend` 字段
- Modifies: QaRequest 模型增加后端选项

- [ ] **Step 1: 更新 QaRequest 模型**

```python
# app/models.py — 替换 QaRequest：

class QaRequest(BaseModel):
    question: str
    backend: str = "claude"  # claude | codex | doubao | deepseek | glm | kimi | custom
```

模型无需改动（字段签名已兼容），但需确保 `backend` 验证允许新增值。

- [ ] **Step 2: 修改 qa_routes.py 后端调用**

```python
# app/routes/qa_routes.py — 第 97 行：

# 旧：
backend = get_backend(body.backend)

# 新：
backend = get_backend(body.backend)

cli_used = backend.command if backend.command != "api" else body.backend
```

注意：`APIBackend.command == "api"`，需用 `body.backend` 替代作为 `cli_used` 的值返回。

实际上 `get_backend` 的函数签名已经改为 `name: str | None = None`，兼容旧的 `get_backend(body.backend)` 调用方式。

- [ ] **Step 3: 修改 qa_routes.py 中 cli_used 的处理**

在第 98 行附近：
```python
# 旧：
cli_used = backend.command

# 新：
if isinstance(backend, APIBackend):
    from app.ai.provider_presets import PROVIDERS
    preset = PROVIDERS.get(body.backend)
    cli_used = preset["name"] if preset else "自定义"
else:
    cli_used = backend.command
```

同时需要在文件头部添加 import：
```python
from app.ai.api_client import APIBackend
```

- [ ] **Step 4: 运行 QA 测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_qa_routes.py -v
```

Expected: 8 tests PASS (这些测试 mock 了 CLI 行为，新增后端的改动不应破坏现有测试)

- [ ] **Step 5: Commit**

```bash
git add app/routes/qa_routes.py app/models.py
git commit -m "feat: QA 路由适配多后端选择"
```

---

### Task 7: 设置 API 路由

**Files:**
- Create: `app/routes/settings_routes.py`
- Create: `tests/test_settings_routes.py`

**Interfaces:**
- Consumes: `settings` 表
- Produces: `GET /settings` (JSON), `PUT /settings` (JSON), `POST /settings/test-ocr`, `POST /settings/test-ai`

- [ ] **Step 1: 编写测试**

```python
# tests/test_settings_routes.py
import pytest


def test_get_settings_requires_auth(client):
    """未登录不能访问设置"""
    resp = client.get("/settings")
    assert resp.status_code == 302


def test_get_settings_returns_json(auth_client, monkeypatch, tmp_path):
    """获取设置返回 JSON"""
    db_path = tmp_path / "test_sett.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ocr.access_token', 'abc')"
        )

    resp = auth_client.get("/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ocr.access_token"] == "abc"


def test_put_settings_saves_values(auth_client, monkeypatch, tmp_path):
    """批量保存设置"""
    db_path = tmp_path / "test_sett2.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.put("/settings", json={
        "ocr.access_token": "new-token",
        "ai.backend": "deepseek",
    })
    assert resp.status_code == 200

    # 验证持久化
    resp2 = auth_client.get("/settings")
    data2 = resp2.json()
    assert data2["ocr.access_token"] == "new-token"
    assert data2["ai.backend"] == "deepseek"


def test_put_settings_removes_old_key(auth_client, monkeypatch, tmp_path):
    """更新后旧值被覆盖"""
    db_path = tmp_path / "test_sett3.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db, get_db
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('ai.backend', 'claude')"
        )

    resp = auth_client.put("/settings", json={"ai.backend": "kimi"})
    assert resp.status_code == 200

    resp2 = auth_client.get("/settings")
    assert resp2.json()["ai.backend"] == "kimi"


def test_test_ocr_no_token(auth_client, monkeypatch, tmp_path):
    """无 token 时测试 OCR 连接返回错误"""
    db_path = tmp_path / "test_sett4.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.post("/settings/test-ocr")
    assert resp.status_code == 400
    assert "请先配置" in resp.json()["detail"]


def test_test_ai_no_key(auth_client, monkeypatch, tmp_path):
    """无 api_key 时测试 AI 连接返回错误"""
    db_path = tmp_path / "test_sett5.db"
    monkeypatch.setattr("app.database.DATABASE_PATH", str(db_path))
    from app.database import init_db
    init_db()

    resp = auth_client.post("/settings/test-ai", json={
        "backend": "deepseek", "api_key": "",
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_settings_routes.py -v
```

Expected: FAIL — `ModuleNotFoundError` 或 404

- [ ] **Step 3: 创建 `app/routes/settings_routes.py`**

```python
"""设置管理路由"""
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/settings")
async def get_settings(request: Request):
    """获取所有设置"""
    from app.database import get_db

    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@router.put("/settings")
async def save_settings(request: Request):
    """批量保存设置"""
    from app.database import get_db

    body = await request.json()
    with get_db() as conn:
        for key, value in body.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )
    return {"status": "ok"}


@router.post("/settings/test-ocr")
async def test_ocr(request: Request):
    """测试 OCR API 连通性"""
    from app.ocr.paddle_api import get_setting

    access_token = get_setting("ocr.access_token")
    if not access_token:
        return JSONResponse(
            {"detail": "请先配置 AI Studio access_token"}, status_code=400
        )

    # 发送最小测试请求
    import httpx
    import base64

    # 生成 1x1 像素的测试图片 (最小 PNG)
    test_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img_b64 = base64.b64encode(test_png).decode("utf-8")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
                params={"access_token": access_token},
                data={"image": img_b64},
            )
            data = resp.json()
            if "error_code" in data:
                return JSONResponse(
                    {"detail": f"连接失败: {data.get('error_msg', '未知错误')}"},
                    status_code=400,
                )
            return {"status": "ok", "message": "OCR API 连接正常"}
    except Exception as e:
        return JSONResponse(
            {"detail": f"连接失败: {e}"}, status_code=400
        )


@router.post("/settings/test-ai")
async def test_ai(request: Request):
    """测试 AI API 连通性（传入临时后端参数）"""
    body = await request.json()
    backend_name = body.get("backend", "")
    api_key = body.get("api_key", "")
    base_url = body.get("base_url", "")
    model = body.get("model", "")

    if not api_key:
        return JSONResponse(
            {"detail": "请先填写 API Key"}, status_code=400
        )

    if backend_name == "custom":
        if not base_url:
            return JSONResponse(
                {"detail": "请填写自定义 base_url"}, status_code=400
            )
    else:
        from app.ai.provider_presets import PROVIDERS
        preset = PROVIDERS.get(backend_name)
        if preset:
            base_url = preset["base_url"]
            model = preset["default_model"]

    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return {"status": "ok", "message": "AI API 连接正常"}
            else:
                data = resp.json()
                error_msg = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
                return JSONResponse(
                    {"detail": f"连接失败: {error_msg}"}, status_code=400
                )
    except Exception as e:
        return JSONResponse(
            {"detail": f"连接失败: {e}"}, status_code=400
        )
```

- [ ] **Step 4: 在 main.py 中注册路由**

```python
# app/main.py — 在 qa_router 注册之后添加：

from app.routes.settings_routes import router as settings_router
app.include_router(settings_router)
```

- [ ] **Step 5: 运行测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_settings_routes.py -v
```

Expected: 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/settings_routes.py tests/test_settings_routes.py app/main.py
git commit -m "feat: 设置管理 + 测试连接 API"
```

---

### Task 8: 设置弹窗 UI

**Files:**
- Create: `app/templates/partials/settings_dialog.html`
- Create: `static/components/settings.js`

**Interfaces:**
- Consumes: `GET /settings`, `PUT /settings`, `POST /settings/test-ocr`, `POST /settings/test-ai`
- Produces: Alpine 组件 `settingsDialog()`，通过 CustomEvent `open-settings` 打开

- [ ] **Step 1: 创建 `static/components/settings.js`**

```javascript
document.addEventListener('alpine:init', () => {
    Alpine.data('settingsDialog', () => ({
        open: false,
        activeTab: 'ocr',

        // OCR
        ocrToken: '',
        ocrTesting: false,
        ocrTestResult: '',

        // AI
        aiBackend: 'claude',
        aiKeys: {},
        aiTesting: false,
        aiTestResult: '',

        init() {
            window.addEventListener('open-settings', (e) => {
                this.activeTab = e.detail?.tab || 'ocr';
                this.open = true;
                this.loadSettings();
            });
        },

        async loadSettings() {
            try {
                const resp = await fetch('/settings');
                const data = await resp.json();
                this.ocrToken = data['ocr.access_token'] || '';
                this.aiBackend = data['ai.backend'] || 'claude';
                this.aiKeys = {
                    doubao: data['ai.doubao.api_key'] || '',
                    deepseek: data['ai.deepseek.api_key'] || '',
                    glm: data['ai.glm.api_key'] || '',
                    kimi: data['ai.kimi.api_key'] || '',
                    custom_base_url: data['ai.custom.base_url'] || '',
                    custom_api_key: data['ai.custom.api_key'] || '',
                    custom_model: data['ai.custom.model'] || '',
                };
            } catch (e) {
                console.error('加载设置失败', e);
            }
        },

        async save() {
            const payload = {
                'ocr.access_token': this.ocrToken,
                'ai.backend': this.aiBackend,
                'ai.doubao.api_key': this.aiKeys.doubao,
                'ai.deepseek.api_key': this.aiKeys.deepseek,
                'ai.glm.api_key': this.aiKeys.glm,
                'ai.kimi.api_key': this.aiKeys.kimi,
                'ai.custom.base_url': this.aiKeys.custom_base_url,
                'ai.custom.api_key': this.aiKeys.custom_api_key,
                'ai.custom.model': this.aiKeys.custom_model,
            };
            try {
                await fetch('/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                this.open = false;
            } catch (e) {
                console.error('保存设置失败', e);
            }
        },

        async testOCR() {
            this.ocrTesting = true;
            this.ocrTestResult = '';
            try {
                const resp = await fetch('/settings/test-ocr', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) {
                    this.ocrTestResult = '✅ ' + data.message;
                } else {
                    this.ocrTestResult = '❌ ' + (data.detail || '未知错误');
                }
            } catch (e) {
                this.ocrTestResult = '❌ 网络错误';
            }
            this.ocrTesting = false;
        },

        async testAI() {
            this.aiTesting = true;
            this.aiTestResult = '';
            const backend = this.aiBackend;
            let apiKey, baseUrl, model;

            if (backend === 'custom') {
                apiKey = this.aiKeys.custom_api_key;
                baseUrl = this.aiKeys.custom_base_url;
                model = this.aiKeys.custom_model;
            } else if (['doubao', 'deepseek', 'glm', 'kimi'].includes(backend)) {
                apiKey = this.aiKeys[backend];
            }

            try {
                const resp = await fetch('/settings/test-ai', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ backend, api_key: apiKey, base_url: baseUrl, model }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    this.aiTestResult = '✅ ' + data.message;
                } else {
                    this.aiTestResult = '❌ ' + (data.detail || '未知错误');
                }
            } catch (e) {
                this.aiTestResult = '❌ 网络错误';
            }
            this.aiTesting = false;
        },

        close() {
            this.open = false;
            this.ocrTestResult = '';
            this.aiTestResult = '';
        },

        // 当前后端对应的 api_key (用于绑定输入框)
        get currentAIKey() {
            if (this.aiBackend === 'custom') return this.aiKeys.custom_api_key;
            if (['doubao', 'deepseek', 'glm', 'kimi'].includes(this.aiBackend)) {
                return this.aiKeys[this.aiBackend];
            }
            return '';
        },
        set currentAIKey(val) {
            if (this.aiBackend === 'custom') this.aiKeys.custom_api_key = val;
            else if (['doubao', 'deepseek', 'glm', 'kimi'].includes(this.aiBackend)) {
                this.aiKeys[this.aiBackend] = val;
            }
        },
    }));
});
```

- [ ] **Step 2: 创建 `app/templates/partials/settings_dialog.html`**

```html
<!-- 全局设置弹窗 -->
<div x-data="settingsDialog"
     x-show="open"
     @keydown.escape.window="if(open) close()"
     @click.self="close()"
     class="qa-modal-overlay"
     style="display:none"
     x-cloak>
    <div class="qa-modal-box" style="max-width:520px" @click.stop="">
        <div class="qa-modal-header">
            <span>⚙️ 系统设置</span>
            <button class="qa-modal-close" @click="close()" aria-label="关闭">&times;</button>
        </div>
        <div class="qa-modal-body">
            <!-- 标签页 -->
            <div style="display:flex;gap:0;margin-bottom:1rem;border-bottom:2px solid var(--pico-muted-border-color)">
                <button @click="activeTab='ocr'"
                        :style="{borderBottom: activeTab==='ocr' ? '2px solid var(--pico-primary-background)' : '2px solid transparent', fontWeight: activeTab==='ocr' ? 'bold' : 'normal'}"
                        style="flex:1;padding:0.5rem;border:none;background:none;cursor:pointer;margin-bottom:-2px;font-size:0.85rem">
                    📷 OCR 识别
                </button>
                <button @click="activeTab='ai'"
                        :style="{borderBottom: activeTab==='ai' ? '2px solid var(--pico-primary-background)' : '2px solid transparent', fontWeight: activeTab==='ai' ? 'bold' : 'normal'}"
                        style="flex:1;padding:0.5rem;border:none;background:none;cursor:pointer;margin-bottom:-2px;font-size:0.85rem">
                    🤖 AI 问答
                </button>
            </div>

            <!-- OCR 标签页 -->
            <div x-show="activeTab==='ocr'">
                <label style="font-size:0.85rem">
                    百度 AI Studio Access Token
                    <input type="password" x-model="ocrToken"
                           placeholder="输入你的 AI Studio 令牌..."
                           style="font-family:monospace;font-size:0.8rem">
                </label>
                <div style="display:flex;gap:0.5rem;align-items:center;margin-top:0.5rem">
                    <button class="outline" @click="testOCR()" :disabled="ocrTesting || !ocrToken"
                            style="font-size:0.75rem;padding:0.25rem 0.75rem">
                        <span x-show="!ocrTesting">🔍 测试连接</span>
                        <span x-show="ocrTesting">⏳ 测试中...</span>
                    </button>
                    <small x-text="ocrTestResult" style="font-size:0.75rem"></small>
                </div>
                <div style="margin-top:0.75rem;font-size:0.75rem;color:var(--pico-muted-color);line-height:1.5">
                    <p style="margin:0.25rem 0">💡 每个接口每日有调用上限，超出将返回 429 错误（免费约 20000 页/天）</p>
                    <p style="margin:0.25rem 0">💡 文件大小无限制，但为避免处理超时，单文件请控制在 100 页以内，超出部分将被忽略</p>
                    <p style="margin:0.25rem 0">🔗 前往 <a href="https://aistudio.baidu.com" target="_blank">aistudio.baidu.com</a> 创建令牌</p>
                </div>
            </div>

            <!-- AI 标签页 -->
            <div x-show="activeTab==='ai'">
                <label style="font-size:0.85rem;margin-bottom:0.5rem">选择后端</label>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.3rem;margin-bottom:0.75rem">
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='claude' ? 'var(--pico-primary-background)' : '', color: aiBackend==='claude' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="claude" style="display:none"> Claude Code
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='codex' ? 'var(--pico-primary-background)' : '', color: aiBackend==='codex' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="codex" style="display:none"> Codex
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='doubao' ? 'var(--pico-primary-background)' : '', color: aiBackend==='doubao' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="doubao" style="display:none"> 豆包
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='deepseek' ? 'var(--pico-primary-background)' : '', color: aiBackend==='deepseek' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="deepseek" style="display:none"> DeepSeek
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='glm' ? 'var(--pico-primary-background)' : '', color: aiBackend==='glm' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="glm" style="display:none"> GLM (智谱)
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='kimi' ? 'var(--pico-primary-background)' : '', color: aiBackend==='kimi' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="kimi" style="display:none"> Kimi (月暗)
                    </label>
                    <label style="font-size:0.8rem;padding:0.3rem;border:1px solid var(--pico-muted-border-color);border-radius:4px;cursor:pointer"
                           :style="{background: aiBackend==='custom' ? 'var(--pico-primary-background)' : '', color: aiBackend==='custom' ? 'var(--pico-primary-inverse)' : ''}">
                        <input type="radio" x-model="aiBackend" value="custom" style="display:none"> 自定义
                    </label>
                </div>

                <!-- API Key 输入 (仅云后端显示) -->
                <div x-show="['doubao','deepseek','glm','kimi','custom'].includes(aiBackend)">
                    <label style="font-size:0.85rem">
                        API Key
                        <input type="password" x-model="currentAIKey"
                               placeholder="sk-xxx..."
                               style="font-family:monospace;font-size:0.8rem">
                    </label>

                    <!-- 自定义额外字段 -->
                    <div x-show="aiBackend==='custom'" style="margin-top:0.5rem">
                        <label style="font-size:0.85rem">
                            Base URL
                            <input type="text" x-model="aiKeys.custom_base_url"
                                   placeholder="https://api.example.com/v1"
                                   style="font-family:monospace;font-size:0.8rem">
                        </label>
                        <label style="font-size:0.85rem;margin-top:0.3rem">
                            Model
                            <input type="text" x-model="aiKeys.custom_model"
                                   placeholder="model-name"
                                   style="font-family:monospace;font-size:0.8rem">
                        </label>
                    </div>

                    <div style="display:flex;gap:0.5rem;align-items:center;margin-top:0.5rem">
                        <button class="outline" @click="testAI()" :disabled="aiTesting || !currentAIKey"
                                style="font-size:0.75rem;padding:0.25rem 0.75rem">
                            <span x-show="!aiTesting">🔍 测试连接</span>
                            <span x-show="aiTesting">⏳ 测试中...</span>
                        </button>
                        <small x-text="aiTestResult" style="font-size:0.75rem"></small>
                    </div>
                </div>

                <!-- CLI 后端提示 -->
                <div x-show="aiBackend==='claude' || aiBackend==='codex'"
                     style="font-size:0.75rem;color:var(--pico-muted-color);margin-top:0.5rem">
                    <p>使用本地 CLI 工具，请确保已安装并登录对应命令行。</p>
                </div>
            </div>

            <!-- 底部按钮 -->
            <div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:1.5rem;padding-top:0.75rem;border-top:1px solid var(--pico-muted-border-color)">
                <button class="outline" @click="close()" style="font-size:0.8rem">取消</button>
                <button @click="save()" style="font-size:0.8rem">💾 保存</button>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add static/components/settings.js app/templates/partials/settings_dialog.html
git commit -m "feat: 全局设置弹窗 (Alpine.js)"
```

---

### Task 9: 集成到 base.html

**Files:**
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes: `settings_dialog.html` 模板、`settings.js`
- Adds: 设置弹窗容器 + 事件监听 + JS 引入

- [ ] **Step 1: 改造 base.html**

在 QA 弹窗 (`#qa-modal-overlay`) 之后、条文弹窗之前，插入 settings 弹窗：

```html
<!-- 设置弹窗 -->
{% include "partials/settings_dialog.html" %}
```

在 `<script>` 引用区添加 settings.js，放在 clause-modal.js 之后：

```html
<script src="/static/components/settings.js"></script>
```

- [ ] **Step 2: 运行冒烟测试确认页面不崩溃**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/test_auth_routes.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: base.html 集成设置弹窗"
```

---

### Task 10: 出入口改造

**Files:**
- Modify: `app/templates/partials/tree_panel.html`
- Modify: `app/templates/base.html` (QA 弹窗标题栏)

- [ ] **Step 1: tree_panel.html — 导入对话框增加 API 设置入口**

在导入对话框 `<div class="dialog-box">` 内，`</form>` 之后，`<div id="import-result">` 之前，加一行：

```html
<div style="text-align:right;margin-top:0.3rem">
    <a href="#" @click.prevent="closeDialog(); $dispatch('open-settings', {tab:'ocr'})"
       style="font-size:0.75rem;color:var(--pico-muted-color)">⚙️ API 设置</a>
</div>
```

- [ ] **Step 2: base.html — QA 弹窗标题栏增加 AI 设置按钮**

将 QA modal header 改为双按钮布局（设置 + 关闭），设置按钮先关闭 QA 弹窗再打开设置弹窗（通过原生 onclick 派发 CustomEvent，因为该区域在 Alpine x-data 作用域外）：

```html
<div class="qa-modal-header">
    <span>🤖 AI 智能问答</span>
    <div style="display:flex;gap:0.5rem;align-items:center">
        <button style="background:none;border:none;cursor:pointer;font-size:1rem;padding:0;line-height:1"
                onclick="document.getElementById('qa-modal-overlay').style.display='none';
                         window.dispatchEvent(new CustomEvent('open-settings', {detail:{tab:'ai'}}))"
                title="AI 设置"
                aria-label="AI 设置">⚙️</button>
        <button class="qa-modal-close"
                onclick="document.getElementById('qa-modal-overlay').style.display='none'"
                aria-label="关闭">&times;</button>
    </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/partials/tree_panel.html app/templates/base.html
git commit -m "feat: 导入/QA 界面增加设置入口"
```

---

### Task 11: 清理依赖 + 全量测试

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 确认依赖清理**

requirements.txt 中 PaddleOCR 和 paddlepaddle 已在 Task 2 删除，确认文件中不含这两行：

```bash
grep -i paddle requirements.txt
```

Expected: 无输出

- [ ] **Step 2: 运行全量测试**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -m pytest tests/ -v
```

Expected: 所有测试 PASS

- [ ] **Step 3: 处理任何失败测试**

根据失败信息修复回归问题。重点关注的测试文件：
- `test_ocr.py` — 已重写
- `test_import.py` — OCR 路径变更
- `test_qa_routes.py` — 后端工厂变更
- `test_cli_client.py` — get_backend 变更

- [ ] **Step 4: 验证启动**

```bash
cd /d/CC-Workspace/construction-spec-query-v2 && D:/Python/python.exe -c "
from app.main import app
print('App created successfully')
# 验证所有路由注册
routes = [r.path for r in app.routes]
assert '/settings' in routes, 'Settings route missing'
assert '/qa/ask' in routes, 'QA route missing'
print('All routes OK')
"
```

Expected: `App created successfully` + `All routes OK`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/
git commit -m "chore: 清理依赖 + 全量测试通过"
```
