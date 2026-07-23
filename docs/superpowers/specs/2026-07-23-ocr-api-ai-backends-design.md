# 设计文档：OCR API 化 + AI 多后端支持

**日期**: 2026-07-23
**状态**: 已确认
**分支**: `feature/ocr-ai-backends`

---

## 一、需求概述

### 1.1 OCR 模块

将导入流程中的 OCR 识别从本地 PaddleOCR 模型改为百度 AI Studio 云端 API。

- 删除 `app/ocr/paddle_ocr.py`（本地 PaddleOCR）
- 删除 `requirements.txt` 中 `paddleocr`、`paddlepaddle` 依赖
- 新增 `app/ocr/paddle_api.py`，封装 AI Studio HTTP API
- 导入界面增加 API 设置入口

### 1.2 AI 问答模块

增加多后端支持，用户可选择本地 CLI agent 或云端大模型 API。

- 保留现有 CLI 后端（Claude Code / Codex）
- 新增云端 API 后端：豆包、DeepSeek、GLM、Kimi + 自定义
- 云端后端均兼容 OpenAI 接口格式，统一为一个类 + 配置驱动

### 1.3 统一设置界面

新增全局设置弹窗 + SQLite `settings` 表，管理 OCR 和 AI 的 API 配置。

---

## 二、架构

```
改造涉及的文件：

app/ocr/                              app/ai/
├── paddle_api.py       (NEW)         ├── cli_client.py       (不改)
├── pdf_extract.py      (不改)         ├── api_client.py       (NEW)
└── __init__.py                       ├── classifier_ai.py    (不改)
                                      ├── embedding.py        (不改)
app/routes/                           ├── provider_presets.py (NEW)
├── import_routes.py    (改: OCR调用路径) └── __init__.py
├── ocr_routes.py       (NEW)
├── qa_routes.py        (改: 后端工厂)
└── settings_routes.py  (NEW)

app/templates/                        static/
├── base.html           (改: 设置弹窗)  └── components/
└── partials/                              └── settings.js (NEW)
    ├── tree_panel.html        (改)
    ├── qa_panel.html          (改)
    └── settings_dialog.html   (NEW)

数据库: settings 表 (NEW)
```

### 数据流

```
OCR 导入:
  上传 PDF → is_scanned() → ocr_pdf_to_md() → HTTP POST AI Studio
  → 每页返回识别文本 → 拼 Markdown → 审查页 → 确认 → 解析入库

AI 问答:
  用户提问 → hybrid_search(30候选) → 向量重排序(Top-5)
  → 根据 settings 表选择后端 → CLI subprocess / HTTP POST 云 API
  → 返回答案 + 引文
```

---

## 三、OCR 模块详细设计

### 3.1 删除文件

- `app/ocr/paddle_ocr.py` — 本地 PaddleOCR
- `requirements.txt` 中 `paddleocr>=3.0.0`、`paddlepaddle>=3.0.0`

### 3.2 新增 `app/ocr/paddle_api.py`

```python
class PaddleStudioAPI:
    """百度 AI Studio OCR API 客户端"""

    BASE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1"
    ACCESS_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"

    def __init__(self, access_token: str):
        self.access_token = access_token

    def ocr_image(self, img_path: str) -> str:
        """对单张图片执行 OCR（accurate_basic 高精度版），返回识别文本"""

    def ocr_pdf_to_md(self, pdf_path: str, output_dir: str | None = None) -> str:
        """PDF 逐页渲染 PNG → 每页调用 ocr_image() → 拼 Markdown"""
```

要点：
- 使用百度 OCR `accurate_basic` 接口（高精度通用文字识别）
- 免费版每日约 20000 页，超限返回 429 错误
- `ocr_pdf_to_md()` 流程与旧版相同：渲染 PNG → OCR → 拼 MD → 删 PNG
- `ocr_pdf_to_md()` 最多处理前 100 页，超出部分忽略（文件大小无限制，但避免处理超时）
- access_token 从 `settings` 表读取 key `ocr.access_token`
- 如果 token 未配置，导入时在进度信息中提示用户先到设置页配置

### 3.3 改造 `app/routes/import_routes.py`

`_process_import()` 中第 94 行：

```python
# 旧：from app.ocr.paddle_ocr import ocr_pdf_to_md
# 新：from app.ocr.paddle_api import PaddleStudioAPI
api = PaddleStudioAPI(access_token=get_setting("ocr.access_token"))
md_path = api.ocr_pdf_to_md(file_path)
```

### 3.4 依赖变更

- 移除: `paddleocr`、`paddlepaddle`
- 保留: `fitz`（PyMuPDF，PDF 渲染）、`httpx`（已有，HTTP 调用）

---

## 四、AI 多后端详细设计

### 4.1 新增 `app/ai/api_client.py`

```python
class APIBackend(CLIBackend):
    """OpenAI 兼容 API 后端 — 支持豆包/DeepSeek/GLM/Kimi/自定义"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.command = "api"  # 标识用

    async def ask(self, prompt, context="", work_dir=None) -> CLIResponse:
        """HTTP POST 调用 /chat/completions，返回 CLIResponse"""

    def is_available(self) -> bool:
        """检查 api_key 非空"""

    def classify_batch_sync(self, clauses, dimension) -> list[ClassifyResult]:
        """同步分类 — 复用 ask 逻辑"""
```

