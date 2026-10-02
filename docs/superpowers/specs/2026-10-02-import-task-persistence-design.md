# 导入任务台账持久化 — 设计文档

> 日期：2026-10-02
> 症状：服务重启（含开发期 `--reload`）会**静默**清空所有导入任务的进度与审查入口
> 状态：根因已实证，待实施
> 前置：A 组修复（`46cc99c` / `820280c` / `ce2e40d` / `b95cdf7` / `75d4ea3`）、A 组缺口回补（`bdef8be`）

---

## 一、症状与复现

用户操作序列：导入一本规范 → 中途退出导入界面 → 切页 → 再点「导入规范」。观察到：

1. 弹窗里没有该任务的进度（只看到空表单）；
2. 右下角浮标停在「导入中 20%」，点击无反应、也进不去导入界面。

排查中发现**第三个、也是更根本的问题**：该任务的台账条目在排查过程中消失了。

### 1.1 现场时间线（本次实证，非推测）

| 时刻 | 事实 | 证据 |
| --- | --- | --- |
| 15:54 | 上传 `411d4975.pdf`（1.85 MB） | `data/uploads/411d4975.pdf` mtime |
| 15:59 | OCR 完成，正文落盘 | `data/outputs/411d4975/411d4975.md`（281 869 B）mtime |
| 16:02:11 | 任务仍在，状态 `review_needed` | `curl /import/progress/411d4975/json` → `{"status":"review_needed","progress":50,…}` |
| **16:02:54** | **worker 进程新建**（PID 3984） | `wmic … CreationDate` |
| ~16:03 | 浏览器内 `fetch` 同一端点 → `{"status":"unknown"}` | 页面内实测 |
| 16:03:20 | `curl` 同一端点 → 也变成 `unknown` | 与浏览器一致 |
| **16:03:39** | **worker 进程再次新建**（PID 17032） | `wmic … CreationDate` |

两个新建时刻与「创建 `.verify/*.py` 探针脚本」的时刻重合。`uvicorn --reload` 默认监视
**整个工作目录**下的 `*.py`，因此写探针脚本会重启 worker —— 而 `progress_store` 是模块级
内存字典，一重启即全清。

同一任务两次查询得到相反答案，原因正是"由不同的 worker 作答"：16:02 那次由**旧 worker**
（15:32 启动，台账尚在）作答；16:03 那次由**重启后的新 worker**（16:02:54 启动，台账为空）
作答。两次观测之间发生了一次重启，而不是两个 worker 同时在跑。

### 1.2 用户视角的两个缺口（已修，记录以备追溯）

它们与本设计无耦合，但正是本次排查的入口，且**放大了**持久化缺失的后果：

- **弹窗不恢复进度**（`bdef8be` 修）：A 组最初只在浮标上做了跨页恢复，刻意没碰弹窗；
  而整页跳转会销毁弹窗 DOM 里原有的 `#import-status`，于是切页后再点导入只看到空表单。
- **浮标在"导入中"状态不可点**（`bdef8be` 修）：方案里写的是"点击展开详情/去导入页"，
  实现成了纯 `<span>`。浮标由此成为**只读的死信号**：既不能点进去，也没有别的入口。

### 1.3 复现前提

只要在导入进行中发生任何一次 worker 重启即可复现。除开发期 `--reload` 外，生产环境下
进程崩溃、手动重启、部署都会触发同一条路径。

---

## 二、实测证据

### 2.1 台账读点已收敛（A 组重构的直接收益）

`app/routes/import_routes.py` 全文对 `progress_store` 的引用（`grep` 实测处数）：

| 类别 | 处数 | 说明 |
| --- | --- | --- |
| `_update_task(task_id, **fields)` | 16 | A 组已把 15 处 `.update(` 与 8 处字段赋值全部收敛到此 —— **写路径已是单点漏斗** |
| `progress_store.get(...)` 直读 | 10 | 2 处进度端点（HTML 片段 + JSON）、4 处 `owner` 取用（`log_action` 用）、4 处任务读取（审查页 / 取正文 / 确认 / 取消） |
| `_get_task(task_id)` | 1 | 向量分批循环里的取消守卫 |
| `iter` / `pop` 类 | 4 | 清理遍历 `list(items())` 1 处、`pop` 3 处（清理 / 上传失败回滚 / 取消） |

