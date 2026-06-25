# 检索模块设计文档

## 概述

为施工规范查询系统实现混合检索功能：FTS5 全文搜索 + LanceDB 向量语义搜索并行执行，结果合并去重后返回。

## 范围

### 包含

| 端点 | 方法 | 功能 |
|------|------|------|
| `/search` | GET | 混合搜索：关键词 + 六维筛选 + 分页，返回 HTML 片段 |
| `/clause/{id}` | GET | 条文详情，返回 HTML 片段 |

### 不包含

- `/qa/ask` AI 问答端点（后续迭代）
- 向量索引重建脚本（当前导入流程已自动索引，后续按需添加）

## 架构

```
浏览器 (tree.js / search.js / result_list.html)
  ↓ GET /search?keyword=xx&dim4_specialty=yy&page=1
app/routes/search_routes.py          ← 新建
  ↓ SearchQuery 模型
app/search/hybrid_search.py          ← 新建（编排层）
  ├─ app/search/sql_search.py        ← 已有（FTS5）
  └─ app/search/vector_search.py     ← 已有（LanceDB）
       ↓ 合并去重
  HTML 片段 (result_list.html)
```

## 混合搜索策略

### 合并规则

1. 有关键词时：FTS5 和向量搜索并行执行
2. 无关键词时（仅维度筛选）：只用 FTS5（向量搜索需要查询文本）
3. 合并：FTS5 结果排前面，向量结果去重后补充在后面
4. 去重依据：`clause_id`（FTS5 已命中者不再从向量结果追加）
5. 维度筛选：在 FTS5 层通过 SQL LIKE 实现；向量结果追加上来后也应用维度筛选

### 分页

- 合并后的总结果集进行分页
- `page` 和 `per_page`（默认 20）参数控制

### 降级处理

- 向量模型不可用时：仅 FTS5 搜索，不报错
- LanceDB 表不存在时：仅 FTS5 搜索，不报错
- 两者都不可用时：返回空结果 + 提示信息

## 数据流

```
用户输入: keyword="混凝土", dim5_location="屋面"

1. FTS5 搜索
   → SELECT FROM clauses JOIN specs
     WHERE clauses_fts MATCH '混凝土'
     AND c.dim5_location LIKE '%屋面%'
   → 返回 N 条

2. 向量搜索（并行）
   → embed_texts(["混凝土 屋面"])
   → VectorStore.search("混凝土 屋面", top_k=50)
   → 返回 M 条（带 _distance 分数）

3. 合并
   → FTS5 的 N 条直接入结果集（记录 clause_id）
   → 向量 M 条中，跳过已在结果集中的 clause_id
   → 剩余追加到末尾
   → 总计 T 条

4. 分页切片 → 返回 HTML
```

## 需要修改/新建的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/search/hybrid_search.py` | 新建 | 混合搜索编排：并行调用 FTS5 + 向量，合并去重分页 |
| `app/routes/search_routes.py` | 新建 | `/search` 和 `/clause/{id}` 端点 |
| `app/templates/partials/clause_detail.html` | 新建 | 条文详情模板 |
| `app/main.py` | 修改 | 注册 search_router |
| `tests/test_search_routes.py` | 新建 | HTTP 层集成测试 |

### 不修改的文件

- `app/search/sql_search.py` — 现有接口够用
- `app/search/vector_search.py` — 现有接口够用
- `static/components/search.js` — 接口不变
- `static/components/tree.js` — 接口不变
- `app/templates/partials/result_list.html` — 接口不变（可能微调空结果提示）

## 测试计划

### test_search_routes.py（~12 个用例）

| 测试 | 说明 |
|------|------|
| `test_search_keyword` | 关键词搜索返回 200 + HTML |
| `test_search_dimension_filter` | 维度筛选返回正确结果 |
| `test_search_combined` | 关键词 + 维度组合 |
| `test_search_pagination` | 分页参数生效 |
| `test_search_empty_keyword` | 无关键词 + 无筛选 → 返回提示 |
| `test_search_no_results` | 无匹配结果 → 显示空结果提示 |
| `test_search_semantic` | 语义搜索返回结果（同义词场景） |
| `test_search_hybrid_merge` | 混合结果中 FTS5 命中正确去重 |
| `test_search_requires_auth` | 未登录重定向 302 |
| `test_clause_detail` | 条文详情返回 200 |
| `test_clause_detail_not_found` | 不存在的条文返回 404 |
| `test_clause_detail_requires_auth` | 未登录重定向 302 |

### 现有测试

- `tests/test_search.py` 的 5 个测试继续通过（测试 `search_clauses()` 库函数，不受影响）
- 全量 80 个测试保持绿色

## 边界条件

- 关键词含特殊字符（FTS5 语法字符如 `*`, `"`, `(`）：做转义处理
- 空关键词 + 无筛选：返回提示"请输入关键词或选择筛选条件"
- per_page 过大（>100）：限制最大值
- 向量搜索延迟：BGE 模型嵌入约 50-100ms，LanceDB 搜索 <10ms，总体可接受

## 验证

```bash
# 运行所有测试
D:/Python/python.exe -m pytest tests/ -v

# 手动测试
# 1. 浏览器搜索"混凝土" → 应返回精确匹配 + 语义相关结果
# 2. 搜索"砼" → 语义搜索应返回混凝土相关内容（即使 FTS5 无精确匹配）
# 3. 点击搜索结果 → 应加载条文详情
# 4. 维度筛选 + 关键词 → 结果应同时满足两个条件
```
