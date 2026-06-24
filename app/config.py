import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "spec_query.db"))
LANCE_DB_PATH = os.getenv("LANCE_DB_PATH", str(BASE_DIR / "lance_db"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", str(BASE_DIR / "data" / "outputs"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", str(BASE_DIR / "data" / "workspace"))

# 确保目录存在
for d in [UPLOAD_DIR, OUTPUT_DIR, WORKSPACE_DIR, LANCE_DB_PATH]:
    Path(d).mkdir(parents=True, exist_ok=True)
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

# 六大维度定义
DIMENSIONS = {
    "dim1": {"label": "规范属性", "fields": ["hierarchy", "nature", "sys_level", "spec_type"]},
    "dim2": {"label": "工程阶段", "fields": ["stage"]},
    "dim3": {"label": "工程类型", "fields": ["usage", "construction", "scale"]},
    "dim4": {"label": "所属专业", "fields": ["specialty"]},
    "dim5": {"label": "工程部位", "fields": ["location"]},
    "dim6": {"label": "材料/工艺", "fields": ["material"]},
}

# 自适应阈值 (dim_key -> threshold)
ADAPTIVE_THRESHOLDS = {
    "dim1": 0.3,
    "dim2": 0.5,
    "dim3": 0.5,
    "dim4": 0.6,
    "dim5": 0.7,
    "dim6": 0.6,
}

AI_CONFIDENCE_THRESHOLD = 0.7
BATCH_SIZE = 20
BATCH_TIMEOUT_SECONDS = 30
