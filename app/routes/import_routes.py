import hashlib
import html
import logging
import re
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from app.config import UPLOAD_DIR, OUTPUT_DIR
from app.database import get_db
from app.logging_util import log_action, json_detail
from app.parser.md_parser import parse_markdown, is_cover_clause, find_degraded_heading_lines
from app.parser.ocr_clean import clean_ocr_text
from app.parser.spec_prefix import (
    detect_hierarchy, detect_nature, detect_industry, normalize_spec_code, PREFIX_WHITELIST,
)
from app.ocr.pdf_extract import extract_text, is_scanned
from app.classifier.rule_engine import classify_clause, should_use_ai
from app.classifier.batch_queue import try_enqueue
from app.search.vector_search import VectorStore
from app.search.chunking import build_embed_chunks
from app.routes.spec_routes import SPEC_STATUS_ALLOWED

router = APIRouter()

logger = logging.getLogger(__name__)

# replaced_by_code 长度上限（防超长输入污染 DB / 前端渲染）
REPLACED_BY_CODE_MAX_LEN = 100


# 台账可写列的单一来源：列名要拼进 SET 子句，故必须白名单校验
# （既防注入，也防字段名打错后静默写不进去）
_TASK_COLUMNS = frozenset({
    "status", "progress", "message", "md_text", "title", "code",
    "file_path", "file_name", "file_hash", "spec_status", "replaced_by_code",
})


def create_task(task_id: str, owner: str = "") -> None:
    """登记一条导入任务（初始 uploading）"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO import_tasks (task_id, status, progress, message, owner, updated_at) "
            "VALUES (?, 'uploading', 0, '正在上传...', ?, ?)",
            (task_id, owner, time.time()),
        )


def _get_task(task_id: str) -> dict | None:
    """取任务台账条目；不存在返回 None。

    台账取用一律走本函数与 `_update_task`。`cancel_review` 只在 `status == "done"`
    时拒绝取消，也就是**允许取消 processing 中的任务** —— 该任务随后的状态写入必须
    安全地失败，而不是抛异常。

    返回**普通 dict**：调用方大量使用 `task.get(...)`，而 `sqlite3.Row` 没有 `.get`。
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM import_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _update_task(task_id: str, conn: sqlite3.Connection | None = None, **fields) -> bool:
    """安全更新台账条目；任务不存在时返回 False（调用方据此提前收工）。

    顺带盖 `updated_at`：超期清理全靠它算年龄。

    ⚠ **只写传入的字段**，绝不"读全行 → 合并 → 整行写回"：OCR 期间 progress_cb 会
    反复只更新 message，整行写回会把 md_text（实测最大 460 KB）每次重写一遍。

    ⚠ **`conn` 只在「调用方已持有未提交写事务」时必须传入**（Phase 2 的条文插入 →
    向量阶段之间正是这种情形）。`import_tasks` 与 `specifications`/`clauses` 同库，
    SQLite 同一时刻只允许一个写者：此时另开连接会被写锁挡住，抛
    `sqlite3.OperationalError: database is locked`。传入外层连接让这次 UPDATE 并入
    外层事务（由调用方负责 commit），既不再撞锁，也不改变事务边界与回滚语义。
    """
    unknown = set(fields) - _TASK_COLUMNS
    if unknown:
        raise ValueError(f"未知台账字段: {sorted(unknown)}")
    values = dict(fields)
    values["updated_at"] = time.time()
    assignments = ", ".join(f"{col} = ?" for col in values)   # 列名已过白名单
    sql = f"UPDATE import_tasks SET {assignments} WHERE task_id = ?"
    params = (*values.values(), task_id)
    if conn is not None:
        return conn.execute(sql, params).rowcount > 0
    with get_db() as db:
        return db.execute(sql, params).rowcount > 0


def delete_task(task_id: str) -> None:
    """删除台账条目（幂等：不存在也无妨）"""
    with get_db() as conn:
        conn.execute("DELETE FROM import_tasks WHERE task_id = ?", (task_id,))


def iter_tasks() -> list[dict]:
    """列出全部台账条目（供超期清理遍历）"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM import_tasks").fetchall()
    return [dict(r) for r in rows]


def heal_interrupted_tasks() -> int:
    """启动自愈：把已随进程消失的 running 态任务标为 error，返回影响行数。

    `uploading` / `processing` 的后台线程随进程死亡，任务不可能再推进；标成 error
    才能让用户看到明确结论，而不是一个永远转圈的"进行中"。

    **只动这两态**：`review_needed` / `done` / `error` 一律不动。待审查任务因此天然
    复活——它的正文与所需字段都在行里，审查页照旧可用。

    **顺带刷新 `updated_at`**：不刷的话，一个中断很久的任务在重启瞬间就满足终态 TTL
    被清理器立刻删掉，用户刚看到提示、转身条目已消失。
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE import_tasks SET status = 'error', progress = 0, "
            "message = '服务重启导致本次导入中断，请重新导入', updated_at = ? "
            "WHERE status IN ('uploading', 'processing')",
            (time.time(),),
        )
        return cur.rowcount


def _task_ttl_seconds() -> tuple[float, float]:
    """返回 (终态时限, 待审查时限)，单位秒。时限来自参数注册表，不写死。"""
    from app.params.registry import get_param_int
    terminal_min = max(1, get_param_int("import.task_ttl_terminal_min"))
    review_hours = max(1, get_param_int("import.task_ttl_review_hours"))
    return terminal_min * 60.0, review_hours * 3600.0


def _task_disposition(task: dict, now: float, ttl_terminal_s: float, ttl_review_s: float) -> str:
    """决定一条台账条目的去向：keep / drop_row / drop_row_and_disk

    纯函数，便于穷举各种 (状态, 年龄) 组合而无需真跑清理。
    """
    status = task.get("status")
    if status not in ("done", "error", "review_needed"):
        # 运行中（uploading/processing）与任何未知态一律保留：宁可留着也不误删
        return "keep"
    updated = task.get("updated_at")
    if not isinstance(updated, (int, float)):
        return "keep"                      # 算不出年龄的东西不动
    age = now - updated
    if status == "review_needed":
        return "drop_row_and_disk" if age > ttl_review_s else "keep"
    return "drop_row" if age > ttl_terminal_s else "keep"


def sweep_progress_store(now: float | None = None, *, delete_disk: bool = True) -> dict:
    """清扫超期台账条目，返回 `{"rows": 删掉几行, "disk": 清掉几份磁盘产物}`。

    - done/error 超终态时限 → 只删台账行（磁盘留着：可能仍被 specifications 引用）；
    - review_needed 超待审查时限 → 删台账行并清磁盘产物，复用
      `_cleanup_task_artifacts`（它自带「被 specifications 引用则不删」的保护）；
    - 运行中一律不碰。
    """
    ttl_terminal_s, ttl_review_s = _task_ttl_seconds()
    now = time.time() if now is None else now
    dropped = cleaned = 0
    for task in iter_tasks():
        task_id = task["task_id"]
        action = _task_disposition(task, now, ttl_terminal_s, ttl_review_s)
        if action == "keep":
            continue
        if action == "drop_row_and_disk" and delete_disk:
            try:
                _cleanup_task_artifacts(task_id)
                cleaned += 1
            except Exception as e:
                # 磁盘清理失败不得拦住删行，否则该条目会永远清不掉
                logger.warning("超期待审查任务的磁盘清理失败 task=%s: %s", task_id, e)
        delete_task(task_id)
        dropped += 1
        logger.info("导入任务台账超期清理 task=%s status=%s action=%s",
                    task_id, task.get("status"), action)
    return {"rows": dropped, "disk": cleaned}

# 向量索引分批写入的批大小：兼顾「批量语义」（勿退回逐条 add）与「写入进度可观测」
VECTOR_WRITE_BATCH = 500

