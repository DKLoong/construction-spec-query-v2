# 便携分发包设计（zip + 启动器）

> 日期：2026-09-30
> 状态：设计稿（待实施）
> 取代：`2026-08-25-deploy-sharing-design.md`（形态变更，见 §1.2）
> 来源：审查第三方（豆包）分发建议后，经多轮实测核实与决策收敛

---

## 一、背景与目标

### 1.1 分享场景

| 项 | 内容 |
| --- | --- |
| 接收方 | 异地、离线，环境不可知（不保证有 Python） |
| 交付方式 | 独立分发包，非局域网共享 |
| 功能要求 | 需**导入新规范**，故必须携带 embedding 模型 |
| 体验要求 | 开箱即用：解压 → 双击启动器 → 浏览器直接用 |

### 1.2 与 2026-08-25 设计的关系

08-25 稿（`deploy-sharing-design.md`）设计的是**引导式安装器**：检测系统环境 → 建 venv → `pip install` → 从 ModelScope/HF **在线下载模型**。

**本稿取代其形态**，原因是接收方离线且环境不可知——在线装依赖与下模型的路径不可靠。改为一套**完全自包含的便携目录**：内置 Python 运行时、依赖、模型、数据，全程零联网。

**沿用 08-25 已收敛的结论**：
- 端口：默认 8000，被真正占用时自动 +1 找空闲端口（该决策已论证，不再重复）
- 分享版清空 API key、保留规范数据
- 默认只带 BGE，reranker 作为可选增强

**明确变更的结论**：
- 模型改为**离线内置**，不再在线下载；本次**不含 reranker**
- 不采用"config 无 SECRET_KEY 则拒绝启动"的强制方案
- 鉴权改为**默认关闭**（`AUTH_ENABLED=0`），取代 08-25 的"首次登录强制改密码"
- 本次**不做**改密码 / 登录页优化 / agent API

---

## 二、产物形态

```
规范检索系统/
├── 启动.exe                        ← PyInstaller --windowed，无黑窗
├── runtime/                        ← 便携完整 CPython 3.12（本体 ~133MB）
│   ├── python.exe                  ← 启动器只认这个（绝对路径），绝不碰系统 PATH
│   ├── Lib/site-packages/          ← 按 requirements 装齐的依赖（含 torch）
│   └── msvcp140*.dll / vcruntime140*.dll  ← VC++ 运行库，1.13MB（见 §11）
├── app/  static/  scripts/         ← 应用代码
├── models/BAAI/bge-small-zh-v1___5/
├── data/
│   ├── spec_query.db               ← 脱敏副本
│   └── outputs/{5 个目录}           ← 含 78 张正文图片
├── lance_db/
├── .env                            ← AUTH_ENABLED=0 + 随机 SECRET_KEY
└── 使用说明（由用户自行编写）
```

**排除项**：`data/uploads/`、`data/backups/`、`data/app.db`、`data/workspace/`、`*.bak-*`（含 `data/spec_query.db.bak-20260920-210926`）、`tests/`、`docs/`、`__pycache__/`、`ui-demo-*.html`、`TODOS.md`、`.pytest_cache/`

