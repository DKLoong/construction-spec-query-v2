"""创建管理员用户"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password
from app.database import init_db, get_db


def create_admin(username: str = "admin", password: str = "admin123"):
    init_db()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            print(f"用户 '{username}' 已存在，跳过创建。")
            return

        pwd_hash = hash_password(password)
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, pwd_hash),
        )
        print(f"管理员用户创建成功: {username}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="创建管理员用户")
    parser.add_argument("--username", default="admin", help="用户名 (默认: admin)")
    parser.add_argument("--password", default="admin123", help="密码 (默认: admin123)")
    args = parser.parse_args()
    create_admin(args.username, args.password)
