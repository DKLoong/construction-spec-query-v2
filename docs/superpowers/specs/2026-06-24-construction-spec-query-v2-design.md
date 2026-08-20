# 施工规范查询系统 V2 — 设计文档

## 概述

个人使用的施工规范查询系统，以新方案完全重写。管理约 100 本规范（平均 200 页/本），支持按六大维度精确查询条文，接入本地 Claude Code CLI / Codex CLI 实现 AI 智能问答。数据与 AI 全部本地化，无需云端 API。

**前身：** `construction-spec-query`（7 维分类、云端 API、四页布局）— 仅作代码参考，不保留。

---

## 核心需求

### 数据规模
- 规范总量：约 100 本
- 平均篇幅：约 200 页/本
- 导入格式：PDF（含扫描件，PaddleOCR 转 MD）、Markdown
- 输出落盘：`data/outputs/{规范名}/`（项目内）

### 六大分类维度

| 维度 | 级别 | 内容 |
|------|------|------|
| 一、规范属性 | 规范级 | 层级（国标/行标/地标/团标/企标）、性质（强制/推荐）、体系层次（基础/通用/专用）、类型（项目/通用技术规范） |
| 二、工程阶段 | 规范级 | 前期（勘察/测量/规划）、设计（方案/初步/施工图）、施工（准备/过程/验收）、运维（试验/维护/改造） |
| 三、工程类型 | 规范级 | 按用途（民用/工业/农业建筑）、按建设性质（新建/扩建/改建）、按规模（大/中/小） |
| 四、所属专业 | 条文级 | 市政公用 10 子专业、铁路工程 26 子专业、建筑工程 5 子专业，含完整层级树 |
| 五、工程部位 | 条文级 | 地基与基础、主体结构、二次结构、屋面、装饰装修、机电系统、室外工程（参照 GB50300） |
| 六、材料/工艺 | 条文级 | 混凝土、金属、砌体、木材、装饰、防水保温、复合材料、施工工艺 |

### 功能要点
- 多维交叉检索：同时勾选多维度组合筛选
- 层级树形展示：逐级下钻，节点显示条文数量
- 规范属性前置：作为独立高优先级筛选栏
- 全文关键词搜索：与分类树交叉过滤
- AI 智能问答：基于检索条文生成回答，标注引用来源
- 三栏单页布局：左栏（分类树+搜索+导入）、中栏（结果）、右栏（AI 对话）

---

## 架构设计

### 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                     输入层                                │
│  PDF(扫描件) → PaddleOCR → MD    MD文件 → 直接导入        │
│  转换后的 MD 存储至 data/outputs/{规范名}/              │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                   条文解析器                               │
│  按章节/条文号层级切割 MD，提取 clause_no + title +       │
│  content，保留父子层级关系                                │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
┌──────────────────┐    ┌──────────────────────────┐
│  规范级元数据     │    │  条文级标签               │
│  维度一：规范属性  │    │  维度四：所属专业          │
│  维度二：工程阶段  │    │  维度五：工程部位          │
│  维度三：工程类型  │    │  维度六：材料/工艺          │
└────────┬─────────┘    └──────────┬───────────────┘
         │                         │
         └─────────┬───────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│                   分类引擎                                │
│  关键词规则匹配（自适应阈值）── <阈值 ──→ 攒批 CLI AI    │
│  分类结果 → SQLite + LanceDB 双写                        │
│  用户确认/驳回 → 反馈提取关键词 → 补充规则库（闭环）     │
└───────────────────────┬─────────────────────────────────┘
                        │
           ┌────────────┴────────────┐
           ▼                         ▼
┌──────────────────┐    ┌──────────────────────────┐
│  SQLite (FTS5)   │    │  LanceDB                 │
│  · specifications│    │  · clause_id             │
│  · clauses       │    │  · text                  │
│  · 六维评分字段   │    │  · embedding (BGE)       │
│  · 分类规则表     │    │  · dimension_scores      │
│  · 用户表        │    │                          │
└────────┬─────────┘    └──────────┬───────────────┘
         │                         │
         └─────────┬───────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│                   查询层 (三栏 Web UI)                    │