两点结论：**① 写路径已是单点漏斗**；**② 全部读点都是 `.get` 形态，没有一处直接下标**，
因此换存储底座是机械替换，不需要逐个重新推理语义。

### 2.2 现有限制

| 事实 | 位置 |
| --- | --- |
| `progress_store = {}` 模块级内存字典 | `app/routes/import_routes.py:27` |
| 正文 `md_text` 也只存内存 | `_process_import` Phase 1 末尾写入 |
| 审查页的正文从内存取 | `review_content()`（`GET /import/review/{id}/content`） |
| 超期清理（A 组）作用在内存字典上 | `sweep_progress_store` + `main.py` 巡检线程 |

两档时限参数（A 组已落注册表，本设计不变）：

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `import.task_ttl_terminal_min` | 30 分钟 | done/error 超时 → 仅删条目 |
| `import.task_ttl_review_hours` | 24 小时 | review_needed 超时 → 删条目**并删磁盘产物** |

### 2.3 `md_text` 的体积

抽查真实产物：`data/outputs/c64b6c3b/c64b6c3b.md` = 460 011 字符（约 450 KB）。
SQLite 单行 TEXT 上限远大于此；行数由 2.2 的两档 TTL 约束，因此**行内存正文**是可接受的。

---

## 三、根因结论

**导入任务的台账（进度、状态、正文、审查入口）只存在于进程内存，任何 worker 重启都会
静默清空它。** 任务本身在服务端跑、与浏览器无关（`import.js` 的注释如此声明），但它的
**可观测性**与**后续入口**却挂在一份易失的内存上 —— 声明与实现不一致。

后果按状态分三类：

| 中断时的状态 | 后果 | 可恢复性 |
| --- | --- | --- |
| `uploading` / `processing` | 后台线程随进程消失，任务永久卡死 | 需重跑 |
| `review_needed` | **OCR 成果已产出但入口消失**（正文在磁盘或内存里） | 设计上本可恢复，现不可 |
| `done` / `error` | 终态信息消失，用户看不到结果提示 | 无关紧要 |

第二类是本设计的主要收益：OCR 是分钟级且计外部费用的操作，成果却因为一条内存记录丢失
而无法审查。

---

## 四、设计决策

### 4.1 做什么

1. **台账落主库**：新增 `import_tasks` 表，进度/状态/正文/审查所需字段全部入库。
2. **存储层替换**：保留 `_get_task` / `_update_task` 的名称与签名，实现换为 SQL；新增
   `create_task` / `delete_task` / `iter_tasks` 替代直接操作字典之处；**删除模块级
   `progress_store`**。
3. **启动自愈**：`startup()` 中把 `uploading` / `processing` 一律标为 `error`
   （「服务重启导致本次导入中断，请重新导入」），其余状态不动。
4. **清理衔接**：`sweep_progress_store` 改为遍历表行；两档时限参数与删除语义不变。

### 4.2 不做什么，以及为什么

| 不做 | 理由 |
| --- | --- |
| **不加进程内缓存** | 有缓存就等于把"重启丢失"换个形式再引入一次，还多一个失效窗口。这里全是主键点查（微秒级），进度轮询 2 s 一次，压力可忽略 |
| **不自动重跑被中断的任务** | 用户裁定。自动重跑 Phase 1 会真调外部 OCR（分钟级 + 计费）；若中断发生在 Phase 2 则会重复入库（导入是"新增"语义，同编号会并排新增一条） |
| **不做"从 Phase 2 续跑"** | 需额外防重复入库 + 区分中断阶段，实现量显著更大；本轮不做，但记入待办候选 |
| **不改 `md_text` 的落盘约定** | 统一让 `PaddleVLClient` 之外的分支也把正文写到 `OUTPUT_DIR/{task_id}/` 属另一处改动，且正文入库已能覆盖审查页与复活需求 |
| **不动 `specifications` 表结构** | 本设计与规范数据无耦合，新表独立 |

### 4.3 关键设计点

- **`updated_at` 用 REAL（epoch 秒）**，与内存版同型 → A 组 `_task_disposition` 的年龄
  计算逻辑一行不用改。
