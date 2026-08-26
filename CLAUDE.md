# CLAUDE.md — construction-spec-query-v2 项目记忆与开发规则

> **效力优先级**：项目规则 > 全局 CLAUDE.md > 用户临时模糊指令
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

## 二、浏览器自动化（Playwright）

> 已安装 playwright 1.62.0，使用系统 Chrome（`channel="chrome"`），无需额外下载浏览器。

### 2.1 同步 API（脚本/简单场景）

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    page.goto("http://localhost:8000")
    # page.locator("...").click()
    # page.screenshot(path="screenshot.png")
    browser.close()
```

### 2.2 异步 API（FastAPI 路由/后台任务推荐）

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch(channel="chrome", headless=True)
    page = await browser.new_page()
    await page.goto("http://localhost:8000")
    # await page.locator("...").click()
    await browser.close()
```

### 2.3 关键参数说明

| 参数 | 说明 |
| ------ | ------ |
| `channel="chrome"` | 使用系统已安装的 Chrome，不走 Playwright 内置 Chromium |
| `headless=True` | 无头模式（CLI/后台必备）；需调试时临时改为 `False` |
| `headless=False` | 有头模式，需桌面环境（仅本地 VS Code 调试时可用） |

### 2.4 注意事项

- 本项目为 FastAPI 应用，路由内使用 Playwright 时优先用**异步 API**
- URL 以 `http://127.0.0.1:8000` 启动的本地服务为测试目标
- 浏览器用完必须 `close()`，或使用 `async with` 自动释放

---

## 三、服务重启规范（高频坑，务必严格执行）

> **背景**：Windows 下 `uvicorn --reload` 是**双进程**——reloader 主进程（监听端口）+ `multiprocessing` worker 子进程。多次启动会残留多个进程树同时监听 8000（SO_REUSEADDR 允许多进程同端口），用户请求被旧 worker 处理 → 改代码「没生效」，且 `--reload` 长期运行会静默失效。**重启必须按以下顺序执行，缺一不可：**

1. **杀全部残留进程**：
   - `netstat -ano | grep :8000 | grep LISTENING` —— 列出**所有**监听 PID（可能有多个）
   - `wmic process where "name='python.exe'" get ProcessId,CommandLine | grep multiprocessing` —— 找 worker 子进程（其 `parent_pid` 指向 reloader）
   - reloader + worker **全部显式 `taskkill //F //PID <pid>` 逐个执行**，观察每条输出；`tasklist` 找不到的 netstat PID 是幽灵（进程已死），真实进程用 wmic 定位 multiprocessing worker
2. **验证端口干净**：`netstat -ano | grep :8000 | grep LISTENING` 应**无输出**
3. **启动**：`D:/Python/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`（后台）
4. **复验唯一监听**：启动后 `netstat :8000 LISTENING` 应**恰好 1 个**，且 wmic 确认该 PID 命令行是 uvicorn
5. **改静态文件/模板后**：浏览器必须 `Ctrl+F5` 强刷；`base.html` 里 `<script src="...?v=N">` 的版本号 `N` 必须**递增**，否则浏览器缓存旧 js 不失效（改 `qa.js`/`md-render.js` 等必查）