**运行期要求**：解压目录必须可写（不得放在 `C:\Program Files\` 下）。

---

## 三、线 1：依赖修正

| 动作 | 依据 |
| --- | --- |
| `requirements.txt` **补 `jieba`** | `app/search/tokenize.py:8` 是顶层硬导入、无 try/except 降级；缺失将导致 `sql_search` 导入失败、**检索整条链路崩溃** |
| `requirements.txt` **删 `pytest`** | 生产分发包不需要测试框架 |

**验证方式**：在干净 Python 3.12 环境按修正后的清单实装，跑通（检索 + 导入）后方可进入打包。

> 注：`numpy`、`pydantic`、`starlette`、`lance` 等虽未显式声明，但由 `lancedb`/`fastapi`/`pyarrow` 传递引入，属可接受的隐性依赖，本次不补。

---

## 四、线 2：脱敏

**在 `spec_query.db` 的副本上执行，全程不触碰生产库。**

### 4.1 清除

| 目标 | 实测规模 | 说明 |
| --- | --- | --- |
| `settings` 中 `ai.custom.api_key` | 116 字符 | |
| `settings` 中 `ai.deepseek.api_key` | 35 字符 | |
| `settings` 中 `ocr.access_token` | 40 字符 | |
| `settings` 中 `ocr.accurate-basic.access_token` | 40 字符 | |
| `settings` 中 `ocr.paddle-vl.access_token` | 40 字符 | |
| `settings` 中 `ai.custom.base_url` | 75 字符 | **私有网关地址，同属敏感** |
| `system_logs` 全表 | 2247 行 | 含测试账号痕迹 `u17`/`reimport-verify` 与完整本机路径 |
| `qa_request_logs` 全表 | 10 行 | 含提问原文 |
| `health_check_snapshots` 全表 | 197 行 | 检查历史 |
| `rule_pending` 中 `status='pending'` | 4467 行 | 未审核 |
| `classification_queue` 中 `status='ai_processing'` | 3016 行 | 中断残留 |

### 4.2 保留

| 目标 | 实测规模 |
| --- | --- |
| `specifications` + `clauses` | 5 本规范 / 3044 条 |
| `classification_rules` | 125 条 |
| `lexicon_entries` | 674 条 |
| `term_labels` | 2 条 |
| `rule_pending`：`approved`(87) + `rejected`(442) | 已确认 + 黑名单 |
| `classification_queue`：`review`(1571) + `auto_adopted`(38) | 保留 AI 分类成果，省对方重跑费用 |
| `param_profiles` + 各 `classify.*` / `search.*` / `qa.*` 调优值 | 调优成果 |
| `settings` 中后端选择（`ai.backend` 等） | 仅清 key，保留选择 |

保留的规范明细：

| code | 条文数 | 图片数 |
| --- | --- | --- |
| JTG/T 3610-2019 | 709 | 0 |
| GB 50086-2015 | 665 | 40 |
| JTG F80/1-2017 | 711 | 20 |
| CJJ 2-2008 | 887 | 10 |
| JGJ 107-2016 | 72 | 8 |

### 4.3 改写与重置

| 项 | 处理 |
| --- | --- |
| `SECRET_KEY` | 生成随机值写入 `.env`（`config.py:12` 默认值为 `dev-secret-change-me`，不可分发） |
| `users.password_hash` | 重置为 `--admin-password` 参数指定值的哈希，**默认 `admin`** |
| `specifications.source_path` | 清空（本机绝对路径，模板不展示、代码不消费，仅消除信息泄露） |
| `specifications.output_dir` | **不可清空**（图片路由依赖，见 §7.3）——按 §7.3 的方案处理 |

---

## 五、线 3：启动器

PyInstaller `--windowed` 打包，**不 import `app`**。

> 不可打包整个应用的原因：冻结后 `Path(__file__).resolve().parent.parent`（`app/ai/embedding.py:14`、`app/main.py:103`、`app/main.py:107`）会指向 `_MEIPASS` 临时目录，路径全部失效。启动器只负责起子进程，天然规避。

**职责链**：

1. **依赖自检**（在启动服务之前）——运行
   `runtime/python.exe -c "import torch, lancedb, jieba, fitz; print('ok')"`
   - 通过 → 继续；失败 → **弹出明确提示 + 写日志**，不静默失败
   - 这一条把"依赖能否加载"的验证，从"发布前的干净机测试"变成"每次启动的自动检查"
2. 探测 8000 端口，被占用则递增至找到空闲端口（沿用 08-25 决策）
3. 用 `runtime/python.exe` 的**绝对路径**启动子进程：
   `-m uvicorn app.main:app --host 127.0.0.1 --port <port>`（**无 `--reload`**）
   - **绝不使用裸 `python`** —— 本机 PATH 里有 Python，会让"自带运行时"的真实状态被掩盖，造成假通过（§9.2）
4. **用 Windows Job Object 把子进程绑定到启动器**：父进程终止时由系统回收子进程。
   这覆盖"启动器被强杀"的情况（项目 CLAUDE.md §三 记录的 uvicorn 残留进程坑）
5. 轮询等待服务就绪后，调用 `webbrowser` 打开浏览器
6. `pystray` + `Pillow` 建立托盘图标常驻；右键菜单提供「打开界面」「退出」
7. 日志首行**打印实际使用的 `python.exe` 完整路径**，供 L1/L2 测试核对是否指向包内

**绑定地址固定 `127.0.0.1`**：仅本机可访问。与 `AUTH_ENABLED=0` 组合是安全的；若改为 `0.0.0.0` 则必须同时开启鉴权（见 §7.4 风险）。

---

## 六、线 4：打包

产出单一 zip。构建脚本职责：

1. 校验前置条件（干净 3.12 环境已装齐依赖）
2. 复制便携 CPython 运行时 → `runtime/`
3. 复制应用代码（按 §2 排除项过滤）
4. 复制模型目录，**删除冗余的 `pytorch_model.bin`**（与 `model.safetensors` 内容重复，省 95MB；依据 08-25 稿与本次实测）
5. 复制脱敏后的 `spec_query.db`、`lance_db/`、`data/outputs/` 的 5 个目录
6. 生成 `.env`
7. 打包 pyinstaller 产出的启动器 exe
8. 输出 zip

---

## 七、代码修复（四项）

均遵循项目规范：**先写测试（正常/边界/异常）→ 跑增量测试 → pyright 不新增 error → 单独 commit**。

### 7.1 修复 1（A 方案）：一次性孤儿清理脚本

清理 `data/uploads/` 与 `data/outputs/` 中未被 `specifications` 引用的条目。复用 `app/routes/import_routes.py:1077` `_cleanup_task_artifacts` 的引用集逻辑。

**实测收益**：uploads 36 个孤儿 / 42.3MB；outputs 33 个目录 / 5.8MB；合计 ≈48MB。

**实现要点**：`output_dir` 因规范 code 含 `/` 会形成嵌套（如 `outputs/JTG/T 3610-2019`），**不可按顶层目录粗暴判断**，必须用前缀匹配（`k == key or k.startswith(key + "/")`），否则会误删 `JTG/`、`JTG F80/` 这类父目录。

### 7.2 修复 2（C 方案）：导入后删除冗余图片

`app/routes/import_routes.py:584-599` `_copy_ocr_images` 把图片复制到 `outputs/{code}/imgs/` 后**未删源**，导致每次成功导入都多留一份 `outputs/{task_id}/imgs/`。

**修法**：复制**成功之后**删除 `outputs/{task_id}/imgs/`。

**两个约束**：
1. **必须保留 `outputs/{task_id}/{task_id}.md`** —— `scripts/reimport_specs.py`（解析器改动后的库级验收工具）依赖它（`reimport_specs.py:50-59`，`task_id` 反推自 `source_path` 的文件名主干）。md 体积可忽略，保留以保住该能力。
2. **必须"复制成功才删源"** —— `shutil.copytree` 失败时若已删源会丢图。需校验目标存在后再删，或置于 try 成功分支。

### 7.3 修复 3（B 方案）：图片路由增加路径回退

`app/routes/spec_routes.py:169` 直接用数据库中存的 `output_dir` 绝对路径读图：

```python
img_path = Path(row["output_dir"]) / "imgs" / safe_name
```

分发给他人后该路径不存在，**78 张正文图片将全部 404**。

**修法**：查询语句同时选出 `code` 列；目标路径不存在时，回退用 `OUTPUT_DIR / <code>` 拼接。

> 该修复同时消除本机的 code 含 `/` 导致路径嵌套的隐患，故 §4.3 中 `output_dir` 可保留原值不必改写。

### 7.4 修复 4：鉴权开关

保留鉴权链路代码，增加配置开关使其默认关闭。

```python
# app/config.py
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"