# 台账超期回收的巡检间隔（秒）。不需要很密：两档时限以分钟/小时计，
# 巡检只负责「到点发现」，晚几十秒无影响。
IMPORT_SWEEP_INTERVAL_S = 300


def _sanitize_status(status: str) -> str:
    """status 白名单校验（单一来源 SPEC_STATUS_ALLOWED）：非法值回退默认「现行」。

    非法 status 直接入库会导致检索默认「仅现行」下该规范静默消失。
    """
    return status if status in SPEC_STATUS_ALLOWED else "现行"


def _sanitize_replaced_by_code(replaced_by_code: str) -> str:
    """replaced_by_code 长度上限截断"""
    return (replaced_by_code or "")[:REPLACED_BY_CODE_MAX_LEN]


def _link_replacement(conn, spec_id: int, replaced_by_code: str, status: str) -> None:
    """把校核出的「替代关系编号」落到本规范上，方向**按本规范状态推断**。

    为什么必须推断而不是照抄：同一个 replaced_by_code 字段，prompt 写「被替代的规范
    编号（废止/修订中时若有）」、导入界面标签写「被替代编号」、而本函数的历史实现拿它去
    标记**库中该编号**的规范为废止（当成「我替代掉的旧规范」）。前两者读作「替代我的
    新规范」，第三者读作「我替代的旧规范」——方向相反。AI 的输出方向也随之不稳
    （文档写「本标准代替 X」→ 填旧的；写「本标准被 X 代替」→ 填新的）。

    故按下表判定（两种 AI 输出都能落到正确方向）：

    | 本规范状态   | 编号含义             | 动作                                                     |
    |-------------|---------------------|----------------------------------------------------------|
    | 废止/修订中 | 替代**它**的新规范   | 存入 replaced_by_code；库中有该新版则把自己的外键指向它     |
    | 现行        | 被**它**替代的旧规范 | 把库中那本标为废止，外键指向本规范（历史行为，保持不变）     |
    """
    if not replaced_by_code:
        return
    norm = normalize_spec_code(replaced_by_code)
    # 排除自引用：同码重导时 code = norm 会命中刚 INSERT 的行自身
    other = conn.execute(
        "SELECT id FROM specifications WHERE code = ? AND id != ?", (norm, spec_id)
    ).fetchone()

    if status in ("废止", "修订中"):
        # 本规范被替代：留存替代者编号（即便新版尚未入库，详情页也能给出可查的编号）
        conn.execute(
            "UPDATE specifications SET replaced_by_code = ? WHERE id = ?",
            (replaced_by_code, spec_id),
        )
        if other:
            conn.execute(
                "UPDATE specifications SET replace_by_spec_id = ? WHERE id = ?",
                (other["id"], spec_id),
            )
    elif other:
        # 本规范替代了旧规范：标记旧规范废止并反向关联（新规范状态不动）
        conn.execute(
            "UPDATE specifications SET status = '废止', replace_by_spec_id = ? WHERE id = ?",
            (spec_id, other["id"]),
        )


# ═══════════════════════════════════════════
# 导入判重（编号 / 名称）
# 用户口径（2026-09-30 裁定）：以编号或名称判重；**状态不参与判定** ——
# 同一本规范不可能有两种状态，库中同码的废止版与现行版并存属历史记录，
# 不该被当成「不同规范」，也不该因此放过重复导入。
# 与 `file_hash`（上传字节 SHA256）判重**互补**：同文件是确定性重复，
# 同编号/同名称是疑似重复，两者文案与后续动作分开。
# ═══════════════════════════════════════════

# 名称判重时剥除的末尾版本括号（如 `（2015年版）`/`(2016版)`/`(2015)`）。
# 库里可能带、导入名可能不带 —— 不剥就会把同一本判成两本，判重形同虚设。
_DUP_TITLE_VERSION_RE = re.compile(r"[（(]\s*\d{4}\s*(?:年版|版)?\s*[）)]$")
# 归一化后要剥掉的末尾标点（全角空格与全角句号都在内 —— 前者不在 \s 的直观预期里，
# 后者是最常见的句末残留）
_DUP_TITLE_STRIP = " 　.,，、。;；:：!！?？-—_·…"
_DUP_TITLE_MAX_PASSES = 3


def _dup_norm_code(code: str | None) -> str:
    """编号判重归一：只保留字母数字并大写。

    为什么不能直接用 `normalize_spec_code` 的返回值比较：它**不插**前缀与序号之间的
    空格 —— `normalize_spec_code('GBT50010-2010')` 得 `GB/T50010-2010`，而库中存的是
    `GB/T 50010-2010`，直接相等比较会把同一本判成两本（判重形同虚设）。
    抹掉 '/'-空格-连字符后对齐：`GBT50010-2010` / `GB/T 50010-2010` / `gb/t 50010—2010`
    一律得到 `GBT500102010`。顺带把 GB/T 与 GBT 这类写法差异一并消掉。
    """
    return re.sub(r"[^0-9A-Z]", "", normalize_spec_code(code or "").upper())


def _normalize_title_for_dup(title: str | None) -> str:
    """名称判重归一：NFKC 折叠 → 去空白 → 剥末尾标点与版本括号。

    与解析侧（`_parse_filename_to_code_title`）**刻意不同**：那边禁用 NFKC，因为它会
    改坏要入库的名称（`Ⅲ`→`III`、`（2015年版）` 折成半角）。这里只用于**比较**、
    不写回任何字段，所以 NFKC 正合适 —— 正需要把全角/兼容字符折叠掉，
    才能让 `ＪＧＪ１０７` 与 `JGJ107` 判为同名。
    """
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = re.sub(r"\s+", "", t)
    for _ in range(_DUP_TITLE_MAX_PASSES):
        stripped = _DUP_TITLE_VERSION_RE.sub("", t).strip(_DUP_TITLE_STRIP)
        if stripped == t:
            break
        t = stripped
    return t


def _dup_text(value: object) -> str:
    """外部输入 → 去空白文本：非字符串一律视作空（防 int/list 打进 .strip() 抛 500）"""
    return value.strip() if isinstance(value, str) else ""


def find_duplicate_specs(code: str | None, title: str | None) -> list[dict]:
    """按**编号或名称**查库中已存在的规范，返回命中列表（判重用）。

    - 编号比较走 `normalize_spec_code` 同口径（入库前已归一），否则
      `GBT50010-2010` 与 `GB/T 50010-2010` 会被判成两本。
    - 名称比较走 `_normalize_title_for_dup`：SQL 做不了跨行归一，故取回内存比较。
      规范库规模天然是「一本一行」（数百量级），只取 id/code/title/status/clause_count
      五列全量比较的开销可忽略 —— 这是**有意的**全量读，不是待优化的全量加载。
    - **状态不参与判定**，但原样带出供前端展示（库里同码可能废止版与现行版并存，
      用户需要看到到底是哪一条）。
    """
    norm_code = _dup_norm_code(code)
    norm_title = _normalize_title_for_dup(title)
    if not norm_code and not norm_title:
        return []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, code, title, status, clause_count FROM specifications"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        reasons = []
        if norm_code and _dup_norm_code(r["code"]) == norm_code:
            reasons.append("code")
        if norm_title and _normalize_title_for_dup(r["title"]) == norm_title:
            reasons.append("title")
        if reasons:
            out.append({"id": r["id"], "code": r["code"], "title": r["title"],
                        "status": r["status"], "clause_count": r["clause_count"],
                        "reasons": reasons})
    return out


def _format_duplicate_rows(dups: list[dict]) -> str:
    """把命中项渲染成可读行。

    **必须转义**：code/title 来自导入表单与 OCR，属用户可控内容，直接插进 f-string
    HTML 就是存储型 XSS（全局规则 §1.1 零容忍）。
    """
    return "<br>".join(
        f"#{d['id']} {html.escape(str(d['code']))} | {html.escape(str(d['title']))}"
        f" | {html.escape(str(d['status']))} | {d['clause_count']} 条"
        for d in dups
    )