│  左栏：分类树 + 搜索 + 导入  │  中栏：结果列表  │  右栏：AI 对话  │
│  多维交叉筛选 ←→ 关键词搜索 ←→ 向量语义检索 ←→ AI 问答   │
└─────────────────────────────────────────────────────────┘
```

### 分类决策链（优化后）

```
条文文本
  │
  ├── 维度一~三（规范级）：导入时一次性判定
  │     · 标准号前缀匹配 → 规范属性/层次（阈值 30%）
  │     · 规范名称关键词 → 工程阶段/类型（阈值 50%）
  │     · 低于阈值 → 导入界面人工勾选，无需 AI
  │
  ▼
  维度四~六（条文级）：逐条分类
  │
  ├── 规则引擎匹配 ──→ 计算各维度得分
  │     · 标签继承：父章节已标注标签 → 子条文自动继承路径
  │     · 关键词命中加分：精确 > 关键词 > 模糊
  │     · 使用自适应阈值判定
  │
  ├── max_score ≥ 该维度阈值 → 直接采纳，打上标签
  │
  ├── max_score < 阈值 → 进入攒批队列
  │     · 同一维度 + 同批积累到 20 条 → 提交 CLI AI 批量分类
  │     · AI 返回 (标签, 置信度)
  │     · AI 置信度 ≥ 0.7 → 自动采纳
  │     · AI 置信度 < 0.7 → 入人工复核队列
  │
  └── 用户确认/驳回后 → 反馈提取关键词 → 补充规则库
```

### 自适应阈值

| 维度 | 确定性 | 阈值 | 原因 |
|------|--------|------|------|
| 规范属性 | 极高 | 30% | GB/GB/T/JGJ 前缀即可判定 |
| 工程阶段 | 高 | 50% | 关键词明确（验收、勘察、设计） |
| 工程类型 | 高 | 50% | 关键词较少歧义 |
| 所属专业 | 中 | 60% | 专业术语可能有交叉 |
| 工程部位 | 低 | 70% | 描述性文本歧义多，AI 更有优势 |
| 材料/工艺 | 中 | 60% | 材料名称明确但工艺描述可能模糊 |

### 标签继承

专业维度的层级树天然支持标签继承：

```
条文归属"接触网"章节
  → 自动继承路径: 铁路工程 → 电力牵引供电 → 接触网
  → dim4_specialty = "铁路工程,电力牵引供电,接触网"
