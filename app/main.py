from fastapi import FastAPI
from app.config import BASE_DIR

app = FastAPI(title="施工规范查询系统 V2")


@app.get("/health")
async def health():
    return {"status": "ok"}