def _compute_file_hash(file_bytes: bytes) -> str:
    """计算文件的 SHA256 哈希值"""
    return hashlib.sha256(file_bytes).hexdigest()


# ═══════════════════════════════════════════
# 文件名自动识别（/import/parse-filename）的字符归一与噪声词表
# 集中在此，勿把字面量散进函数（全局规则：业务常量统一管理）
# ═══════════════════════════════════════════

# 定向全角→半角映射。**必须用 str.maketrans**：str.translate 要求键是码点整数，
# 直接传 {str: str} 字典会被静默忽略（不报错、也不生效），正好会伪装成「全角已支持」。
# 刻意**不用 unicodedata.normalize('NFKC')**：NFKC 会把 `（2015年版）` 的括号折成半角、
# `Ⅲ`→`III`、`①`→`1`，破坏规范名称本身。
_FULLWIDTH_TRANS = str.maketrans({
    **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},  # ０-９
    **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},  # Ａ-Ｚ
    **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)},  # ａ-ｚ
    "／": "/",   # ／ 全角斜杠
    "＿": "_",   # ＿ 全角下划线
    "　": " ",   # 　 全角空格
    # 破折号族 → 半角连字符。变体收不全的后果不是「不匹配」而是**静默吞年份**：
    # 年份落不进 (\d{4})?，被并进名称，code 退回 'GB 50010' —— 而 code 是全系统身份键
    # （拼 output_dir、做替代关系匹配），同一本规范会被分裂成两条记录。
    **{c: "-" for c in "-‐‑‒–—―⁃﹘﹣－"},
})

# 「文件名前缀里的格式噪声」词表：出现在编号之前，属文件流转痕迹而非规范名称。
# 注意**不收裸「扫描」**——以「扫描」开头的真实规程名会被误剥，只收「扫描件」。
_LEAD_NOISE_WORDS = (
    "扫描件", "高清", "副本", "打印版", "电子版", "完整版", "无水印", "正版", "OCR",
)
# 「名称尾部的格式噪声」词表：比前置词表**少一个「正版」**。
# 理由：`修正版`/`校正版` 都以「正版」结尾，放尾部规则会把 `…修正版` 过剥成 `…修`；
# 而「正版」出现在编号**之前**时无歧义（没有规范名称以「正版」开头）。
_TAIL_NOISE_WORDS = tuple(w for w in _LEAD_NOISE_WORDS if w != "正版")

# 前置噪声词与前置序号（`扫描件_GB…`、`1. GB…`、`2）GB…`）分成两个正则而非 `|` 交替：
# 避免交替分支的顺序歧义，也便于各自独立演进。
_LEAD_NOISE_WORD_RE = re.compile(
    r"^(?:" + "|".join(_LEAD_NOISE_WORDS) + r")[\s_\-]*", re.IGNORECASE
)
# 末尾 `(?!\d)` 让 `1.5倍…`/`2.0版…` 这类非序号前缀原样保留
_LEAD_ORDINAL_RE = re.compile(r"^(?:\s*\d{1,3}\s*[.、)）](?!\d)\s*)+")
# 尾部噪声：纯数字括号（`(1)`/`（2）`）或噪声词后缀
_TAIL_NOISE_RE = re.compile(
    r"[（(]\s*\d{1,3}\s*[）)]$|(?:" + "|".join(_TAIL_NOISE_WORDS) + r")$", re.IGNORECASE
)
# 剥离循环上限：仅防无界循环，正常 2 轮内收敛
_NOISE_STRIP_MAX_PASSES = 5


def _strip_lead_noise(name: str) -> str:
    """循环剥离文件名开头的格式噪声（噪声词、数字序号），直到稳定。"""
    for _ in range(_NOISE_STRIP_MAX_PASSES):
        stripped = _LEAD_NOISE_WORD_RE.sub("", name, 1)
        stripped = _LEAD_ORDINAL_RE.sub("", stripped, 1).lstrip()
        if stripped == name:
            return name
        name = stripped
    return name


def _strip_tail_noise(title: str) -> str:
    """循环剥离名称尾部的格式噪声（纯数字括号、噪声词后缀），直到稳定。

    循环而非单次：`混凝土结构设计规范(1)副本` 需要连剥两层。
    strip 的分隔符集合**不含括弧**——括弧一旦被当作分隔符削掉，`(1)` 的右括号会先消失，
    「纯数字括号」规则就永远匹配不到它（旧实现 `strip("（）()")` 正是这个病，还会把
    `（2015年版）` 削成悬空括号）。也**不含 `/`**：`/` 出现在名称开头是 DB13/T 这类
    误解析的判据，削掉就失去防线。
    """
    for _ in range(_NOISE_STRIP_MAX_PASSES):
        stripped = _TAIL_NOISE_RE.sub("", title).strip("- _.")
        if stripped == title:
            return title
        title = stripped
    return title


def _parse_filename_to_code_title(filename: str) -> tuple[str, str, bool]:
    """从文件名识别规范编号与名称，返回 (code, title, matched)。

    通用命名格式：{字母前缀}[/推荐代号] {标准号}[-年份] {名称}
    例如 'GB/T 50010-2010 混凝土结构设计规范.pdf'
        → code='GB/T 50010-2010', title='混凝土结构设计规范'
    不匹配通用格式时 matched=False，交由用户手动录入。

    容错分三段：**归一**（全角与破折号族 → 半角）→ **剥离**（前置噪声词/序号）→
    **清理**（尾部噪声；编号再过 normalize_spec_code）。
    """
    raw = filename.strip()
    # 剥离扩展名：用 rsplit 而非 Path——文件名含 '/'（如 GB/T 50010-2010）时，
    # '/' 会被 Path 当作路径分隔符，把 GB 误当目录吞掉。
    name = raw.rsplit(".", 1)[0].strip() if "." in raw else raw
    name = _strip_lead_noise(name.translate(_FULLWIDTH_TRANS))
    m = re.match(
        r"^([A-Za-z]{1,5})(/[A-Za-z]{1,4})?[\s\-_]*(\d{1,5}(?:\.\d+)?)[\s\-_]*(\d{4})?[\s_\-]*(.*)$",
        name,
    )
    if not m:
        return "", "", False
    prefix, slash_suffix, number, year, title = m.groups()
    prefix = prefix.upper()
    if prefix not in PREFIX_WHITELIST:
        return "", "", False
    # 裸 Q/T（团体/企业标准代号）必须带 ≥2 位序号：真实的团体/企业标准写成 T/CECS、Q/SY，
    # 裸 T/Q 本身不构成编号；而施工场景里 `T2塔楼施工方案`（栋号）、`Q1报表`（季度）是高频
    # 文件名，序号放宽到 1 位后若放行，会把它们误回填成 `T 2` / `Q 1`。
    if prefix in ("Q", "T") and slash_suffix is None and len(number) < 2:
        return "", "", False

    title = _strip_tail_noise(title.strip("- _."))
    # 名称以残留分隔符开头（如 DB13/T 被误解析成 DB+13 后名称以 /T 开头）→ 判定不匹配
    if title and title[:1] in "/-_":
        return "", "", False
    # 既无名称也无年份，信息过少，不自动填充
    if not title and not year:
        return "", "", False

    code = f"{prefix}{slash_suffix or ''} {number}" + (f"-{year}" if year else "")
    # 编号过规范化兜底：无斜杠推荐变体（GBT50353）补成 GB/T 50353；并保证
    # 「界面显示的编号 == 入库编号」（_process_import_phase2 会做同一次归一）
    return normalize_spec_code(code), title, True


@router.post("/import/parse-filename")
async def parse_filename(filename: str = Form("")):
    """根据文件名自动识别规范编号与名称（供导入表单自动回填）"""
    code, title, matched = _parse_filename_to_code_title(filename)
    return {"code": code, "title": title, "matched": matched}