- **`_update_task` 的返回值语义保持不变**（任务不存在返回 `False`，调用方据此收工）。
  A 组已用它消除「取消后残留线程 KeyError」，落库后该保护依然成立，并且**跨进程**成立。
- **UPDATE 只写传入的字段，不做全行覆盖**。OCR 期间 `progress_cb` 会反复只更新
  `message`；若实现成"读全行 → 合并 → 整行写回"，就会把 460 KB 的 `md_text` 每次重写
  一遍。`_update_task(**fields)` 的字面形状已经天然支持按字段生成 `SET` 子句，实现时
  不要退回全行写。
- **启动自愈必须是同步的**，且排在 `init_db()` 之后、开始接受请求之前 —— 否则可能出现
  「请求读到仍标 processing 的僵尸任务」。
- **`review_needed` 天然复活**：不改任何状态，因为正文与所需字段都在行里；审查页
  `GET /import/review/{id}` 与 `/content` 照旧工作。

---

## 五、表结构与存储层

### 5.1 表定义

加入 `app/database.py` 的 SCHEMA（`CREATE TABLE IF NOT EXISTS`，加法式，既有库自动补建）：

```sql
CREATE TABLE IF NOT EXISTS import_tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                 -- uploading/processing/review_needed/done/error
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',       -- 取消/确认的属主校验用
    updated_at REAL NOT NULL,             -- epoch 秒
    md_text TEXT,                         -- Phase 1 产物：审查页与"待审查"复活的唯一来源
    title TEXT, code TEXT, file_path TEXT, file_name TEXT,
    file_hash TEXT, spec_status TEXT, replaced_by_code TEXT
);
```

字段与现内存字典**逐一对齐**（含 A 组新增的 `updated_at`），迁移不改任何调用方语义。
主键即唯一查询键；清理是全表扫描，行数由 TTL 约束。

### 5.2 存储层 API

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `create_task` | `(task_id, owner) -> None` | 替代 `progress_store[task_id] = {...}`。写入初始行：`status='uploading'`、`progress=0`、`message='正在上传...'`，并盖 `updated_at` |
| `_get_task` | `(task_id) -> dict \| None` | 名称签名不变，实现改查库 |
| `_update_task` | `(task_id, **fields) -> bool` | 名称签名不变；任务不存在返回 `False` 且不抛异常；一条 UPDATE，顺带刷新 `updated_at` |
| `delete_task` | `(task_id) -> None` | 替代 `pop`（取消、上传失败回滚） |
| `iter_tasks` | `() -> list[dict]` | 替代 `list(progress_store.items())`（清理遍历） |

调用点返回值一律**行内 `None` 安全**：`_get_task` 取不到即 `None`，调用方沿用现有
`is None` / `not ...` 判断，逻辑不新增分支。

### 5.3 启动自愈

`app/main.py` 的 `startup()` 中、`init_db()` 之后同步执行：

```sql
UPDATE import_tasks
   SET status='error', progress=0,
       message='服务重启导致本次导入中断，请重新导入', updated_at=?
 WHERE status IN ('uploading','processing');
```

只动这两态。前端无需新增分支 —— `error` 已有渲染路径（`import_progress.html` 的
error 分支 + 浮标与弹窗的 error 文案）。

**`updated_at` 必须一并刷新**：清理器按"距 `updated_at` 的年龄"判定（见 5.4）。若不刷新，
一个中断很久的任务在重启瞬间就会立刻满足终态 TTL 而被清掉 —— 用户刚重启就看到提示、
转身条目已消失，等于把"静默丢失"推迟了几秒。刷新后它至少能活满一档终态保留期。

### 5.4 与 A 组清理的衔接

`sweep_progress_store` 改为遍历 `iter_tasks()`；`_task_disposition` 不动。动作语义重命名
为「删行」与「删行 + 删磁盘」：

- `drop_memory` → `delete_task(task_id)`，磁盘一律不动；
- `drop_memory_and_disk` → `delete_task(task_id)` + `_cleanup_task_artifacts(task_id)`，
  后者自带「被 `specifications` 引用则不删」的保护（A 组已核实存在，不重复实现）。

---

## 六、风险与验证

### 6.1 风险

