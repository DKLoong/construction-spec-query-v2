"""QA 运行期配置读取（DB settings 优先，config.py 常量兜底）

所有阈值集中可配置（qa.* 键），避免魔法数字；每次请求实时读 DB，
改设置后热生效。默认值集中在 `app/config.py` 的 `QA_CONFIG_DEFAULTS`。
"""
from app.config import QA_CONFIG_DEFAULTS


def _get(key: str) -> str:
    from app.ocr.paddle_api import get_setting
    return get_setting(f"qa.{key}")


def get_qa_float(key: str, default: float = 0.0) -> float:
    """读取 qa.* 数值配置；DB 无值或非法时回退默认（默认来自 QA_CONFIG_DEFAULTS）。"""
    if default == 0.0:
        default = float(QA_CONFIG_DEFAULTS.get(key) or 0.0)
    v = _get(key)
    try:
        return float(v) if v != "" else default
    except (TypeError, ValueError):
        return default


def get_qa_int(key: str, default: int = 0) -> int:
    """读取 qa.* 整数配置；下限校验（>=0），避免配置为负导致异常。"""
    if default == 0:
        default = int(QA_CONFIG_DEFAULTS.get(key) or 0)
    v = _get(key)
    try:
        val = int(v) if v != "" else default
        return max(0, val)
    except (TypeError, ValueError):
        return default


def get_qa_bool(key: str, default: bool = False) -> bool:
    if not default:
        default = bool(QA_CONFIG_DEFAULTS.get(key) or False)
    v = _get(key)
    if v == "":
        return default
    return v.lower() in ("1", "true", "yes", "on")


def get_qa_str(key: str, default: str = "") -> str:
    if not default:
        default = str(QA_CONFIG_DEFAULTS.get(key) or "")
    v = _get(key)
    return v if v != "" else default