@router.post("/import/check-duplicate")
async def check_duplicate(body: dict):
    """按编号/名称查库中是否已有同规范（导入弹窗判重用）。

    **纯读接口**：不落库、不埋点 —— 用户在弹窗里每敲一次编号都会被防抖调用一次，
    埋点会把 system_logs 刷爆（口径同维护宫格的纯读端点）。

    未命中返回 `{"duplicates": []}`，命中返回带 reasons 的列表；两种情形**结构一致**，
    不返回 null（全局规则 §1.2：禁止「成功返回数组、失败返回 null」）。
    """
    return {"duplicates": find_duplicate_specs(
        _dup_text(body.get("code")), _dup_text(body.get("title")))}


@router.post("/import/validate-version")
async def validate_version(request: Request, body: dict):
    """AI 校核规范版本与命名：返回 {status, replaced_by_code, corrected_code, corrected_title, ai_available}

    AI 不可用/异常/输出非 JSON → 兜底返回 status=现行、ai_available=False（不抛错）。
    corrected_code 一律再过 normalize_spec_code 兜底（D19 第一级正则）。
    埋点：AI 成功 INFO、降级兜底 WARN；code 为空不触发校验，不埋。
    """
    from app.ai.prompts import build_version_check_prompt
    from app.ai.cli_client import get_backend

    code = (body.get("code") or "").strip()
    title = (body.get("title") or "").strip()
    fallback = {
        "status": "现行", "replaced_by_code": "", "ai_available": False,
        "corrected_code": normalize_spec_code(code), "corrected_title": title,
    }
    if not code:
        return fallback

    username = getattr(request.state, "username", "")
    result = fallback
    ai_available = False
    try:
        backend = get_backend()
        if backend.is_available():
            import json as _json
            from app.config import WORKSPACE_DIR
            prompt = build_version_check_prompt(code, title)
            resp = await backend.ask(prompt, context="", system_prompt="", work_dir=WORKSPACE_DIR)
            if resp.success and resp.content.strip():
                parsed = _json.loads(resp.content)
                result = {
                    "status": parsed.get("status", "现行") if parsed.get("status") in ("现行", "废止", "修订中") else "现行",
                    "replaced_by_code": (parsed.get("replaced_by_code") or "").strip(),
                    "corrected_code": normalize_spec_code(parsed.get("corrected_code") or code),
                    "corrected_title": (parsed.get("corrected_title") or title).strip(),
                    "ai_available": True,
                }
                ai_available = True
    except Exception:
        pass
    if ai_available:
        log_action("import", "INFO", "版本校验完成",
                   detail=json_detail({"code": code, "title": title,
                                       "status": result["status"]}),
                   username=username)
    else:
        log_action("import", "WARN", "版本校验降级(AI不可用)",
                   detail=json_detail({"code": code, "title": title}),
                   username=username)
    return result


@router.post("/import/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    code: str = Form(""),
    force_ocr: bool = Form(False),
    status: str = Form("现行"),
    replaced_by_code: str = Form(""),
    dup_confirmed: str = Form(""),
):
    content = await file.read()
    file_hash = _compute_file_hash(content)

    # 检查是否已导入过相同文件
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, code, title, created_at FROM specifications WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()

    if existing:
        log_action("import", "WARN", "导入重复文件",
                   detail=json_detail({"code": existing["code"],
                                       "title": existing["title"],
                                       "created_at": existing["created_at"],
                                       "file_hash": file_hash}),
                   username=getattr(request.state, "username", ""))
        # code/title 来自导入表单与 OCR（用户可控），必须转义后再插进 f-string HTML
        # —— 这里是 HTMLResponse，不经过 Jinja 自动转义（全局规则 §1.1）
        return HTMLResponse(
            f"""<div id="import-status" style="color:#c08552;font-weight:bold">
            ⚠️ 该文件已导入过<br>
            <small>规范编号：{html.escape(str(existing['code']))} | 名称：{html.escape(str(existing['title']))}<br>
            导入时间：{html.escape(str(existing['created_at']))}</small><br>
            <a href="/specs">前往规范管理 →</a>
            </div>"""
        )

    # 疑似重复（同编号/同名称但**文件不同**）：警告 + 可确认继续。
    # 不做硬拒绝 —— 用更清晰的文件重导同一本是合法需求（用户 2026-09-30 就做过一次），
    # 硬拒绝会逼用户先删旧规范（实测删一条 887 条的规范约 10 分钟）才能重导。
    # 放在这里（file_hash 检查之后、台账登记之前）：同文件仍走上面那条更硬的
    # 文案；未确认时不创建任务、不落盘。
    # `_dup_text` 兜住非字符串：真实 HTTP 下 FastAPI 恒给 str，但端点被**直接调用**时
    # （测试里有这种用法）拿到的是 `Form()` 声明对象，直接 .strip() 会 AttributeError
    dups = find_duplicate_specs(_dup_text(code), _dup_text(title))
    if dups and _dup_text(dup_confirmed).lower() not in ("1", "true", "on", "yes"):
        log_action("import", "WARN", "导入疑似重复规范",
                   detail=json_detail({"code": code, "title": title,
                                       "matched": [d["id"] for d in dups]}),
                   username=getattr(request.state, "username", ""))
        return HTMLResponse(
            f"""<div id="import-dup-blocked" style="color:#c08552;font-weight:bold">
            ⚠️ 库中已存在同一规范（疑似重复导入）<br>
            <small>{_format_duplicate_rows(dups)}</small><br>
            <small>如确认仍要导入（例如用更清晰的文件重导），请再次点击「开始导入」。</small><br>
            <a href="/specs">前往规范管理 →</a>
            </div>"""
        )

    task_id = uuid.uuid4().hex[:8]
    # 记录属主：取消/确认仅限本人（防御越权删除他人任务）
    create_task(task_id, owner=getattr(request.state, "username", ""))

    # filename 为库声明中的可选字段（`str | None`）：经 HTTP 由 Starlette 的
    # MultiPartParser 构造时恒为 str（无 filename= 的 part 会被当成普通表单字段而
    # 非 UploadFile），但直接依赖库声明之外的形态不严谨 —— 按全局规则 1.1 校验外部输入。
    if not file.filename:
        delete_task(task_id)
        return HTMLResponse(
            '<div id="import-status" style="color:#c00;font-weight:bold">'
            "❌ 缺少文件名，无法识别文件类型</div>", status_code=400)

    ext = Path(file.filename).suffix.lower()
    save_path = Path(UPLOAD_DIR) / f"{task_id}{ext}"
    save_path.write_bytes(content)

    background_tasks.add_task(
        _process_import, task_id, str(save_path), title, code, file_hash, force_ocr,
        status, replaced_by_code,
    )
    log_action("import", "INFO", "提交导入任务",
               detail=json_detail({"task_id": task_id, "code": code, "title": title,
                                   "filename": file.filename, "status": status,
                                   "replaced_by_code": replaced_by_code}),
               username=getattr(request.state, "username", ""))
    # 任务号走响应头下发（而非让前端从 HTML 片段里正则抠）：import-tracker.js 需要它
    # 才能跨页恢复进度。非任务响应（判重拦截/即时错误）不带此头，前端据此不启动追踪。
    return HTMLResponse(
        f'<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">处理中...</div>',
        headers={"X-Import-Task-Id": task_id},
    )


@router.get("/import/progress/{task_id}")
async def get_progress(request: Request, task_id: str):
    p = _get_task(task_id) or {"status": "unknown", "progress": 0, "message": "未知任务"}
    from app.main import templates
    return templates.TemplateResponse(request, "partials/import_progress.html", {
        "task_id": task_id, "progress": p,
    })