| 风险 | 评估 | 处置 |
| --- | --- | --- |
| OCR 期间写入变频繁（`progress_cb` 每次回消息一条 UPDATE） | 队列重试间隔数十秒，非高频 | 实测确认；若成为瓶颈再考虑合并写 |
| `md_text` 单行最大约 460 KB | 由两档 TTL 约束行数 | 随清理一并回收 |
| 新表影响既有数据 | 纯新增表，与 `specifications` 无外键耦合 | 迁移为 `IF NOT EXISTS`，零改动 |
| 落库后读点变慢 | 主键点查 | 实测 `GET /import/progress/{id}/json` 耗时对比 |
| 自愈误伤 | 只匹配两态；`review_needed` 不受影响 | 单测穷举五态 |

### 6.2 验证方式

**pytest**（新文件 `tests/test_import_task_store.py`）：

- CRUD 往返；`_update_task` 对不存在任务返回 `False` 且不抛异常；
- `updated_at` 每次更新被刷新；
- **「无进程内状态」断言**：写入后**新开一个连接**即可读到 —— 语义上等价于"重启后仍在"；
- 启动自愈只改 `uploading` / `processing`，五态穷举；
- `sweep_progress_store` 在库上生效（删行 / 删行+删盘 / 被引用文件不删 / 时限来自参数）。

**一次性真实验收（探针做不了）**：

"持久"这件事**无法由探针单独证明** —— 探针跑在一个已运行的服务上，而刷新页面根本不会
重启服务，因此"刷新后还能看到"只证明了浏览器侧，不证明落库。真正的判据是**重启服务**，
而探针无法重启自己依赖的服务。故：

- pytest 用「新开连接即可读到」在**语义上**等价地证明"无进程内状态"（见上）；
- **真停服务再启**由实施者在探针之外手工执行一次并留证据：停 8123 → 启 →
  ① 停在 `review_needed` 的任务审查页仍可进、`/content` 仍返回正文；
  ② 曾处于 `uploading`/`processing` 的条目被标为 `error` 且文案正确；
  ③ `GET /import/progress/{id}/json` 仍返回该任务而非 `unknown`。

**回归范围**：`tests/test_import*.py`、`tests/test_ocr_review.py`、`tests/test_param*.py`
全量 + 探针 f7/f8/f10/f11（f8 与弹窗/浮标强相关）。

### 6.2.1 实测记录（2026-10-02，Task 4）

**口径**：全程只用隔离实例（`data/_probe_spec.db` 副本 + `_probe_uploads` /
`_probe_outputs` / `_probe_lance` + 端口 8123），真实库 `data/spec_query.db`、
`data/uploads`、`data/outputs`、`lance_db` 一个字节未写。8000 端口的开发服务未动。

现场：先跑探针 `f8`（9/9 passed）留下一个真实 `review_needed` 任务（`edc3cdbd`），
再手工塞入僵尸任务 `dead0001`（`status='processing'`, `progress=20`）。

#### ① 重启前基线

```
$ curl -s -b <cookie> http://127.0.0.1:8123/import/progress/edc3cdbd/json
{"status":"review_needed","progress":50,"message":"OCR 完成，请审查识别结果","needs_review":true}

$ curl -s -b <cookie> http://127.0.0.1:8123/import/progress/dead0001/json
{"status":"processing","progress":20,"message":"正在 OCR 识别...","needs_review":false}
```

#### ② 读点延迟实测（§6.1「落库后读点是否变慢」）

连打 20 次 `GET /import/progress/edc3cdbd/json`，`curl -w "%{time_total}"` 原始值（秒）：

```
0.005184 0.006392 0.003700 0.005108 0.004140 0.005728 0.005017 0.004054 0.004540
0.004322 0.004497 0.004175 0.005233 0.004348 0.004262 0.004906 0.004406 0.004803
0.004518 0.004073
```

聚合：**平均 4.7 ms**（n=20），最小 3.7 ms，最大 6.4 ms。轮询间隔 2000 ms，
远小于该值 ⇒ 风险项「落库后读点变慢」不成立（主键点查如期为毫秒级）。

#### ③ Phase 2 进度冻结窗口实测（控制者裁定 R7）

