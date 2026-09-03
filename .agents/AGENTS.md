# AGENTS.md — construction-spec-query-v2 项目专属规则

> **效力优先级**：项目规则 > 全局 AGENTS.md > 用户临时模糊指令
需求与本规则冲突时，优先执行本规则并主动告知用户风险。

---

## 一、常用项目命令
>
> 执行前需先进入项目目录：`cd /d/CC-Workspace/construction-spec-query-v2`

```bash
D:/Python/python.exe -m pip install -r requirements.txt  # 安装依赖
D:/Python/python.exe -m pytest tests/ -v                  # 运行测试
D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload  # 启动服务
D:/Python/python.exe scripts/create_admin.py              # 创建管理员
D:/Python/python.exe scripts/seed_rules.py                # 写入种子规则
```
