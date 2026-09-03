"""参数设置方案管理路由（/maintenance/params/*）

- GET  meta：注册表元数据（前端渲染/校验）；profiles：方案列表（含虚拟「默认方案」id=0）
- POST profiles / PUT profiles/{id}：新建（自动命名「方案N」）/ 改名+改值
- POST profiles/{id}/apply：把方案值写入 settings 热生效；id=0=应用默认（删覆盖键回退内置默认）

约定：默认方案 is_system、不落库（虚拟 id=0，天然不可改，作一键回滚兜底）。
"""
import json
import re
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.database import get_db
from app.params import registry

router = APIRouter()


def _default_profile() -> dict:
    return {"id": 0, "name": "默认方案", "is_system": True, "values": {}}


def _profile_dict(row) -> dict:
    try:
        values = json.loads(row["values_json"] or "{}")
    except (TypeError, ValueError):
        values = {}
    return {"id": row["id"], "name": row["name"],
            "is_system": bool(row["is_system"]), "values": values}


def _validate_values(values) -> dict:
    """只收注册表内 key；类型/范围校验不过抛 ValueError。空串值视为「用默认」放行。"""
    if not isinstance(values, dict):
        raise ValueError("values 须为对象")
    cleaned = {}
    for key, raw in values.items():
        if key not in registry.all_param_keys_set():
            raise ValueError(f"未知参数: {key}")
        raw = str(raw).strip()
        if raw == "":
            continue  # 空 = 回退默认（不写覆盖）
        ok, _ = registry.validate_value(key, raw)
        if not ok:
            raise ValueError(f"参数超出可调范围: {key}={raw}")
        cleaned[key] = raw
    return cleaned


def _next_profile_name(conn) -> str:
    names = [r["name"] for r in conn.execute("SELECT name FROM param_profiles").fetchall()]
    max_n = 0
    for n in names:
        m = re.fullmatch(r"方案(\d+)", n)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"方案{max_n + 1}"


@router.get("/maintenance/params/meta")
async def params_meta(request: Request):
    return {"groups": registry.PARAM_GROUPS, "params": registry.PARAM_META}


@router.get("/maintenance/params/profiles")
async def params_profiles(request: Request):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, is_system, values_json FROM param_profiles ORDER BY id").fetchall()
    return [_default_profile()] + [_profile_dict(r) for r in rows]


@router.post("/maintenance/params/profiles")
async def params_profile_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求体须为 JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    values = body.get("values") or {}
    try:
        cleaned = _validate_values(values)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    with get_db() as conn:
        if not name:
            name = _next_profile_name(conn)
        try:
            cur = conn.execute(
                "INSERT INTO param_profiles (name, is_system, values_json) VALUES (?, 0, ?)",
                (name, json.dumps(cleaned, ensure_ascii=False)))
            profile_id = cur.lastrowid
        except Exception:
            return JSONResponse({"detail": f"方案名称已存在: {name}"}, status_code=400)
        row = conn.execute(
            "SELECT id, name, is_system, values_json FROM param_profiles WHERE id = ?",
            (profile_id,)).fetchone()
    return _profile_dict(row)


@router.put("/maintenance/params/profiles/{profile_id}")
async def params_profile_update(request: Request, profile_id: int):
    if profile_id == 0:
        return JSONResponse({"detail": "默认方案不可修改"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求体须为 JSON"}, status_code=400)
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, is_system, values_json FROM param_profiles WHERE id = ?",
            (profile_id,)).fetchone()
        if not row:
            return JSONResponse({"detail": "方案不存在"}, status_code=404)
        name = (body.get("name") if body.get("name") is not None else row["name"]).strip()
        values = body.get("values")
        values_json = row["values_json"]
        if values is not None:
            try:
                values_json = json.dumps(_validate_values(values), ensure_ascii=False)
            except ValueError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
        try:
            conn.execute(
                "UPDATE param_profiles SET name = ?, values_json = ?, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (name, values_json, profile_id))
        except Exception:
            return JSONResponse({"detail": f"方案名称已存在: {name}"}, status_code=400)
        row = conn.execute(
            "SELECT id, name, is_system, values_json FROM param_profiles WHERE id = ?",
            (profile_id,)).fetchone()
    return _profile_dict(row)


@router.post("/maintenance/params/profiles/{profile_id}/apply")
async def params_profile_apply(request: Request, profile_id: int):
    """应用方案：id=0 → 删除全部注册表覆盖键（回退内置默认）；否则写该方案值并置 marker"""
    keys = registry.all_param_keys()
    if profile_id == 0:
        with get_db() as conn:
            ph = ",".join("?" * len(keys))
            conn.execute(f"DELETE FROM settings WHERE key IN ({ph})", keys)
            conn.execute(
                "DELETE FROM settings WHERE key = 'params.applied_profile_id'")
        registry.clear_param_cache()
        from app.search.hybrid_search import clear_search_cache
        clear_search_cache()
        return {"status": "ok", "applied": 0}

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, values_json FROM param_profiles WHERE id = ?",
            (profile_id,)).fetchone()
        if not row:
            return JSONResponse({"detail": "方案不存在"}, status_code=404)
        values = json.loads(row["values_json"] or "{}")
        ph = ",".join("?" * len(keys))
        conn.execute(f"DELETE FROM settings WHERE key IN ({ph})", keys)
        for key, raw in values.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, raw))
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES ('params.applied_profile_id', ?)",
            (str(profile_id),))
    registry.clear_param_cache()
    from app.search.hybrid_search import clear_search_cache
    clear_search_cache()
    return {"status": "ok", "applied": profile_id}