背景：Phase 2 的进度写入并入外层写事务（`task-1-review.md` I-1），commit 之前对
HTTP 端点不可见，用户会看到进度条停在某一档不动、直到整段跑完跳到 100。
本项把该窗口的真实长度量出来。

夹具：1200 条 `.md`（每行带时间戳使 `file_hash` 每次不同），解析结果 1200 条条文
（离线校验：`parsed clauses: 1200`），跨 3 个向量写入批次（`VECTOR_WRITE_BATCH=500`）。
采样脚本 `.superpowers/sdd/2026-10-02-import-task-persistence/task-4-measure-phase2-visibility.py`
每 0.5 s 采样一次，遇终态自停。

完整原始输出：

```
模态：processing → done；共 21 次采样 / 2 个可见档位

    首见(s)    持续(s)  progress  状态 / message
     0.03     9.87        70  processing / 正在分类 1200 条条文...
    10.43     0.00       100  done / 导入完成：1200 条条文已解析，0 个维度已分类

最长一次「同一个数不动」：9.87s（progress=70，message='正在分类 1200 条条文...'）
向量阶段（progress>=70）最长的冻结档：9.87s

终态：status=done progress=100
```

**结论数字：最长一次「同一个数不动」= 9.87 秒，发生在 progress=70 档。** 全程只
出现 2 个可见档位（70 → 100），80 / 84 / 90+ 三档隐藏档位一次都没露面 —— 冻结现象与
I-1 的描述完全一致。

**条件说明（不得当成推算值）**：本次实测的 embedding 模型是**热**的（应用启动时
`_startup_warmup_embedding` 已预热，同实例此前也跑过导入），故 9.87 s **不含**模型
首次加载。设计文档估算冷启动加载约 30 s；该数字是估算，未在本次实测中测到，不得
与本处的 9.87 s 相加或并列当实测值。

**采样脚本的一处环境适配（不改脚本语义）**：Windows 下 curl 的 cookie jar 写作
`#HttpOnly_<domain>\t…\taccess_token\t<token>`（行首带 `#`、行尾 CRLF），而脚本的
解析器只认「不以 `#` 开头且第 6 列恰为 `access_token`」的行 ⇒ 首次运行时它从未取到
token，被 `AuthMiddleware` 重定向到登录页，300 s 空采样后以「没有采到任何样本」退出
（**该次无效，未写入本记录**）。改为喂入一份去掉 `#HttpOnly_` 前缀、行尾换 LF 的
同内容 cookie 文件后即正常工作；上面的数字来自修正后的那次运行。

#### ④ 真停服务再启（按项目 CLAUDE.md §三 顺序）

杀进程前：

```
$ netstat -ano | grep ':8123' | grep LISTENING
  TCP    127.0.0.1:8123         0.0.0.0:0              LISTENING       836
$ taskkill //F //PID 836
成功: 已终止 PID 为 836 的进程。
$ netstat -ano | grep ':8123' | grep LISTENING     # 无输出
```

`wmic … CommandLine` 核查：8123 无 `uvicorn`/`spawn_main` 活进程（同期存活的只有
8000 端口的开发服务 `12888` 及其 worker），判定端口确实干净后，以相同四条环境变量重启。
重启日志（`.verify/_probe_uvicorn.log`）：

```
WARNING [app.main] 启动自愈：1 个导入任务因服务重启被中断，已标记为需重跑
INFO:     Application startup complete.
```

自愈 WARN 排在 `Application startup complete` **之前**，符合「同步执行、开始接受请求
之前完成」的设计要求（4.3 关键设计点）。

#### ⑤ 重启后逐条核对

```
① 待审查任务仍在（期望 review_needed / needs_review=true）
$ curl -s -b <cookie> http://127.0.0.1:8123/import/progress/edc3cdbd/json
{"status":"review_needed","progress":50,"message":"OCR 完成，请审查识别结果","needs_review":true}

② 审查页与正文（期望 200 且返回正文）
$ curl -s -b <cookie> -o /dev/null -w "review_page:%{http_code}\n" http://127.0.0.1:8123/import/review/edc3cdbd
review_page:200
$ curl -s -b <cookie> http://127.0.0.1:8123/import/review/edc3cdbd/content
{"content":"1 ··\n1.0.1 ···················\n1.0.2 ·······················…"}   # HTTP 200，content 130 字符
（"·" 是探针 PDF 用默认字体写入中文、PyMuPDF 无法编码所致，与本次验收无关；要点是正文非空且可取）

③ 僵尸任务被自愈为 error，文案正确
$ curl -s -b <cookie> http://127.0.0.1:8123/import/progress/dead0001/json
{"status":"error","progress":0,"message":"服务重启导致本次导入中断，请重新导入","needs_review":false}

④ 启动日志有自愈 WARN（见 ④ 节的日志行）
```

