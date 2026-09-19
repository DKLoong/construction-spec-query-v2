# rule-pending 规则级（clause 可空）实施报告

> 增量：规则级 pending 完整闭环（存量 confirmed=0 碎片规则进词面校核/黑名单，可逐条裁决并联动停用）+ Tab3 改名。
> 基线 HEAD=d11ec72 → 交付 commit **c876754**。

## 一、目标与实现摘要

| # | 目标 | 实现 | 状态 |
|---|------|------|------|
| 1 | schema：`rule_pending.clause_id` 可空 + 部分唯一索引 | `app/database.py` SCHEMA_SQL 去 NOT NULL，新增 `idx_rule_pending_rule_key ON (dimension,pattern,label) WHERE clause_id IS NULL AND status='pending'` | ✅ |
| 2 | init_db 迁移（NOT NULL → 可空重建） | `_migrate_rule_pending_clause_nullable`：PRAGMA 检测 → DROP 旧索引名 → RENAME → 建新表 → 拷全部列 → DROP 旧表 → 重建四索引；幂等（notnull=0 跳过） | ✅ |
| 3 | store 层 | `insert_pending(clause_id: int\|None)` 规则级分支；`_backfill_by_status` 加 `clause_id IS NOT NULL`；新 `deactivate_fragment` / `approve_rule` | ✅ |
| 4 | routes 换 4 处 approve → `approve_rule`；3 处 reject → `deactivate_fragment` | `rules_routes.py` word/clause/inline + 黑名单 approve；word reject/clause reject/inline removed 联动停用 | ✅ |
| 5 | Tab3 改名 | `review_tabs.html` 按钮「🚫 黑名单」→「🚫 黑名单管理」 | ✅ |
| 6 | 迁移脚本 | `scripts/migrate_pending_fragments.py`（幂等，`run_migration(conn)` 可 import） | ✅ |
| 7 | 测试 | `test_rule_pending.py` +8、新 `test_rule_pending_migrate.py` +3 | ✅ |

## 二、关键语义

- **规则级 pending**：`clause_id IS NULL`，无来源条文。裁决单元仍四元组，但规则级行只参与键级词面校核（Tab2）与黑名单（Tab3），不写列、不清 queue（`backfill` 过滤 NULL）。
- **同键唯一**：条文行唯一保留原 `idx_rule_pending_uniq`（全键）；规则级 pending 用部分唯一索引 `WHERE clause_id IS NULL AND status='pending'`，并发由 `insert_pending` 捕获 `IntegrityError` 兜底。
- **驳回联动停用**：`deactivate_fragment` 只停用 `confirmed=0 AND is_active=1` 的碎片（未人工背书）；`confirmed>0` 已确认规则不受驳回影响。`(label IS NULL OR label=?)` 与 `key_state` 语义一致。
- **批准复活**：`approve_rule` 先 `bump_rule(is_confirmed=True, new_rule_active=True, label=label, sub_field=按维映射)`，再 `UPDATE ... SET is_active=1 WHERE confirmed>0 AND is_active=0`，令被停用碎片在人工批准后复活为启用 confirmed 规则。

## 三、迁移（dev 库）

- schema 迁移由 `init_db()` 在服务重启后自动执行（`_migrate_rule_pending_clause_nullable`），本次**未手动跑 dev 库迁移**，留待 controller 统一在服务重启后完成。
- 存量碎片导入由 `scripts/migrate_pending_fragments.py` 独立幂等运行：`D:/Python/python.exe scripts/migrate_pending_fragments.py`。不改任何 `classification_rules` 状态。

## 四、测试结论

- 定向：`test_rule_pending.py` + `test_rule_pending_migrate.py` + `test_database.py` → 66 passed。
- 回归：`test_review_batch.py` + `test_rules_routes.py` + `test_rule_sink.py` + `test_rule_label.py` + `test_rule_feedback_loop.py` → 34 passed。
- 全量：`tests/ -q` → **693 passed**（基线 682，+11 新增用例）。

## 五、风险与说明

- `approve_rule` 把 `sub_field` 映射（dim4→specialty / dim5→location / dim6→material）下沉进 `rule_pending.py`（原在 routes 的 `_DIM_SUB_FIELD`），routes 侧常量与 `bump_rule` 导入已清理，行为与改前一致（新规则仍回填 sub_field）。
- `review_blacklist.html` 空态文案「🎉 黑名单为空」未随 Tab3 改名（任务仅要求按钮/标题），如后续统一可一并改「黑名单管理」。
- 部分唯一索引仅约束 `status='pending'` 的规则级行，非 pending 规则级行同键可并存——与 `insert_pending` 的应用层「任一状态行已存在则不插」双重兜底，语义与条文行一致（状态原地迁移不新增行）。