@router.get("/import/progress/{task_id}/json")
async def get_progress_json(task_id: str):
    """进度 JSON（跨页浮标用）。形状恒定：进行中/终态/未知都返回同一组键。

    为什么另立端点而不复用 HTML 片段：浮标只要数字，HTML 得先解析才能取百分比；
    且导入弹窗仍在用 HTML 端点，两处共用同一响应会互相牵制。

    未知任务（不存在或已被超期清理）同样返回 200 而非 404 ——
    契约与同族 HTML 端点一致，客户端少一条出错分支。
    """
    p = _get_task(task_id) or {"status": "unknown", "progress": 0, "message": "未知任务"}
    return JSONResponse({
        "status": p.get("status", "unknown"),
        "progress": p.get("progress", 0),
        "message": p.get("message", ""),
        "needs_review": p.get("status") == "review_needed",
    })


def _process_import(task_id: str, file_path: str, title: str, code: str,
                    file_hash: str = "", force_ocr: bool = False,
                    status: str = "现行", replaced_by_code: str = ""):
    """后台任务 Phase 1：OCR(如需) → 暂停等待审查 → 审查后继续 Phase 2

    force_ocr=True 时强制走 OCR（适用于「半扫描」PDF：有少量文本层但格式
    会丢失，is_scanned 自动判定不可靠）。
    """
    # 入口白名单校验（status 非法回退默认「现行」；replaced_by_code 截断）
    status = _sanitize_status(status)
    replaced_by_code = _sanitize_replaced_by_code(replaced_by_code)
    conn = None
    try:
        from app.database import get_connection
        conn = get_connection()
        path = Path(file_path)
        ext = path.suffix.lower()
        _update_task(task_id, status="processing", progress=10, message="正在提取文本...")

        # Step 1: 获取 MD 文本
        if ext == ".md":
            md_text = path.read_text(encoding="utf-8")
            # 保守清洗（删除页码行/纯数字行/OCR失败标记/重复页眉），再进入 Phase 2
            md_text = clean_ocr_text(md_text)
            # MD 文件直接继续 Phase 2（同一线程内安全）
            _process_import_phase2(task_id, md_text, title, code, file_path, file_hash, status, replaced_by_code)
            return
        elif ext == ".pdf":
            if force_ocr or is_scanned(file_path):
                _update_task(task_id, progress=20, message="正在 OCR 识别...")
                from app.ocr.paddle_api import create_ocr_client
                try:
                    api = create_ocr_client()
                except RuntimeError as e:
                    _update_task(task_id, 
                        status="error", progress=0,
                        message=str(e)
                    )
                    return
                # 透传 OCR 等待/重试提示到进度 UI（如「队列繁忙，正在自动重试…」）
                if hasattr(api, "progress_cb"):
                    def _ocr_progress(msg: str, _tid: str = task_id) -> None:
                        _update_task(_tid, message=msg)
                    # 动态可选属性（仅 PaddleVLClient 支持）→ 用 setattr，
                    # 而非在 OCRClient 协议里声明成所有实现者的硬要求
                    setattr(api, "progress_cb", _ocr_progress)
                md_path = api.ocr_pdf_to_md(file_path)
                md_text = Path(md_path).read_text(encoding="utf-8")
            else:
                md_text = extract_text(file_path)
        else:
            _update_task(task_id, status="error", message=f"仅支持 .md/.pdf（当前为 {ext or '无扩展名'}）")
            return

        # 保守清洗（OCR/extract 通用）：删除页码行、纯数字行、OCR 失败标记、重复页眉
        md_text = clean_ocr_text(md_text)

        # PDF 文件：保存 OCR/extract 结果，暂停等待人工审查
        # 取用守卫：任务若已被取消/清理（cancel_review 允许取消 processing 中的任务），
        # 这里返回 False，直接收工，不再往一个不存在的槽位写
        if not _update_task(
            task_id,
            md_text=md_text, title=title, code=code, file_path=file_path,
            file_name=path.name, file_hash=file_hash,
            # 表单传入的规范状态/被替代编号（用 spec_status 键，避免与任务处理状态 status 冲突）
            spec_status=status, replaced_by_code=replaced_by_code,
            status="review_needed", progress=50,
            message="OCR 完成，请审查识别结果",
        ):
            return
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        _update_task(task_id, status="error", progress=0, message=str(e))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _copy_ocr_images(ocr_dir: str | Path, out_dir: str | Path) -> None:
    """把 OCR 阶段下载的图片从 ocr_dir/imgs/ 复制到 out_dir/imgs/

    OCR 图片下载在 OUTPUT_DIR/{task_id}/imgs/（task_id 目录），而 spec.output_dir
    是 OUTPUT_DIR/{code}/（code 非空时目录不同）。导入确认时复制图片，使条文
    content 里的相对引用 imgs/xxx.jpg 在 spec.output_dir 下成立（供条文页展示）。
    """
    import shutil

    ocr_dir = Path(ocr_dir)
    out_dir = Path(out_dir)
    if ocr_dir == out_dir:
        return  # 未填 code 时 output_dir 即 OCR 目录，无需复制
    src = ocr_dir / "imgs"
    if not src.is_dir():
        return
    shutil.copytree(src, out_dir / "imgs", dirs_exist_ok=True)


def _filter_cover_clauses(clauses: list[dict]) -> list[dict]:
    """过滤封面/出版信息页脏数据条文（命中封面特征词 ≥2 个直接丢弃）

    返回过滤后的条文列表，不 INSERT 封面脏数据（如标准首页、出版信息页）。
    """
    return [c for c in clauses if not is_cover_clause(c.get("content", ""))]


def _nearest_ancestor_id(no_to_id: dict[str, int], clause_no: str) -> int | None:
    """按**点段前缀**由长到短回溯，取首个在 `no_to_id` 中存在的祖先 id，否则 None。

    `21.4.1` → 依次试 `21.4`、`21`。为什么不能简单取「编号去掉最后一段」：
    批一删掉了「只有标题、无自身正文」的章节行（`clauses` 里没有该行），
    故最近**现存**祖先常常是编号更短的那一级（用户实测：`21.3.x` 直跳 `21.4.1`）。
    本函数返回的父级编号必是子级编号的段前缀 —— 不变量由两条用例分别复核（改本函数
    或改 INSERT 的 `parent_clause` 实参时都要跟着跑）：
    `tests/test_import.py::test_imported_parent_links_are_segment_prefixes`（导入级，
    读库里已落地的行：孤儿计数为 0 且每对父子的编号满足段前缀）与
    `tests/test_import.py::test_nearest_ancestor_id_walks_dot_segment_prefixes`（helper 级）。

    `no_to_id` 由调用方在**循环外**一次性预载（GC §5：循环内禁逐行 DB IO）。
    """
    segs = [s for s in (clause_no or "").split(".") if s]
    for k in range(len(segs) - 1, 0, -1):
        prefix = ".".join(segs[:k])
        if prefix in no_to_id:
            return no_to_id[prefix]
    return None


