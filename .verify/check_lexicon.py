# -*- coding: utf-8 -*-
import sqlite3
conn = sqlite3.connect(r"D:\CC-Workspace\construction-spec-query-v2-aux\data\spec_query.db")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("TABLES:", tables)
for t in tables:
    if "lexicon" in t or "term" in t:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(t, "count:", n)
        except Exception as e:
            print(t, "ERR", e)
