# -*- coding: utf-8 -*-
"""创建测试账号 admin/admin123（幂等）"""
import sys
sys.path.insert(0, r"D:\CC-Workspace\construction-spec-query-v2-aux")
from app.auth import hash_password
from app.database import get_db

pw = hash_password("admin123")
with get_db() as conn:
    conn.execute(
        "INSERT OR IGNORE INTO users(username,password_hash,is_active) VALUES('admin',?,1)",
        (pw,),
    )
    rows = conn.execute("SELECT username, is_active FROM users").fetchall()
    print("USERS:", [(r["username"], r["is_active"]) for r in rows])