# app/main.py AuthMiddleware.dispatch 首部
if not AUTH_ENABLED:
    request.state.username = "admin"      # 唯一注入点，73 处 log_action 自动跟随
    return await call_next(request)
```

**依据**：全项目 `log_action` 的用户名均取自 `getattr(request.state, "username", "")`，而该值仅在 `app/main.py:144` 一处赋值——改这一行即可让全部日志记为 `admin`。

**安全联动（文档必写）**：`AUTH_ENABLED=0` + 绑定 `0.0.0.0` = 任何人可访问且无门槛。默认绑 `127.0.0.1` 时安全；若改为局域网共用，必须同时开启鉴权。

---

## 八、测试策略

| 对象 | 测试内容 |
| --- | --- |
| 修复 1 | 孤儿被删、被引用文件保留、嵌套父目录（`JTG/`）不被误删 |
| 修复 2 | 确认后 `imgs/` 被删、`{task_id}.md` 保留、复制失败时不删源 |
| 修复 3 | 路径存在时走原逻辑；路径不存在时回退 `OUTPUT_DIR/code` 成功返回图片 |
| 修复 4 | 开关关闭时无 cookie 可直达、日志用户名为 `admin`；开关打开时原鉴权行为不变 |
| 脱敏脚本 | 产出后断言：5 个 key 与 base_url 为空、清空表行数为 0、保留表行数符合 §4.2、admin 可用参数密码登录 |
| 分发包 | 目标环境端到端（见 §9.3） |

---

## 九、验收标准

### 9.1 分层验证策略

不追求一步到位的"纯净虚拟机"——VM（VirtualBox + 完整镜像，数小时 + 数 GB）降为**最末选项**。

| 层级 | 做法 | 能验出什么 | 成本 |
| --- | --- | --- | --- |
| L1 | 本机解压到**与项目无关的路径**（含空格/中文，如 `E:/test/规范检索系统/`），双击启动器 | 路径写死、空格/编码问题 | 5 分钟 |
| L2 | 新建**本地用户账户**登录后测试 | 用户级环境依赖（本机 Python 挂在**用户** Path 上，新账户不会继承） | 10 分钟 |
| L4 | **对方机器首次安装** | 真实环境，最彻底 | 0 |
| L5 | VirtualBox + Windows 镜像 | 完全干净 | 数小时 + 数 GB |

**环境约束**：本机为 Windows 10 **Home 版**，Windows Sandbox（需 Pro/Enterprise）不可用，故轻量沙盒这条路不存在。

### 9.2 "假通过"风险（本机测试的核心危险）

本机测试的危险**不是**"环境不干净导致失败"，而是**"环境太好导致假通过"**：本机 PATH 有 Python、系统装了 VC++ 运行库——若启动器实现不严谨，本机顺利通过，到对方机器才炸。

**应对**：
1. 启动器用自带 runtime 的**绝对路径**（§5 第 3 步）；
2. 日志首行打印实际 `python.exe` 路径，L1/L2 时核对是否指向包内；
3. L1 使用与项目无关的路径，避免隐式相对路径偶然生效。

### 9.3 端到端检查清单

在目标环境（L4 或 L5）上：

1. 解压 zip，双击 `启动.exe`
2. **无黑色控制台窗口**，浏览器自动打开
3. 无需登录，直接进入主界面
4. 能检索到 3044 条条文
5. 条文正文中的图片正常显示（不裂图）
6. 能完整导入一本新规范（OCR → 分类 → 向量索引）
7. 托盘图标存在，右键可退出，且**验证 Job Object 生效**（启动器退出后无 uvicorn 残留进程）

---

## 十、对宿主机的写入边界

用户明确关切："在对方机器上测试会不会污染主机？" 以下为**逐项代码核实**结果（非印象）。

**不会发生**：

| 检查项 | 结果 |
| --- | --- |
| 写注册表 | ❌ 不碰（grep 命中的"注册表"均为项目自身的参数注册表概念） |
| 写用户目录 / AppData | ❌ `app/`、`scripts/` 零命中 `expanduser` / `Path.home()` |
| 装系统服务 / 开机自启 | ❌ 不做 |
| 改系统 PATH / 环境变量 | ❌ 不做 |
| 写 `System32` | ❌ 不写（DLL 放应用目录） |
| 需要管理员权限 | ❌ 不需要 |
| 业务文件写入 | ✅ 全部经 `app/config.py:16-26` 的路径常量，默认都落在**解压目录内** |
| 临时文件 | ✅ `app/ocr/paddle_api.py:338-348` 用 `tempfile.mkstemp` 后 `finally: os.unlink()` 立即清理 |
| 卸载 | 删除解压目录即可，无残留 |

**会发生的（需知情）**：

| # | 行为 | 说明 |
| --- | --- | --- |
| 1 | 模型缓存目录 | `~/.cache/huggingface`(53K)、`~/.modelscope`(1K) 由模型库自动创建，仅元数据不含权重。**唯一写到解压目录之外的路径** |
| 2 | 防火墙规则 | 绑 `127.0.0.1`（仅回环）通常不触发提示；若弹出且用户允许，会加一条入站规则 |
| 3 | 残留进程 | 启动器被强杀时 uvicorn 子进程可能孤儿化。**由 Job Object 根除**（§5 第 4 步） |
| 4 | 数据外发（隐私） | 导入规范 → PDF 上传至百度 / PaddleOCR 云端；AI 问答 → 文本发往 AI 服务商。默认 key 已清空，**不配则不发生** |

第 4 项**必须写进使用说明**——对方对数据去向有知情权。

**结论**：这是一个纯用户态、可删除的程序。真正的风险不在"污染主机"，而在残留进程（可根除）与数据外发（需告知）。这也是便携 zip 相对安装包的核心优势——安装包才需要写注册表、装到 Program Files、建开始菜单项，卸载还常留垃圾。

---

## 十一、风险与未决项

| 项 | 说明 |
| --- | --- |
| 依赖组合需实测 | 3.12 环境下 `torch`(CPU)/`lancedb`/`pyarrow` 的版本搭配未经实测，是唯一需要先验证的前置条件 |
| **VC++ 运行库** | 便携 CPython 自带 `vcruntime140*.dll`，但**不含** `msvcp140*.dll`（C++ 标准库，torch/PyMuPDF 依赖）。本机因系统装了 VC++ Redistributable 才正常。**对策：5 个 DLL 共 1.13MB 复制进 `runtime/`**（Microsoft 认可的 app-local 部署）。注意**打包不等于被加载**，仍须 §9 验证 |
| 启动器杀软误报 | PyInstaller 产物的常见问题，无法完全规避；可备一个 `.bat` 作为后备入口 |
| Windows Home 无 Sandbox | 轻量沙盒不可用，VM 只能走 VirtualBox/VMware（见 §9.1） |
| 未验证项 | 分发包在真实目标机器上的表现，只能由 §9.3 的端到端验收证明 |
| 明确不做 | 改密码 / 登录页优化 / agent API / 云服务器部署 / 文件名规范化 / 重导 UI |

---

## 附：本次讨论中被否定的方案

| 方案 | 否定理由 |
| --- | --- |
| PyInstaller 打包整个应用 | 冻结后 `BASE_DIR` 指向 `_MEIPASS`，需大改代码适配 |
| 安装包（Inno Setup） | 需管理员权限、写注册表、装到 Program Files，风险高于便携 zip |
| 模型转 fp16 | 本次仅带 95MB 的 bge-small-zh，收益不足 50MB，不值得引入"预置向量与查询模型精度失配"的风险 |
| 模型压缩包 + 启动器解压 | 模型权重压缩率仅 91.7%（几乎压不动），且 95MB 无需解压步骤 |
| 上传文件名规范化 + 重导 UI | 用户实际重导行为是从自己的文件夹选文件重新上传，不需要从 uploads 挑旧文件；重导 UI 是伪需求 |
| 上传内容去重（原 D 方案） | 现有 `file_hash` 判重（`import_routes.py:405-428`）已在落盘前硬拦截，等效覆盖 |
| 强制 SECRET_KEY / 首次登录改密码 | 鉴权默认关闭后无必要，且改密码功能本次不做 |
| 不打包 Python 运行时（依赖对方环境） | 接收方环境不可知且离线；自包含才能做到"解压即用"（见 §2） |

---

## 附二：验证方法教训

**命令报错不等于结果为否**（2026-09-30 实测踩坑）

排查"Python 是否在 PATH 里"时执行：

```bash
reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path | grep -i python
```

该命令在 Git Bash 下实际**报错**（`错误: 无效语法`），但错误信息被管道后的 `grep` 滤掉，输出为**空**——于是把"命令失败"误读成了"查无结果"，并据此得出了**错误结论**（用户当场用系统环境变量界面指出 Python 确实在 PATH 中；重新核实确认它挂在**用户级** Path 上）。

**规则**：判定"不存在"之前，必须先确认命令**本身成功执行**——检查退出码，或单独打印 stderr。**不要把管道过滤后的空输出当作否证。**

同类陷阱参见记忆 `verification-traps`。
