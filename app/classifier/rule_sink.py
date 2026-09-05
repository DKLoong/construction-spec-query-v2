"""规则命中沉淀（人工确认与 AI 自动采纳共用，避免重复逻辑）

bump_rule 统一维护 classification_rules 的 hit_count/confirmed，并按「长期正确率」
自动启停——不是单条置信度（单条可能恰好命中，规则质量看积累）。
自动启停/新规则阈值均从参数注册表读 DB 覆盖（rule_sink / rules_routes 报表共用同一来源）。
"""
from app.params.registry import get_param_float, get_param_int
from app.termdict import is_valid_label


def bump_rule(conn, dimension: str, pattern: str, sub_field: str = "",
              is_confirmed: bool = False, new_rule_active: bool = False,
              label: str | None = None) -> None:
    """规则命中沉淀：存在则 hit++（人工确认时 confirmed++）并自动启停；不存在则新建。

    - is_confirmed：人工确认来源为 True（confirmed+1）；auto_adopted 自动采纳为 False
      （仅 hit++，避免 AI 未人工确认虚增正确率）
    - new_rule_active：新生成规则初始是否启用（auto_adopted 或人工确认 conf≥阈值 传 True）
    - label：规则赋值标签（命中后写入分类列，区别于匹配词 pattern）。历史规则 label 为
      NULL（旧 pattern 当标签语义）时，本次带 label 沉淀会回填纠正
    """
    enable_ratio = get_param_float("classify.rule_auto_enable_ratio")
    enable_min_hit = get_param_int("classify.rule_auto_enable_min_hit")
    disable_ratio = get_param_float("classify.rule_disable_ratio")
    disable_min_hit = get_param_int("classify.rule_disable_min_hit")
    new_threshold = get_param_float("classify.new_rule_threshold")
    row = conn.execute(
        "SELECT id, hit_count, confirmed, is_active, label FROM classification_rules "
        "WHERE dimension = ? AND pattern = ?",
        (dimension, pattern),
    ).fetchone()

    if row:
        new_hit = row["hit_count"] + 1
        new_conf = row["confirmed"] + (1 if is_confirmed else 0)
        conn.execute(
            "UPDATE classification_rules SET hit_count = ?, confirmed = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (new_hit, new_conf, row["id"]),
        )
        # 历史规则 label 为空且本次带 label → 回填（纠正「匹配词被当标签」的旧语义）
        if not row["label"] and label:
            conn.execute(
                "UPDATE classification_rules SET label = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (label, row["id"]),
            )
        # 自动启停：按长期正确率
        ratio = (new_conf * 1.0 / new_hit) if new_hit else 0.0
        if ratio >= enable_ratio and new_hit >= enable_min_hit:
            if not row["is_active"]:
                conn.execute(
                    "UPDATE classification_rules SET is_active = 1, updated_at = datetime('now','localtime') WHERE id = ?",
                    (row["id"],),
                )
        elif new_conf > 0 and ratio < disable_ratio and new_hit > disable_min_hit:
            if row["is_active"]:
                conn.execute(
                    "UPDATE classification_rules SET is_active = 0, updated_at = datetime('now','localtime') WHERE id = ?",
                    (row["id"],),
                )
    else:
        # 闸门③：label 非空但非权威 → 新规则强制停用（碎片不自动启用）；
        # 人工确认路径(label 先入词典,Task6)不受影响。dim2/3/1 恒放行。
        active = 1 if new_rule_active else 0
        if label and not is_valid_label(dimension, label):
            active = 0
        conn.execute(
            """INSERT INTO classification_rules
               (dimension, sub_field, pattern, match_type, priority, threshold,
                hit_count, confirmed, is_active, label)
               VALUES (?, ?, ?, 'keyword', 0, ?, ?, ?, ?, ?)""",
            (dimension, sub_field, pattern,
             new_threshold,
             1,  # 本次 bump 即该规则首次命中
             1 if is_confirmed else 0,  # 人工确认来源首次即记 confirmed
             active,
             label),
        )