**浏览器侧补验**（Playwright，`channel="chrome"`，headless）：登录 → 预置
`sessionStorage.importTaskId=edc3cdbd` → 回首页，读到的浮标与入口为：

```
浮标文案 = '📝 待审查点击继续审查'
浮标「去审查」入口 href = '/import/review/edc3cdbd'
点击后 URL = http://127.0.0.1:8123/import/review/edc3cdbd
审查页条目数 = 1
BROWSER_CHECK_PASS
```

即重启后待审查任务在浮标上**仍可见、仍可一键进入审查页**。

> 注：简报设想的是「点浮标 → 弹窗里有『去审查』入口」，但实测 `review_needed` 态的
> 浮标是**直达链接**（`a.import-floater__link` → `/import/review/{id}`，文案
> 「📝 待审查 / 点击继续审查」），一步到位、不经弹窗；「点浮标开导入弹窗」是
> `processing` 态的形态（由探针 f8_7 覆盖）。故本处按真实契约断言直达链接可点、能进审查页。

#### ⑥ 收尾

```
$ taskkill //F //PID 24096            # 成功: 已终止 PID 为 24096 的进程。
$ netstat -ano | grep ':8123' | grep LISTENING      # 无输出
$ wmic … python.exe CommandLine → 8123 残留：无；存活 uvicorn/spawn_main 仅 8000 的开发服务(12888)与其 worker
$ rm -rf data/_probe_spec.db data/_probe_uploads data/_probe_outputs data/_probe_lance data/_probe_phase2.md
$ rm -f  .verify/_cookie.txt .verify/_probe_uvicorn.log .verify/_cookie_lf.txt .verify/_latency_raw.txt .verify/_freeze_raw.txt .verify/_probe_browser_check.py
$ ls -d data/_probe_*  →  无
```

---

## 七、附：开发期 `--reload` 误伤

本次排查的触发条件是 `uvicorn --reload` 监视**整个工作目录**的 `*.py`，因此编辑
`scripts/`、`tests/`、`.verify/` 下的脚本也会重启 worker。两种缓解：

1. 启动时用 `--reload-dir app`（把监视范围收窄到应用代码）；
2. 本设计落库后，重启不再清空台账，误伤面大幅缩小（只剩在跑任务的 Phase 1/2 会被标成
   `error`）。

**裁定（2026-10-02，用户）：第 1 条已写入项目 `CLAUDE.md` §三「服务重启规范」第 3 步**，
作为启动命令的必带参数（不再是可选项）。第 2 条由本设计承担。

---

## 八、遗留

- **手工回填 `411d4975` —— ✅ 已执行（2026-10-02）**：该任务先于持久化存在，本设计追不回它，
  故按用户授权用 `scripts/backfill_import_task.py` 补了一条处于 `review_needed` 的记录，
  指向 `data/outputs/411d4975/411d4975.md`（正文 130 814 字符，OCR 已完成），
  使其重新可审查、**免掉重跑那 5 分钟 OCR**。

  用户提供的身份：编号 `DB32/T 2618-2023`（由 `DB32/T 2618—2023` 归一化，与
  `spec_prefix.normalize_spec_code` 一致）、名称「高速公路工程施工安全技术规范」。
  执行前已核**库内不存在同编号/同名称**（防重复身份），并先干跑确认字段。

  核对结果（打真实开发服务 8000）：台账行 `411d4975 / review_needed / 50`；
  `/import/review/411d4975` → **200**、`/content` → **200**；进度端点返回 `needs_review: true`。
  脚本按约定记了一条 `WARN` 级「手工回填导入台账」审计（含 task_id / code / 正文长度 / 脚本名）。
- 「从 Phase 2 续跑」列为待办候选（见 4.2）。
