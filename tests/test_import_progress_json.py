"""导入进度 JSON 端点（跨页浮标用）：GET /import/progress/{task_id}/json

浮标在**任意页面**轮询本端点取百分比，所以它必须是**形状稳定的纯读 JSON**：
无论任务存在与否，都返回同一组键。理由有二——

1. 全局规则要求「接口返回格式必须统一，禁止成功/失败结构不一致」；
2. 前端浮标只需一条逻辑：读 status 判终态、读 progress 画数字。
   若未知任务改回 404，客户端就得再写一条出错分支，且必然要处理
   「服务重启后 progress_store 清空」这条**正常**路径（不是异常）。

与同族的 `/import/progress/{task_id}`（HTML 片段）保持同一口径：
未知任务 → `status="unknown"`，不抛 404。
"""
import pytest


@pytest.fixture(autouse=True)
def _clean_progress_store():
    """progress_store 是模块级全局字典，用例之间必须隔离"""
    yield
    from app.routes import import_routes
    import_routes.progress_store.clear()


def _set_task(task_id, **fields):
    from app.routes import import_routes
    import_routes.progress_store[task_id] = fields


def test_progress_json_shape_for_known_task(auth_client):
    """进行中任务 → 200 + 统一形状，progress 原样回传"""
    _set_task("t1", status="processing", progress=45, message="正在生成向量...")

    resp = auth_client.get("/import/progress/t1/json")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "processing",
        "progress": 45,
        "message": "正在生成向量...",
        "needs_review": False,
    }


def test_progress_json_flags_review_needed(auth_client):
    """review_needed → needs_review=True（浮标据此显示「去审查」入口）

    这是最容易丢的一步：审查页只在 task 仍是 review_needed 时可进入，
    用户切走后没有任何入口知道任务卡在等审查。
    """
    _set_task("t2", status="review_needed", progress=50, message="等待审查")

    data = auth_client.get("/import/progress/t2/json").json()

    assert data["status"] == "review_needed"
    assert data["needs_review"] is True


def test_progress_json_unknown_task_keeps_shape(auth_client):
    """未知任务（服务重启后 progress_store 清空）→ 仍 200 且形状不变"""
    resp = auth_client.get("/import/progress/deadbeef/json")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "unknown",
        "progress": 0,
        "message": "未知任务",
        "needs_review": False,
    }


def test_progress_json_terminal_states_report_needs_review_false(auth_client):
    """终态（done/error）不得被误判为待审查"""
    _set_task("t3", status="done", progress=100, message="导入完成")
    _set_task("t4", status="error", progress=30, message="导入失败: 解析异常")

    assert auth_client.get("/import/progress/t3/json").json()["needs_review"] is False
    assert auth_client.get("/import/progress/t4/json").json()["status"] == "error"
