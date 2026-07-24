"""OpenAI 兼容 API 后端"""
import logging
from app.ai.cli_client import CLIBackend, CLIResponse

logger = logging.getLogger(__name__)


class APIBackend(CLIBackend):
    """OpenAI 兼容 API 后端 — 支持豆包/DeepSeek/GLM/Kimi/自定义"""

    command = "api"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        """HTTP POST 调用 /chat/completions"""
        import time
        import httpx

        full_prompt = prompt
        if context:
            full_prompt = (
                f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
            )

        messages = [
            {
                "role": "system",
                "content": "你是建筑施工规范查询助手。只根据提供的上下文回答，不要编造规范条文。如果上下文中没有相关信息，请如实告知。回答请使用中文。",
            },
            {"role": "user", "content": full_prompt},
        ]

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": 2048,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                duration = (time.time() - start) * 1000
                return CLIResponse(
                    success=True, content=content, duration_ms=duration,
                )
        except httpx.HTTPStatusError as e:
            duration = (time.time() - start) * 1000
            status_code = e.response.status_code
            if status_code == 429:
                return CLIResponse(
                    success=False, content="",
                    error="API 调用超限 (429)，请检查当日配额或稍后重试",
                    duration_ms=duration,
                )
            return CLIResponse(
                success=False, content="",
                error=f"API 返回错误 (HTTP {status_code})",
                duration_ms=duration,
            )
        except httpx.TimeoutException:
            duration = (time.time() - start) * 1000
            return CLIResponse(
                success=False, content="",
                error="API 调用超时",
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return CLIResponse(
                success=False, content="",
                error=f"API 调用失败: {e}",
                duration_ms=duration,
            )

    def classify_batch_sync(self, clauses, dimension) -> list:
        """同步分类 — 不走 API，回退到基类默认"""
        from app.ai.cli_client import ClassifyResult

        items = "\n".join(
            f"{i+1}. [ID:{c['clause_id']}] {c['content'][:200]}"
            for i, c in enumerate(clauses)
        )
        dim_labels = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}
        dim_label = dim_labels.get(dimension, dimension)

        import asyncio
        resp = asyncio.run(self.ask(
            prompt=f"请以 JSON 格式返回分类结果: "
                   f'[{{"clause_id": <id>, "label": "<分类标签>", "confidence": <0.0-1.0>}}]',
            context=f"你是施工规范分类助手。为以下条文标注{dim_label}维度。\n{items}",
        ))

        if resp.success:
            import json
            try:
                text = resp.content
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    data = json.loads(text[start:end])
                    return [
                        ClassifyResult(
                            clause_id=item["clause_id"],
                            label=item.get("label", ""),
                            confidence=item.get("confidence", 0.5),
                        )
                        for item in data
                    ]
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"API 返回解析失败: {e}")
        return []
