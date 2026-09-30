# -*- coding: utf-8 -*-
import sqlite3
conn = sqlite3.connect(r"D:\CC-Workspace\construction-spec-query-v2-aux\data\spec_query.db")
cols = [r[1] for r in conn.execute("PRAGMA table_info(lexicon_entries)").fetchall()]
print("COLS:", cols)
rows = conn.execute("SELECT * FROM lexicon_entries").fetchall()
for r in rows:
    print(r)