def _process_import_phase2(task_id: str, md_text: str, title: str, code: str,
                            file_path: str, file_hash: str = "",
                            status: str = "现行", replaced_by_code: str = ""):
    """后台任务 Phase 2：解析 → 分类 → 索引（始终创建新连接，线程安全）"""
    # 入库前白名单兜底校验（覆盖 upload_file → _process_import 与 confirm_review 两条路径）
    status = _sanitize_status(status)
    replaced_by_code = _sanitize_replaced_by_code(replaced_by_code)
    conn = None
    try:
        from app.database import get_connection
        conn = get_connection()

        # 取消守卫：任务可能在 Phase 1 / 审查等待期间被取消或超期清理。
        # 此时不该再往下做解析/分类/索引——那是在为一份已被放弃的导入干活。
        if not _update_task(task_id, progress=60, message="正在解析条文..."):
            logger.info("导入任务已取消或超时清理，跳过 Phase 2 task=%s", task_id)
            return

        # Step 2: 解析条文 + 过滤封面/出版信息页脏数据
        clauses_data = _filter_cover_clauses(parse_markdown(md_text))
        # 降级行自检（2026-09-29）：结构标题没被认出来属**静默**失效 —— 不报错、进度正常、
        # 条文数只差几条，后果只是标题被折进上一条（实测 JTG F80/1 的 `13.4.3` 因此吞掉
        # 10,923 字符、其后 B/C/D 的面包屑全被套成 13.4），用户只能翻条文才发现。
        # 此处只收集，留到 commit 后落 WARN（log_action 自开新连接，事务内调用会 BUSY）。
        degraded_rows = find_degraded_heading_lines(md_text)

        # Step 3: 规范级分类（code 先归一化，再 detect 层级/性质/行业）
        code = normalize_spec_code(code) or Path(file_path).stem
        dim1_hierarchy = detect_hierarchy(code)
        dim1_nature = detect_nature(code)
        dim1_industry = detect_industry(code)

        _update_task(task_id, progress=70, message=f"正在分类 {len(clauses_data)} 条条文...")

        # Step 4: 加载分类规则
        rules_rows = conn.execute(
            "SELECT * FROM classification_rules WHERE is_active = 1"
        ).fetchall()
        rules = [dict(r) for r in rules_rows]

        # Step 5: 规范级分类 (dim2/dim3)
        spec_classify_text = f"{code} {title}"
        spec_scores, spec_labels, spec_rule_ids = classify_clause(spec_classify_text, [], rules)
        dim2_stage = spec_labels.get("dim2", "")
        dim3_usage = spec_labels.get("dim3", "")

        # 更新规范级规则统计
        for dim in ("dim2", "dim3"):
            rule_id = spec_rule_ids.get(dim)
            if rule_id:
                conn.execute(
                    "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                    (rule_id,),
                )

        # Step 6: 写入数据库 + 分类 + 向量索引
        output_dir = str(Path(OUTPUT_DIR) / (code or Path(file_path).stem))
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 复制 OCR 图片到 spec.output_dir（图片实际下载在 task_id 目录）
        _copy_ocr_images(Path(OUTPUT_DIR) / task_id, output_dir)

        conn.execute(
            """INSERT INTO specifications (code, title, dim1_hierarchy, dim1_nature,
               dim1_industry, dim2_stage, dim3_usage, source_path, output_dir, file_hash, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code or Path(file_path).stem, title or Path(file_path).stem,
             dim1_hierarchy, dim1_nature, dim1_industry,
             dim2_stage, dim3_usage,
             file_path, output_dir, file_hash, status),
        )
        spec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 替代关系落库（方向按本规范状态推断，见 _link_replacement 文档）
        _link_replacement(conn, spec_id, replaced_by_code, status)

        classified_count = 0
        # 向量阶段的失败信息先收集、commit 后再落日志：log_action 自开新连接，
        # 在本函数未提交的事务内调用会 BUSY 并被其静默丢弃（同下方「导入成功」埋点注释）。
        vector_warn = None
        vs = None
        try:
            vs = VectorStore()
        except Exception as e:
            # 绝不静默：vs 为 None 意味着本批条文的向量一条都不会写，
            # 事后唯一症状是维护页「缺失向量索引」而无从追查原因。
            vector_warn = f"向量库不可用，本批条文未写向量索引: {e}"

        # 第一步：先插入所有条文到 SQLite，收集需要 embedding 的记录
        # 整批只取一次 AI 介入阈值（DB 覆盖热生效），避免条文循环内反复查
        from app.params.registry import get_adaptive_thresholds
        from app.search.tokenize import build_search_text
        adaptive_thresholds = get_adaptive_thresholds()
        # parent_clause 的祖先查表：本规范已有条文的 clause_no → id，**循环外一次查询**
        # （GC §5 禁循环内逐行 DB IO）；新插入的条文随即并入，供后续兄弟/子级找到。
        no_to_id = {
            r["clause_no"]: r["id"]
            for r in conn.execute(
                "SELECT id, clause_no FROM clauses WHERE spec_id = ?", (spec_id,)
            )
        }
        embedding_records = []
        for cd in clauses_data:
            scores, best_labels, best_rule_ids = classify_clause(cd["content"], cd.get("parent_path", []), rules)
            dim4_val = best_labels.get("dim4", "")
            dim5_val = best_labels.get("dim5", "")
            dim6_val = best_labels.get("dim6", "")

            # 最近**现存**祖先的 id（批一不存「只有标题的章节行」，故可能跨级）
            parent_id = _nearest_ancestor_id(no_to_id, cd["clause_no"])
            # section_path 是解析器产出的面包屑快照（原始，供展示）；
            # breadcrumb 是它的 jieba 预分词结果（供 FTS 的独立列）。
            st, bc = build_search_text(
                cd["clause_no"], cd["title"], cd["content"], cd.get("section_path", ""))
            conn.execute(
                """INSERT INTO clauses (spec_id, clause_no, title, content, parent_clause,
                   dim4_specialty, dim5_location, dim6_material, clause_is_non,
                   search_text, breadcrumb, section_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (spec_id, cd["clause_no"], cd["title"], cd["content"], parent_id,
                 dim4_val, dim5_val, dim6_val,
                 1 if cd.get("is_non_clause") else 0,
                 st, bc, cd.get("section_path", "")),
            )
            clause_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 并入查表：后续兄弟/子级条文按段前缀回溯时能找到本行
            no_to_id.setdefault(cd["clause_no"], clause_id)

            for dim in ["dim4", "dim5", "dim6"]:
                rule_id = best_rule_ids.get(dim)
                if should_use_ai(dim, scores, adaptive_thresholds):
                    # 入列自检：已删除条文/废止规范/非条文不入队；同(clause,dim)防重
                    try_enqueue(conn, clause_id, dim, scores[dim])
                    # 规则匹配到但得分不足 → 仅记录命中（统计语义与旧实现一致）
                    if rule_id:
                        conn.execute(
                            "UPDATE classification_rules SET hit_count = hit_count + 1 WHERE id = ?",
                            (rule_id,),
                        )
                elif best_labels.get(dim):
                    classified_count += 1
                    # 规则匹配到且得分达标 → 命中 + 确认
                    if rule_id:
                        conn.execute(
                            "UPDATE classification_rules SET hit_count = hit_count + 1, confirmed = confirmed + 1 WHERE id = ?",
                            (rule_id,),
                        )

            if vs is not None:
                dim_scores_str = ",".join(f"{k}={v:.2f}" for k, v in scores.items())
                # 超长条文切块 → **每块一条向量记录**（chunk_index 递增）。前缀预算的
                # 预留只在 build_embed_chunks 一处做（与重建路径共用，各算一套会分叉）。
                for ci, embed_text in enumerate(build_embed_chunks(
                        cd["content"], code=code, spec_title=title,
                        clause_no=cd["clause_no"], clause_title=cd["title"],
                        section_path=cd.get("section_path", ""))):
                    embedding_records.append({
                        "clause_id": clause_id,
                        "spec_id": spec_id,
                        "text": embed_text,
                        "dim_scores": dim_scores_str,
                        "chunk_index": ci,
                    })

        # 第二步：批量计算 embedding（比逐条快一个数量级）
        if vs is not None and embedding_records:
            try:
                from app.ai.embedding import embed_texts, get_model
                import numpy as np

                n_vec = len(embedding_records)
                # 模型首次加载约 30 秒，是本流程最长的单点停顿。若与编码合并为一步，
                # 进度条会静止半分钟——单列一档，用户才知道在等什么。
                # conn=conn：此刻 conn 仍持有条文插入的写事务，另开连接会撞写锁
                _update_task(task_id, conn=conn,
                    progress=80, message="正在加载向量模型（首次约 30 秒）…")
                if get_model() is None:
                    raise RuntimeError("Embedding 模型不可用")

                _update_task(task_id, conn=conn,
                    progress=84, message=f"正在生成向量（{n_vec} 块）…")
                texts = [r["text"] for r in embedding_records]
                embeddings = embed_texts(texts)

                records = [
                    {
                        "clause_id": r["clause_id"],
                        "spec_id": r["spec_id"],
                        "text": r["text"],
                        "embedding": np.array(embeddings[i], dtype=np.float32),
                        "dim_scores": r["dim_scores"],
                        "chunk_index": r["chunk_index"],
                    }
                    for i, r in enumerate(embedding_records)
                ]

                # 表不存在时先按显式 schema 建表，确保 embedding 列是固定大小向量类型
                if not vs._table_exists():
                    first_emb = np.array(embeddings[0], dtype=np.float32)
                    from app.search.vector_search import embedding_schema
                    vs.db.create_table("clause_embeddings",
                                       schema=embedding_schema(len(first_emb)))

                # 分批写入。两条约束同时成立：
                # 1) 不得退回逐条 add——实测逐条（且每行重开表）比批量慢 40 倍，
                #    且每次 add 产生一个新版本（真实表曾落到 rows=73 / version=173）
                # 2) 不得单次 add 全量——上万条时写入期间进度完全不动
                # 回归测试：tests/test_import_vector_batch.py
                total = len(records)
                for start in range(0, total, VECTOR_WRITE_BATCH):
                    # 长循环里的取消守卫：上万条要写多个批次，中途被取消就停手。
                    # 必须在这里守：LanceDB 写入不在 SQLite 事务内，多写的部分
                    # 不会被 rollback 回收。
                    if _get_task(task_id) is None:
                        logger.info("导入任务已取消/超时清理，中止向量写入 task=%s", task_id)
                        return
                    vs._get_table().add(records[start:start + VECTOR_WRITE_BATCH])
                    done = min(start + VECTOR_WRITE_BATCH, total)
                    _update_task(task_id, conn=conn,
                        progress=90 + int(9 * done / total),
                        message=f"正在写入向量索引（{done}/{total}）…")

                # 收尾压实：子块使行数上升 ~15-18%，版本数随之加快增长（真实表曾
                # rows=73 / version=173）。压实失败只记 WARNING——见 VectorStore.optimize，
                # 它绝不抛异常，故不会把已入库的导入判成失败。只换文案不动 progress
                # （上一档已是 99，写回固定值反而像故障）。
                _update_task(task_id, conn=conn, message="正在压实向量表…")
                vs.optimize()
            except Exception as e:
                # 不影响导入完成，但绝不静默：进度 message 随任务结束即消失，
                # 故同时收集告警，commit 后落 system_logs（否则向量缺失无从追查）。
                # 只改文案、不动 progress——此处进度可能已推进到 90+，写回固定值
                # 会造成进度回退，反而更像故障。
                _update_task(task_id, conn=conn,
                    message=f"向量索引部分失败: {str(e)}"
                )
                vector_warn = f"向量索引写入失败，部分条文缺索引: {e}"

        conn.execute("UPDATE specifications SET clause_count = ? WHERE id = ?",
                    (len(clauses_data), spec_id))
        conn.commit()
        # commit 之后再埋点（log_action 自开新连接，事务内调用会 BUSY）
        log_action("import", "INFO", "导入成功",
                   detail=json_detail({"spec_id": spec_id, "code": code,
                                       "clause_count": len(clauses_data),
                                       "replaced_by_code": replaced_by_code}),
                   username=(_get_task(task_id) or {}).get("owner", "system"))
        if vector_warn:
            # 向量阶段失败在此统一落 WARN：条文已入库但索引不全，
            # 维护页会显示「缺失向量索引」，这条日志是唯一的追查线索
            log_action("import", "WARN", "向量索引未完整写入",
                       detail=json_detail({"code": code, "reason": vector_warn,
                                           "clause_count": len(embedding_records)}),
                       username=(_get_task(task_id) or {}).get("owner", "system"))
        if degraded_rows:
            # 结构标题降级同理（见 Step 2 的收集点）：条文已入库、不影响可用性，
            # 但那些标题的正文挂在**上一条**名下 —— 这条 WARN 是用户唯一的追查线索。
            # 级别取 'WARN'（而非模块 logger 桥接写的 'WARNING'）：日志 UI 的
            # 「⚠️ 异常」筛选与告警着色只认 'WARN'，用后者会变成界面里的隐形记录。
            log_action("import", "WARN", "结构标题疑似未被识别",
                       detail=json_detail({
                           "code": code,
                           "rows": len(degraded_rows),
                           "samples": [f"L{n} {t[:60]}" for n, t, _ in degraded_rows[:5]],
                       }),
                       username=(_get_task(task_id) or {}).get("owner", "system"))

        _update_task(task_id, 
            status="done", progress=100,
            message=f"导入完成：{len(clauses_data)} 条条文已解析，{classified_count} 个维度已分类"
        )
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        # 文案必须点明「本次未完成、需重跑」：上方 rollback 只回滚 SQLite 事务，
        # LanceDB 的向量写入不在事务内（向量在 commit 前就已落表）——失败会留下
        # **常驻半成品态**（维护页随后报「缺失向量索引」），只写原始异常串会让用户
        # 以为这是一次可以忽略的偶发错误。
        # 不加「导入失败」前缀：import_progress.html 的 error 分支已渲染
        # 「导入失败: {{ message }}」（且第 14 行另有一处裸渲染），带了会重复。
        _update_task(task_id, 
            status="error", progress=0,
            message=f"本次未完成，请重跑：{e}",
        )
        log_action("import", "ERROR", "导入失败",
                   detail=json_detail({"task_id": task_id, "code": code, "error": str(e)}),
                   username=(_get_task(task_id) or {}).get("owner", "system"))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ═══════════════════════════════════════════
# OCR 审查
# ═══════════════════════════════════════════

@router.get("/import/review/{task_id}")
async def review_page(request: Request, task_id: str):
    """OCR 审查页"""
    task = _get_task(task_id)
    if not task or task.get("status") != "review_needed":
        from app.main import templates
        return templates.TemplateResponse(request, "base.html", {
            "left_content": "partials/tree_panel.html",
            "center_content": "partials/welcome.html",
        })

    from app.main import templates
    return templates.TemplateResponse(request, "base.html", {
        "left_content": "partials/tree_panel.html",
        "center_content": "partials/ocr_review.html",
        "task_id": task_id,
        # 审查页隐藏左侧分类树，页面两栏全宽显示（编辑 | 预览）
        "hide_tree": True,
    })


@router.get("/import/review/{task_id}/content")
async def review_content(request: Request, task_id: str):
    """获取 OCR 原始文本"""
    task = _get_task(task_id)
    if not task or "md_text" not in task:
        return JSONResponse({"detail": "任务不存在或已过期"}, status_code=404)
    return {"content": task["md_text"], "file_name": task.get("file_name", "")}


@router.get("/import/review/{task_id}/imgs/{filename}")
async def review_image(request: Request, task_id: str, filename: str):
    """服务 OCR 审查页 markdown 中引用的图片

    OCR 阶段已把官网 markdown.images 的图片按相对路径 imgs/xxx.jpg 保存到
    OUTPUT_DIR/{task_id}/imgs/；审查页前端把相对引用改写为
    /import/review/{task_id}/imgs/{filename} 后由本路由返回文件。
    """
    from fastapi.responses import FileResponse

    # task_id 为 uuid4().hex[:8]（8 位十六进制），校验防止目录拼接越权
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)
    # 仅取文件名，防路径穿越
    safe_name = Path(filename).name
    img_path = Path(OUTPUT_DIR) / task_id / "imgs" / safe_name
    if not img_path.is_file():
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    return FileResponse(str(img_path))


