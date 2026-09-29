"""探针专用 mock LLM（OpenAI 兼容接口的最小子集）——**只服务 scripts/probe_qa_ui.py，不是产品代码**。

为什么入仓（2026-09-29）：这份桩此前两次以「临时工具」身份写在 `%TEMP%` 里、用完即删，
于是每次要跑「需要真回答」的探针用例都得重写一遍。它无依赖、无状态、输出确定，
放进 `scripts/` 才能让探针套件**可复现**。若将来不再需要，删它的同时要改
`scripts/probe_qa_ui.py` 文件头的运行方式与本文件的所有引用。

用法（与 probe_qa_ui.py 文件头配套）：

    D:/Python/python.exe scripts/probe_mock_llm.py 8199        # 端口可省，默认 8199

接口：`POST <任意前缀>/chat/completions`，兼容 `stream: true`（SSE）与非流式两种形态
—— 与 `app/ai/api_client.py` 的 `ask` / `ask_stream` 两条路径一一对应。
回答固定含「GB 50204」：那是 `probe_qa_ui.py` 的确定性判据 `MOCK_ANSWER_MARKER`
（用来把「真回答了」与「渲染出一个非空气泡」区分开）。
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 固定回答。**改它之前先看这三条既有判据**（少一条就有用例假红，且红得莫名其妙）：
#   ① 含明文「GB 50204」—— `_qa_ask` 用它区分「真回答了」与「渲染出一个非空气泡」；
#   ② 含 `**加粗**`—— t4 用 `.qa-answer strong` 钉「send() 写 html + 渲染管线跑过」；
#   ③ 够长到能切成多帧 —— t5 用「中途采样 < 最终长度」钉渐进渲染。
#
# ⚠️ ②的加粗**必须加在词上**（`**混凝土强度等级**`），不能写成 `**《GB 50204》第 7.4 节**`：
# CommonMark 的左右边界（flanking）规则要求开定界符**后面不能紧跟标点**且前面是非空白/标点，
# 而 `据**《…` 里 `**` 后面紧跟 `《`（标点）、前面是汉字 ⇒ **不是左边界** ⇒ marked 压根不解析，
# 渲染出来仍是字面 `**`（2026-09-29 实测：t4 因此红在「未渲染出 Markdown 加粗」，
# 而真因是这个桩的文案写法——排查花了半小时，别再踩）。
MARKER_REPLY = (
    "根据《GB 50204》第 7.4 节，**混凝土强度等级**应按标准养护试件的立方体抗压强度评定。"
    "（mock 回答，仅供探针判据使用，不含任何真实检索结果）"
)
# 每帧字符数：**故意切多帧**——单帧回答覆盖不到「流式渲染中途 → done 收尾」这条路径
_CHUNK = 12
# 帧间隔（秒）：**必须有**。真模型的帧是分散到达的才叫"流式"；若把帧一次写完，
# 浏览器在同一个事件循环里全收到 ⇒ t5 的「渐进渲染」用例判为"一次性写入"（实测过）。
# 取 0.04s：几十帧 ≈ 0.3s，足够前端渲染中间态，又不拖慢整个套件。
_FRAME_DELAY = 0.04


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ProbeMockLLM/1.0"

    # 参数名必须与基类一致（`format`，虽遮蔽内建名）——改名会被 pyright 判为不兼容覆写
    def log_message(self, format, *args):       # noqa: A002  不打默认访问日志（探针输出才是证据）
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, b'{"error":{"message":"only /chat/completions"}}', "application/json")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            self._send(400, b'{"error":{"message":"bad json"}}', "application/json")
            return

        model = (body.get("model") or "mock-model") if isinstance(body, dict) else "mock-model"
        is_stream = bool(body.get("stream")) if isinstance(body, dict) else False
        print(f"[mock-llm] stream={is_stream} model={model}", flush=True)
        if is_stream:
            self._stream(model)
            return
        payload = {
            "id": "mock-cmpl-1", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": MARKER_REPLY}}],
        }
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json")

    def _stream(self, model: str) -> None:
        """SSE：逐帧 delta → `[DONE]`。

        不写 Content-Length，靠 `Connection: close` + 连接关闭作为正文结束（HTTP/1.1 允许，
        httpx 的 `aiter_lines` 照常按行读）——这正是 `api_client.ask_stream` 的读取方式。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        head = {"id": "mock-cmpl-1", "object": "chat.completion.chunk", "model": model}
        for i in range(0, len(MARKER_REPLY), _CHUNK):
            frame = dict(head, choices=[{"index": 0, "delta": {"content": MARKER_REPLY[i:i + _CHUNK]}}])
            self.wfile.write(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(_FRAME_DELAY)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8199
    print(f"[mock-llm] listening on http://127.0.0.1:{port}/v1/chat/completions", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()