```

实现：导入解析时识别 MD 标题层级，父标题名对应父标签，子条文继承全部祖先路径。

### 攒批 CLI 分类

- 触发条件：pending ≥ 20 条 OR 距上次提交 > 30 秒
- 批量 Prompt 含最多 20 条条文，要求返回 JSON
- CLI 子进程 `--print` 模式，超时 120s
- 结果逐条写入 `classification_queue`

### 规则反馈闭环

用户确认 AI 分类后：
1. 从条文提取高 TF-IDF 关键词
2. 检查规则表是否已有类似规则
3. 新关键词 → 创建规则（is_active=0，待启用）
4. 规则已存在 → hit_count++、confirmed++
5. confirmed/hit_count 持续 < 0.3 → 规则自动禁用

---

## 数据模型

### SQLite 表结构

#### `specifications` — 规范元数据（维度一~三）

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| code | TEXT NOT NULL | 规范编号，如 "GB 50204-2015" |
| title | TEXT NOT NULL | 规范全称 |
| short_name | TEXT | 简称 |
| dim1_hierarchy | TEXT | 规范层级：国家标准/行业标准/地方标准/团体标准/企业标准 |
| dim1_nature | TEXT | 规范性质：强制性/推荐性 |
| dim1_sys_level | TEXT | 体系层次：基础标准/通用标准/专用标准 |
| dim1_spec_type | TEXT | 规范类型：项目规范/通用技术规范 |
| dim2_stage | TEXT | 工程阶段（可多选，逗号分隔） |
| dim3_usage | TEXT | 按用途：民用建筑/工业建筑/农业建筑 |
| dim3_construction | TEXT | 按建设性质：新建/扩建/改建 |
| dim3_scale | TEXT | 按规模：大型/中型/小型 |
| status | TEXT | 现行/废止/修订中，默认"现行" |
| source_path | TEXT | 原始 PDF/MD 文件路径 |
| output_dir | TEXT | OCR/MD 输出目录 data/outputs/{规范名} |
| clause_count | INTEGER | 条文总数，默认 0 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

#### `clauses` — 条文核心表（维度四~六）

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| spec_id | INTEGER FK | 关联 specifications.id |
| clause_no | TEXT NOT NULL | 条文号，如 "5.2.1" |
| title | TEXT | 条文标题 |
| content | TEXT NOT NULL | 条文正文 |
| parent_clause | INTEGER FK | 父条文 ID（自引用） |
| dim4_specialty | TEXT | 所属专业（逗号分隔层级路径） |
| dim5_location | TEXT | 工程部位（逗号分隔层级路径） |
| dim6_material | TEXT | 材料/工艺（逗号分隔层级路径） |
| ai_classified | INTEGER DEFAULT 0 | 0=规则 1=AI辅助 |
| needs_review | INTEGER DEFAULT 0 | 0=正常 1=待复核 |
| created_at | TEXT | 创建时间 |

#### `classification_rules` — 分类规则

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| dimension | TEXT NOT NULL | dim1~dim6 |
| sub_field | TEXT | 子字段（如 dim4→specialty） |
| pattern | TEXT NOT NULL | 关键词或正则 |
| match_type | TEXT DEFAULT 'keyword' | keyword/regex/exact |
| priority | INTEGER DEFAULT 0 | 优先级，大→优先 |
| threshold | REAL NOT NULL | 该规则适用的维度阈值 |
| hit_count | INTEGER DEFAULT 0 | 命中次数（反馈统计） |
| confirmed | INTEGER DEFAULT 0 | 用户确认次数 |
| is_active | INTEGER DEFAULT 1 | 启用/禁用 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

#### `classification_queue` — 分类批次队列

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| clause_id | INTEGER FK | 关联 clauses.id |
| dimension | TEXT NOT NULL | 待分类维度 |
| keyword_score | REAL | 规则引擎最高分 |
| batch_id | TEXT | 批次 ID，同一批一起送 AI |
| ai_label | TEXT | AI 返回的标签 |
| ai_confidence | REAL | AI 自评置信度 |
| status | TEXT DEFAULT 'pending' | pending/ai_processing/auto_adopted/review/done |
| created_at | TEXT | 创建时间 |

#### `users` — 用户鉴权

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| username | TEXT UNIQUE | 用户名 |
| password_hash | TEXT | bcrypt 密码哈希 |
| is_active | INTEGER DEFAULT 1 | 启用/禁用 |
| created_at | TEXT | 创建时间 |

#### FTS5 全文索引

```sql
CREATE VIRTUAL TABLE clauses_fts USING fts5(
    clause_no, title, content,
    dim4_specialty, dim5_location, dim6_material,
    content='clauses', content_rowid='id'
);
```

### LanceDB 向量表 `clause_embeddings`

| 字段 | 类型 | 说明 |
|------|------|------|
| clause_id | INT64 | 对应 clauses.id |
| spec_id | INT64 | 对应 specifications.id |
| text | STRING | 拼接文本：`[clause_no] [title] [content]` |
| embedding | FIXED_SIZE_LIST(FLOAT32, 512) | BGE-small-zh 向量 |
| dim_scores | STRING | "dim4=xxx,dim5=xxx,dim6=xxx" |

---

## AI 集成设计

### CLI 调用架构

```
┌─────────────────────────────────────────────────┐
│                  FastAPI App                      │
│                                                   │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐  │
│  │ 分类模块  │  │ 问答模块   │  │ Embedding模块 │  │
│  └────┬─────┘  └─────┬─────┘  └──────┬───────┘  │
│       │              │               │           │
│       ▼              ▼               │           │
│  ┌────────────────────────┐          │           │
│  │   CLI 客户端抽象层      │          │           │
│  │   ┌────────┐ ┌───────┐ │          │           │
│  │   │Claude  │ │Codex  │ │          │           │
│  │   │CLI     │ │CLI    │ │          │           │
│  │   └───┬────┘ └───┬───┘ │          │           │
│  └───────┼──────────┼─────┘          │           │
│          │          │                │           │
│          ▼          ▼                ▼           │
│     subprocess    subprocess    sentence-        │
│     (stdio)       (stdio)       transformers     │
└─────────────────────────────────────────────────┘
```

### CLI 后端抽象

```python
class CLIBackend(ABC):
    async def ask(self, prompt, context="", work_dir=None) -> CLIResponse: ...
    async def classify_batch(self, clauses, dimension) -> list[ClassifyResult]: ...
    def is_available(self) -> bool: ...