调用方式：使用 `httpx.AsyncClient`，POST 到 `{base_url}/chat/completions`，OpenAI 兼容格式。

### 4.2 新增 `app/ai/provider_presets.py`

```python
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

### 4.3 改造 `get_backend()` 工厂

```python
# 旧: 只支持 claude / codex
def get_backend(name: str) -> CLIBackend:
    if name == "codex":
        return CodexCLI()
    return ClaudeCodeCLI()

# 新: 从 settings 表读取当前选择，支持所有后端
def get_backend(name: str | None = None) -> CLIBackend:
    # name 未指定时，从 settings 读取 ai.backend
    # claude → ClaudeCodeCLI()
    # codex → CodexCLI()
    # doubao/deepseek/glm/kimi → APIBackend(查 PROVIDERS 预置)
    # custom → APIBackend(查 ai.custom.* settings)
```

### 4.4 改造 `app/routes/qa_routes.py`

- 第 97 行 `get_backend(body.backend)` → 改为 `get_backend()` 或读取前端传入的 `backend` 字段
- 前端 `qa.js` 发送请求时传入当前选择的后端名称

---

## 五、设置模块详细设计

### 5.1 数据库 `settings` 表

```sql
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT DEFAULT ''
);
```

### 5.2 `settings` 表 Key 设计

| Key | 说明 | 默认值 |
|-----|------|--------|
| `ocr.access_token` | AI Studio 令牌 | 空 |
| `ai.backend` | 当前选择的后端 | `claude` |
| `ai.doubao.api_key` | 豆包 API Key | 空 |
| `ai.deepseek.api_key` | DeepSeek API Key | 空 |
| `ai.glm.api_key` | GLM API Key | 空 |
| `ai.kimi.api_key` | Kimi API Key | 空 |
| `ai.custom.base_url` | 自定义 base_url | 空 |
| `ai.custom.api_key` | 自定义 API Key | 空 |
| `ai.custom.model` | 自定义 model | 空 |

### 5.3 新增 `app/routes/settings_routes.py`

| 路由 | 方法 | 功能 |
|------|------|------|
| `/settings` | GET | 获取所有 settings（JSON） |
| `/settings` | PUT | 批量保存 settings（JSON） |
| `/settings/test-ocr` | POST | 测试 OCR API 连通性 |
| `/settings/test-ai` | POST | 测试 AI API 连通性（传入临时后端参数） |

### 5.4 新增 `app/templates/partials/settings_dialog.html`

全局设置弹窗（Alpine.js 组件），在 `base.html` 中引入（与 QA 弹窗同级）：

- 双标签页：OCR 识别 / AI 问答
- OCR 标签：access_token 输入框 + 测试连接按钮 + 两条使用提示
  - 提示1：每个接口每日有调用上限，超出将返回 429 错误（免费约 20000 页/天）
  - 提示2：文件大小无限制，但为避免处理超时，单文件请控制在 100 页以内，超出部分将被忽略
- AI 标签：后端选择（radio 列表，选中后展开 API Key 输入框）+ 测试连接按钮
- 底部保存/取消按钮

### 5.5 新增 `static/components/settings.js`

Alpine 组件 `settingsDialog()`，管理标签页切换、表单提交、测试连接逻辑。

### 5.6 改造 `base.html`

- 新增 `<div id="settings-modal-overlay" ...>` 弹窗容器
- 引入 `<script src="/static/components/settings.js">`

### 5.7 出入口

- 导入对话框（`tree_panel.html`）：底部 `⚙️ API 设置` 按钮 → 打开设置弹窗 OCR 标签
- QA 弹窗标题栏（`qa_panel.html`）：新增 `⚙️` 按钮 → 打开设置弹窗 AI 标签

---

## 六、兼容性考虑

- `app/ocr/pdf_extract.py` 保持不变（`is_scanned()` / `extract_text()` 不依赖 PaddleOCR）
- `app/ai/cli_client.py` 的 `CLIBackend` 基类保持不变，`APIBackend` 继承它
- `app/ai/embedding.py` 完全不受影响
- `app/ai/classifier_ai.py` 中的 `classify_batch_sync()` 继续使用旧 CLI 后端的实现，不受影响
- 首次部署时 `settings` 表为空，所有值回退到空字符串，OCR 不可用会给出明确提示

---

## 七、实施顺序

1. 数据库：创建 `settings` 表
2. OCR 模块：`paddle_api.py` + 删除 `paddle_ocr.py` + 改造 import_routes
3. AI 模块：`api_client.py` + `provider_presets.py` + 改造 `get_backend()` + qa_routes
4. 设置界面：`settings_dialog.html` + `settings.js` + `settings_routes.py` + base.html 改造
5. 入口改造：tree_panel.html + qa_panel.html
6. 清理依赖 + 运行测试
