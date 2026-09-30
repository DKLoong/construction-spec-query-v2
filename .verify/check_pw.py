# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\CC-Workspace\construction-spec-query-v2-aux")
from app.database import get_db
from app.auth import verify_password, hash_password

with get_db() as conn:
    row = conn.execute("SELECT username, password_hash FROM users WHERE username='admin'").fetchone()
    print("USER:", row["username"])
    h = row["password_hash"]
    print("HASH head:", h[:20])
    ok = verify_password("admin123", h)
    print("VERIFY admin123:", ok)

# 重新生成一个 hash 对比
h2 = hash_password("admin123")
print("NEW HASH head:", h2[:20])
print("VERIFY new hash:", verify_password("admin123", h2))