class ClaudeCodeCLI(CLIBackend):
    command = "claude"

class CodexCLI(CLIBackend):
    command = "codex"
```

### 子进程调用策略

| 场景 | 模式 | 超时 | 说明 |
|------|------|------|------|
| 批量分类 | `--print` 非交互 | 120s | 20 条/批，返回 JSON |
| AI 问答 | `--print` 非交互 | 60s | 单次问答，含上下文 |
| 健康检查 | `--version` | 5s | 启动时检测 CLI 可用性 |

### 问答上下文构建

```
用户提问
  ├── ① 关键词提取 → FTS5 全文搜索
  ├── ② BGE 向量化 → LanceDB Top-K（K=10）
  ├── ③ 合并去重，取 Top-15
  ├── ④ 构造 Prompt：
  │     "你是施工规范查询助手。以下是与问题相关的规范条文：
  │      [条文1] GB50204-2015 第5.2.1条：...
  │      请基于以上条文回答，引用时标注规范名称和条文号。"
  └── ⑤ CLI 子进程调用 → 返回回答
```

### 工作区隔离

问答工作区：`data/workspace/{session_id}/`（项目内），每会话独立，CLI 进程 CWD 指向该目录。

### 错误处理

| 情况 | 处理 |
|------|------|
| CLI 未安装 | 前端显示配置引导 |
| CLI 超时 | 返回部分结果 + 提示 |
| CLI 崩溃 | 记录日志，返回"暂时不可用" |
| JSON 解析失败 | 降级为文本解析 + 标记待复核 |

---

## 界面设计

### 整体布局：三栏单页

```
┌──────────────┬────────────────────────┬──────────────┐
│   左栏 25%   │      中栏 45%          │  右栏 30%    │
│              │                        │              │
│ ┌──────────┐ │  ┌──────────────────┐  │ ┌──────────┐ │
│ │🔍 搜索框 │ │  │   欢迎您          │  │ │ AI 问答  │ │
│ └──────────┘ │  │   请选择左侧分类   │  │ │          │ │
│ ┌──────────┐ │  │   或输入关键词     │  │ │ 📝 消息  │ │
│ │📥 导入文档│ │  │   开始查询...     │  │ │ 气泡列表 │ │
│ └──────────┘ │  │                   │  │ │          │ │
│              │  │ (搜索后)           │  │ │          │ │
│ ┌──────────┐ │  │ ┌──────────────┐  │  │          │ │
│ │▼ 规范属性 │ │  │ │ 结果 1       │  │  │          │ │
│ │▼ 工程阶段 │ │  │ │ GB50204 5.2.1│  │  │          │ │
│ │▼ 工程类型 │ │  │ │ ...正文摘要..│  │  │          │ │
│ │▼ 所属专业 │ │  │ ├──────────────┤  │  │          │ │
│ │▼ 工程部位 │ │  │ │ 结果 2       │  │  │          │ │
│ │▼ 材料工艺 │ │  │ │ ...          │  │  │          │ │
│ └──────────┘ │  │ └──────────────┘  │  │          │ │
│              │  │  < 1 2 3 ... >   │  │          │ │
└──────────────┴────────────────────────┴──┴──────────┘
```

### 左栏：分类树 + 搜索 + 导入

- **搜索框**：顶部固定，支持编号/名称/关键词，300ms 防抖后 htmx 触发 FTS5 搜索
- **导入按钮**：搜索框下方等宽，点击弹出导入对话框（Alpine.js），支持拖拽 PDF/MD
- **分类树**：六维层级树，Alpine.js 管理展开折叠，节点显示条文数量，多选 checkbox，已选标签显示在顶部

### 中栏：结果展示

- 初始显示欢迎语
- 搜索结果：规范编号+名称、条文号、正文前 200 字摘要
- 点击展开完整条文（Alpine.js 折叠面板），关键词高亮
- 底部分页器（每页 20 条）
- 分类筛选 + 关键词搜索叠加使用（交集）

### 右栏：AI 问答

- 消息气泡：用户靠右、AI 靠左（含引用标注）
- AI 引用可点击，跳转中栏展开对应条文
- 支持 Markdown 渲染（marked.js）
- 输入框 + 发送按钮 + 模型选择器（Claude/Codex）
- 显示当前对话基于"N 条检索条文"，可展开查看

### 响应式

窗口 < 900px：左栏 → 汉堡菜单，右栏 → 底部抽屉，中栏占满。

---

## 技术栈

### 后端

| 组件 | 选型 | 说明 |
|------|------|------|
| Python 版本 | 3.12+ | |
| Web 框架 | FastAPI | 异步、自动文档 |
| OCR 引擎 | PaddleOCR | 中文识别最优 |
| PDF 文本提取 | PyMuPDF (fitz) | 提取已有文本层 |
| MD 解析 | 自写解析器 | 按标题层级切割条文 + 标签继承 |
| 结构化数据库 | SQLite | 零配置，FTS5 全文索引 |
| 向量数据库 | LanceDB | 嵌入式，Python 原生 |
| Embedding | BGE-small-zh | 本地，sentence-transformers，512 维 |
| AI 分类/问答 | Claude Code CLI / Codex CLI | 子进程调用，`--print` 非交互模式 |
| 鉴权 | python-jose + passlib | JWT + bcrypt |
| 异步任务 | FastAPI BackgroundTasks | OCR + 分类异步处理 |

### 前端

| 组件 | 选型 | 说明 |
|------|------|------|
| 模板 | Jinja2 | 服务端渲染 |
| CSS | Pico.css + 自定义 | 轻量 classless + 三栏布局 |
| 交互 | htmx | AJAX 局部刷新 |
| 客户端状态 | Alpine.js | 树展开折叠、对话框、面板切换 |
| MD 渲染 | marked.js | 条文预览 + AI 回答渲染 |

### 部署

| 组件 | 选型 |
|------|------|
| 服务器 | uvicorn 单进程 |
| 监听地址 | 127.0.0.1:8000 |
| 启动方式 | start.sh / start.ps1 一键脚本 |

---

## 外部依赖

| 服务 | 用途 | 费用 |
|------|------|------|
| PaddleOCR | PDF 扫描件识别 | 免费（本地） |
| PyMuPDF | PDF 文本层提取 | 免费（AGPL） |
| BGE-small-zh | 条文向量化 + 查询向量化 | 免费（本地） |
| Claude Code CLI | 分类补充 + 智能问答 | 免费（本地，需已有 Claude 账户） |
| Codex CLI | 分类补充 + 智能问答（备选） | 免费（本地，需已有 OpenAI 账户） |

全链路本地化，无需额外付费服务。

---

## 项目结构

```
construction-spec-query-v2/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口，挂载路由
│   ├── config.py                # 配置管理（.env 加载）
│   ├── auth.py                  # JWT 鉴权中间件
│   ├── database.py              # SQLite 连接 + 建表
│   ├── models.py                # Pydantic 模型 + 数据校验
│   │
│   ├── ocr/
│   │   ├── __init__.py
│   │   ├── paddle_ocr.py        # PaddleOCR 封装
│   │   └── pdf_extract.py       # PyMuPDF 文本层提取
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   └── md_parser.py         # MD 层级解析 + 条文切割 + 标签继承
│   │
│   ├── classifier/
│   │   ├── __init__.py
│   │   ├── rule_engine.py       # 关键词规则引擎（自适应阈值）
│   │   ├── batch_queue.py       # 攒批队列管理
│   │   └── feedback.py          # 规则反馈闭环
│   │
│   ├── search/
│   │   ├── __init__.py
│   │   ├── sql_search.py        # FTS5 + 多维筛选
│   │   └── vector_search.py     # LanceDB 向量检索
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── cli_client.py        # CLI 客户端抽象层（Claude/Codex）
│   │   ├── classifier_ai.py     # AI 批量分类（prompt 构造 + 解析）
│   │   ├── qa.py                # 问答上下文构建
│   │   └── embedding.py         # BGE 本地 embedding
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth_routes.py       # 登录/登出
│   │   ├── import_routes.py     # 文件导入 + 进度查询
│   │   ├── search_routes.py     # 多维搜索 + 关键词搜索
│   │   ├── qa_routes.py         # AI 问答
│   │   ├── rules_routes.py      # 规则管理 + 复核队列
│   │   └── tree_routes.py       # 分类树数据接口（懒加载）
│   │
│   └── templates/
│       ├── base.html            # 三栏基础布局 (CSS Grid)
│       ├── login.html           # 登录页
│       ├── partials/            # htmx 局部刷新片段
│       │   ├── result_list.html
│       │   ├── tree_node.html
│       │   ├── qa_message.html
│       │   └── import_progress.html
│       └── components/          # Alpine.js 组件
│           ├── tree.js
│           ├── qa.js
│           ├── search.js
│           └── import.js
│
├── static/
│   ├── pico.min.css
│   ├── htmx.min.js
│   ├── alpine.min.js
│   ├── marked.min.js
│   ├── pico.custom.css          # 三栏布局样式
│   └── app.css
│
├── scripts/
│   ├── create_admin.py
│   ├── seed_rules.py            # 六维种子规则
│   └── rebuild_index.py         # 重建 FTS5 + LanceDB 索引
│
├── tests/
│   ├── conftest.py
│   ├── test_database.py
│   ├── test_md_parser.py
│   ├── test_rule_engine.py
│   ├── test_cli_client.py
│   ├── test_search.py
│   ├── test_import.py
│   └── test_qa.py
│
├── data/                        # 应用数据（gitignore）
│   ├── spec_query.db
│   ├── uploads/                 # 上传暂存
│   ├── outputs/                 # OCR/MD 输出
│   └── workspace/               # CLI 问答工作区
│
├── lance_db/                    # LanceDB（gitignore）
├── requirements.txt
├── .env.example
├── start.sh
└── start.ps1
```

---

## 非功能性需求

### 性能
- 单本规范导入 + OCR + 分类 + 索引：目标 5 分钟内
- 查询响应：筛选/搜索 < 1 秒，AI 问答 < 30 秒（含 CLI 启动）
- 向量检索：预估 5~10 万条条文，LanceDB 毫秒级
- CLI 冷启动：1~3 秒，通过攒批平摊

### 安全
- 仅监听 127.0.0.1，不对外暴露
- JWT 鉴权保护所有页面
- 密码 bcrypt 哈希存储
- 无外部 API 调用，数据不离本地

### 可维护性
- 分类规则可视化编辑，无需改代码
- 新规范随时增量导入
- 规则修改后可触发「重新分类」
- 规则反馈闭环：用得越久分类越准
- SQLite 单文件，备份即完整迁移

### 数据落盘
所有数据均在项目目录内，无需外部路径：
- OCR/MD 输出：`data/outputs/{规范名}/`
- 上传暂存：`data/uploads/`
- 问答工作区：`data/workspace/{session_id}/`
- 结构化数据库：`data/spec_query.db`
- 向量数据库：`lance_db/`

---

## 后续扩展预留

- 规范间交叉引用检索
- 规范版本对比（新旧版本条文差异）
- 图片/表格的 OCR 和检索
- 导出查询结果为 PDF/Markdown
- 云端 API 可选切换（抽象层已预留）
