"""AI 问答会话持久化层。

职责边界：
- 本模块只管**会话内容的读写**（表 qa_sessions / qa_messages）
- 请求级埋点（表 qa_request_logs）由 qa_routes._emit_trace 负责，两者互不干涉

约定：
- 所有 SQL 一律参数化（开发铁律 1.1）
- 并发安全依赖 `get_db()` 的 `sqlite3.connect(..., timeout=30)` 把写入串行化；
  有测试守护该不变量（`test_concurrent_appends_same_session_do_not_lose_or_mix`）
- 删除会话时**仍应用层显式删消息**，但理由与首版计划所述不同：
  本项目在 `get_connection()` 里开着 `PRAGMA foreign_keys=ON`（`app/database.py:205`，
  `get_db()` 只是其调用方），**级联删除实际是生效的**。显式删除保留为防御性写法——
  它让行为不依赖那条 PRAGMA，且在更早的 SQLite 版本或将来关掉外键时仍然正确。
- 级联的保护由**两侧**测试合起来构成（T4 已落前提侧）：
  前提侧 = `tests/test_qa_sessions.py::test_qa_foreign_keys_state_is_documented`（断言 PRAGMA 为 ON）
  声明侧 = 本 Task 的 `test_qa_messages_cascade_declaration_present`（断言 schema 里的 ON DELETE CASCADE）
  **只删一侧的任一侧都会让级联静默失效**，故两测缺一不可
- 单会话消息量小（几十条），遍历取用可接受；禁止在循环内发起查询
"""
import json
import logging
from datetime import datetime

from app.database import get_db

logger = logging.getLogger(__name__)

# 会话标题取自首轮问题，截断长度（业务常量集中管理，禁止散落魔法数字）
TITLE_MAX_CHARS = 20

# 标题兜底名（问题为全空白时）
_FALLBACK_TITLE = "新会话"

# 跨会话搜索返回上限
_SEARCH_LIMIT = 100

# 时间戳格式：本地时间 + 微秒，字典序即时间序（写入与比较都靠它）
_TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _now_ts() -> str:
    """当前本地时间戳（微秒精度），如 `2026-09-23 13:32:33.123456`。

    为什么不用 SQL 的 `datetime('now','localtime')`：它**只到秒**，而
    `updated_at` 是会话列表的排序键（`ORDER BY updated_at DESC, id DESC`）。
    同一秒内「新建会话」与「追加消息」会拿到同一个时间串，排序退化为
    按 id 降序 —— 刚追加过消息的旧会话反被新建的空会话压到后面，
    「按最近活跃排序」名存实亡（`test_list_sessions_orders_by_recent_activity`
    实测复现：期望 A(id=1) 居首，实际拿到 B(id=2)）。
    微秒精度下两次写入落到同一时刻的概率可忽略，排序恢复确定性。

    注意：本模块写出的**每一个**时间戳都走本函数，包括
    `qa_sessions.created_at/updated_at` 与 `qa_messages.created_at`——
    DDL 的列默认值只到秒，一旦漏走本函数就会在列内混进秒精度字符串，
    与微秒字符串比较时同秒内的先后会被判错。新增写时间戳的代码路径时，
    请一并走 `_now_ts()`。
    """
    return datetime.now().strftime(_TS_FORMAT)


def derive_title(question: str) -> str:
    """会话默认标题 = 首轮问题截断。

    设计取舍：不做 AI 摘要（多余一次 API 调用、多一个失败点），
    用户自己的话反而是最有效的记忆锚点。
    """
    text = (question or "").strip()
    if not text:
        return _FALLBACK_TITLE
    return text[:TITLE_MAX_CHARS]


def create_session(title: str) -> int:
    """新建会话，返回其 id。

    显式写入 created_at / updated_at（而非依赖列默认值）：列默认值只到秒，
    与 `_now_ts()` 的微秒格式不一致，混用会让 updated_at 的排序出现
    「同秒内新建反而更早」的歧义（见 `_now_ts` 说明）。
    """
    ts = _now_ts()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO qa_sessions (title, created_at, updated_at) VALUES (?,?,?)",
            (title, ts, ts),
        )
        # INSERT 成功后 lastrowid 恒非 None；出口断言守住该不变量，同时便于类型检查窄化
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_session(session_id: int) -> dict | None:
    """按 id 取会话元信息；不存在返回 None。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM qa_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "title": row[1],
            "created_at": row[2], "updated_at": row[3]}


def list_sessions() -> list[dict]:
    """会话列表，按最近活跃倒序；带消息数。

    用 LEFT JOIN + GROUP BY 一次算出全部计数，避免逐行的关联子查询（N+1）——
    开发铁律 1.3「循环内部禁止执行数据库查询」在 SQL 层的等价约束。
    LEFT JOIN 保证零消息的会话仍出现在列表里（COUNT(m.id) 为 0）。
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.title, s.updated_at, COUNT(m.id)
               FROM qa_sessions s
               LEFT JOIN qa_messages m ON m.session_id = s.id
               GROUP BY s.id, s.title, s.updated_at
               ORDER BY s.updated_at DESC, s.id DESC"""
        ).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "msg_count": r[3]}
            for r in rows]


def get_messages(session_id: int) -> list[dict]:
    """取会话全部消息（时间升序），sources/confusable 反序列化为 list。

    前端 messages 数组的字段结构与本函数返回一一对应，可直接映射。
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, role, content, sources_json, confusable_json,
                      filters_json, mode, created_at
               FROM qa_messages WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0], "role": r[1], "content": r[2],
            "sources": _loads_list(r[3]),
            "confusable": _loads_list(r[4]),
            "filters": _loads_dict(r[5]),
            "mode": r[6], "created_at": r[7],
        })
    return out