@router.post("/import/review/{task_id}/confirm")
async def confirm_review(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
):
    """审查确认：提交修改后的 Markdown 文本（raw body），继续 Phase 2

    **md 走 raw body，不走表单字段**：Starlette 对表单字段有 1MB 硬上限
    （`MultiPartParser.max_part_size`，urlencoded 同样受管），超限时在**路由体执行
    之前**就抛 400，导致既无确认日志也无成功/失败日志、Phase 2 从未启动；而 HTMX
    对 4xx 默认不 swap，页面毫无反应。实测 CJJ 2-2008 的 md 46 万字符编码后 1.52MB
    即触发（JGJ 107-2016 的 5.7 万字符则正常），是「大规范根本导不进来」的堵点。
    raw body 无此限制。回归测试见 tests/test_confirm_transport.py。
    """
    task = _get_task(task_id)
    if not task:
        return HTMLResponse("<p style='color:red'>任务不存在或已过期</p>")

    # 防止重复点击确认按钮
    if task.get("status") in ("processing", "done"):
        return HTMLResponse(
            f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
            <p style="color:#c08552">⏳ 导入正在处理中，请勿重复提交...</p></div>"""
        )

    # 读取 raw body 并按 UTF-8 解码（外部输入校验：编码非法/内容为空一律拒绝，
    # 且必须在改动任务状态之前返回，避免"没提交内容却已置为 processing"）
    try:
        content = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        return HTMLResponse(
            "<p style='color:red'>审查内容编码非法（需 UTF-8），未提交</p>",
            status_code=400,
        )
    if not content.strip():
        return HTMLResponse(
            "<p style='color:red'>审查内容为空，未提交</p>", status_code=400
        )

    title = task.get("title", "")
    code = task.get("code", "")
    file_path = task.get("file_path", "")
    file_hash = task.get("file_hash", "")
    status = task.get("spec_status", "现行")
    replaced_by_code = task.get("replaced_by_code", "")

    # 更新为审查后的文本（落库：`_get_task` 返回的是行副本，就地赋值不会持久化）
    _update_task(task_id, md_text=content, status="processing", progress=55,
                 message="审查完成，正在继续导入...")

    # 启动 Phase 2（不再跨线程传递 conn，Phase 2 自己创建连接）
    background_tasks.add_task(
        _process_import_phase2, task_id, content, title, code, file_path, file_hash,
        status, replaced_by_code,
    )
    log_action("import", "INFO", "审查确认继续导入",
               detail=json_detail({"task_id": task_id, "code": code}),
               username=task.get("owner") or getattr(request.state, "username", ""))

    return HTMLResponse(
        f"""<div id="import-status" hx-get="/import/progress/{task_id}" hx-trigger="every 2s" hx-swap="outerHTML">
        <p>审查完成，正在继续导入...</p></div>"""
    )


@router.post("/import/review/{task_id}/cancel")
async def cancel_review(request: Request, task_id: str):
    """取消审查：清理磁盘残留并移除任务，返回主界面

    清理 uploads/{task_id}.{ext}（原始上传文件）与 outputs/{task_id}/（OCR 结果
    目录，含 imgs/ 已下载图片）。任务不存在时仍执行磁盘清理（幂等兜底：台账条目
    可能已被超期清理，而磁盘残留仍在）；但被 specifications
    引用的路径一律不删（防误删已入库规范数据）。
    """
    # 校验 task_id 为 uuid4().hex[:8] 格式，防止目录拼接越权
    if not re.fullmatch(r"[0-9a-f]{8}", task_id):
        return JSONResponse({"detail": "任务ID非法"}, status_code=404)

    task = _get_task(task_id)
    username = getattr(request.state, "username", "")

    if task is not None:
        # 属主校验：仅任务属主可取消（旧任务无 owner 字段视为可取消，兼容历史）
        if task.get("owner") and task.get("owner") != username:
            return JSONResponse({"detail": "无权取消他人任务"}, status_code=403)
        # 已完成导入不可取消（避免删除已入库规范的源文件/输出目录）
        if task.get("status") == "done":
            return JSONResponse({"detail": "导入已完成，无法取消"}, status_code=409)

    _cleanup_task_artifacts(task_id)

    # 移除台账条目（幂等：不存在也无妨）
    delete_task(task_id)
    log_action("import", "INFO", "取消导入审查",
               detail=json_detail({"task_id": task_id}),
               username=username)

    # HX-Redirect 让 HTMX 整页跳回主界面
    return HTMLResponse("", headers={"HX-Redirect": "/"})


def _cleanup_task_artifacts(task_id: str) -> None:
    """清理任务残留（uploads/{task_id}.* 与 outputs/{task_id}/）

    被 specifications 引用的路径不删：output_dir 或 source_path 指向该路径时，
    说明是已入库规范的数据，跳过删除（防御与持久目录的命名空间冲突）。
    """
    import shutil

    with get_db() as conn:
        ref_outputs = {str(r["output_dir"]).replace("\\", "/") for r in conn.execute(
            "SELECT output_dir FROM specifications WHERE output_dir IS NOT NULL")}
        ref_uploads = {Path(r["source_path"]).name for r in conn.execute(
            "SELECT source_path FROM specifications WHERE source_path IS NOT NULL")}

    # 清理 OCR 结果目录 outputs/{task_id}/
    out_dir = Path(OUTPUT_DIR) / task_id
    key = str(out_dir).replace("\\", "/")
    if out_dir.is_dir() and key not in ref_outputs:
        shutil.rmtree(out_dir, ignore_errors=True)

    # 清理上传文件 uploads/{task_id}.{ext}
    for p in Path(UPLOAD_DIR).glob(f"{task_id}.*"):
        if p.name in ref_uploads:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/tree/all")
async def get_tree(request: Request):
    """返回分类树数据"""
    # dim1/2/3 在 specifications 表，dim4/5/6 在 clauses 表
    spec_dims = [
        {"key": "dim1_hierarchy", "label": "规范层级", "field": "dim1_hierarchy"},
        {"key": "dim1_industry", "label": "规范行业", "field": "dim1_industry"},
        {"key": "dim1_nature", "label": "规范性质", "field": "dim1_nature"},
        {"key": "dim2_stage", "label": "工程阶段", "field": "dim2_stage"},
        {"key": "dim3_usage", "label": "工程用途", "field": "dim3_usage"},
    ]
    clause_dims = [
        {"key": "dim4_specialty", "label": "所属专业", "field": "dim4_specialty"},
        {"key": "dim5_location", "label": "工程部位", "field": "dim5_location"},
        {"key": "dim6_material", "label": "材料/工艺", "field": "dim6_material"},
    ]
    result = []

    with get_db() as conn:
        for dim in spec_dims:
            rows = conn.execute(
                f"SELECT {dim['field']}, COUNT(*) as cnt FROM specifications "
                f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
            ).fetchall()
            nodes = _build_tree_nodes(rows, dim["field"])
            result.append({"key": dim["key"], "label": dim["label"], "nodes": nodes})

    with get_db() as conn:
        for dim in clause_dims:
            rows = conn.execute(
                f"SELECT {dim['field']}, COUNT(*) as cnt FROM clauses "
                f"WHERE {dim['field']} IS NOT NULL AND {dim['field']} != '' "
                f"GROUP BY {dim['field']} ORDER BY cnt DESC LIMIT 30"
            ).fetchall()
            nodes = _build_tree_nodes(rows, dim["field"])
            result.append({"key": dim["key"], "label": dim["label"], "nodes": nodes})
    return result


def _build_tree_nodes(rows, field: str) -> list[dict]:
    """将查询结果转为树节点（处理逗号分隔的多值字段）"""
    nodes = []
    for r in rows:
        val = r[field]
        if val and "," in val:
            for sub in val.split(","):
                sub = sub.strip()
                if sub:
                    nodes.append({"label": sub, "value": sub, "count": r["cnt"], "children": []})
        else:
            nodes.append({"label": val or "(未分类)", "value": val, "count": r["cnt"], "children": []})
    return nodes