def _loads_list(raw) -> list:
    """容错反序列化 JSON 数组；脏数据退化为空列表，不抛异常中断整条会话。"""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning("会话消息 JSON 解析失败，按空处理: %s", e)
        return []
    return val if isinstance(val, list) else []


def _loads_dict(raw) -> dict:
    """容错反序列化 JSON 对象（当轮筛选）；脏数据退化为空 dict。"""
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning("会话消息筛选 JSON 解析失败，按空处理: %s", e)
        return {}
    return val if isinstance(val, dict) else {}


def append_message(session_id: int, role: str, content: str,
                   sources: list | None = None, confusable: list | None = None,
                   mode: str = "rag",
                   filters: dict | None = None) -> int:
    """追加一条消息，并刷新所属会话的 updated_at。

    filters 记录**当轮实际生效的筛选**（D5）：回看历史时据此还原
    「这条答案是在什么筛选下产生的」——筛选不入库则该信息不可逆丢失。
    **位置约定**：filters 只能追加在 mode 之后（尾巴），不得插到 confusable
    与 mode 之间——否则按位置调用的老写法 `append_message(sid, role, text,
    sources, confusable, mode)` 会把 mode 字符串静默绑进 filters（
    `json.dumps("text")` 得到合法 JSON，`_loads_dict` 再折成 {}，错得不响）。

    created_at 与 updated_at 用**同一个** `_now_ts()` 时间戳显式写入，
    与 create_session 保持同一格式（见 `_now_ts`）。

    失败消息不入库由调用方保证（见 qa_routes），本层不做判断。
    """
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    confusable_json = json.dumps(confusable or [], ensure_ascii=False)
    filters_json = json.dumps(filters or {}, ensure_ascii=False)
    ts = _now_ts()
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO qa_messages
               (session_id, role, content, sources_json, confusable_json,
                filters_json, mode, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, role, content, sources_json, confusable_json,
             filters_json, mode, ts),
        )
        conn.execute(
            "UPDATE qa_sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        # INSERT 成功后 lastrowid 恒非 None；出口断言守住该不变量，同时便于类型检查窄化
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def touch_session(session_id: int) -> None:
    """仅刷新 updated_at（用于无新消息但要提升排序的场景）。"""
    with get_db() as conn:
        conn.execute(
            "UPDATE qa_sessions SET updated_at = ? WHERE id = ?",
            (_now_ts(), session_id),
        )


def rename_session(session_id: int, title: str) -> bool:
    """重命名；会话不存在返回 False。"""
    clean = (title or "").strip()
    if not clean:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE qa_sessions SET title = ? WHERE id = ?", (clean, session_id)
        )
        return cur.rowcount > 0


def delete_session(session_id: int) -> bool:
    """删除会话及其全部消息。

    显式删消息是**防御性写法**：本项目 get_db() 开着 PRAGMA foreign_keys=ON
    （app/database.py:205），级联删除实际生效；但显式删除让行为不依赖那条 PRAGMA。
    """
    with get_db() as conn:
        cur = conn.execute("DELETE FROM qa_sessions WHERE id = ?", (session_id,))
        if cur.rowcount == 0:
            return False
        conn.execute("DELETE FROM qa_messages WHERE session_id = ?", (session_id,))
        return True


def search_messages(keyword: str, limit: int = _SEARCH_LIMIT) -> list[dict]:
    """跨会话搜索消息内容（LIKE，参数化 + 通配符转义）。

    为什么不上 FTS：qa_messages 是小表（个人/团队量级），全表扫足够；
    且中文子串匹配对「找出我说过的那句话」比分词更精确。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    pattern = f"%{_escape_like(kw)}%"
    with get_db() as conn:
        rows = conn.execute(
            """SELECT m.id, m.session_id, s.title, m.role, m.content
               FROM qa_messages m JOIN qa_sessions s ON s.id = m.session_id
               WHERE m.content LIKE ? ESCAPE '\\'
               ORDER BY m.session_id DESC, m.id DESC
               LIMIT ?""",
            (pattern, max(1, min(limit, _SEARCH_LIMIT))),
        ).fetchall()
    return [{"id": r[0], "session_id": r[1], "session_title": r[2],
             "role": r[3], "content": r[4]} for r in rows]


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，防止用户输入的 % / _ 退化为全表命中。"""
    return (text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_"))


def build_markdown(sess: dict, messages: list[dict]) -> str:
    """把会话渲染为 Markdown（导出用）。

    只做 Markdown 不做 HTML：项目已有完整 md 渲染管线
    （marked + DOMPurify + KaTeX），导出的 md 可直接丢回系统渲染。
    """
    lines = [f"# {sess.get('title') or '未命名会话'}", ""]
    if sess.get("created_at"):
        lines += [f"- 创建时间：{sess['created_at']}"]
    if sess.get("updated_at"):
        lines += [f"- 最后活跃：{sess['updated_at']}"]
    lines.append("")

    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            lines += ["## 问", "", content, ""]
        else:
            lines += ["## 答", "", content, ""]
            sources = m.get("sources") or []
            if sources:
                # 参考条文格式为「规范号 空格 条号」（如 `GB 50204 8.2.1`），
                # 由 tests::test_build_markdown_contains_title_and_turns 钉住——
                # brief 的测试用例即为接口契约，故此处不得改成带《》的写法。
                refs = "、".join(
                    f"{s.get('code', '')} {s.get('clause_no', '')}"
                    for s in sources if isinstance(s, dict)
                )
                if refs:
                    lines += [f"> 参考条文：{refs}", ""]
    return "\n".join(lines)
