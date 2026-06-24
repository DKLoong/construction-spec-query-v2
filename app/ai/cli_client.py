import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CLIResponse:
    success: bool
    content: str
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class ClassifyResult:
    clause_id: int
    label: str
    confidence: float


class CLIBackend(ABC):
    command: str = ""

    @abstractmethod
    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse: ...

    def classify_batch_sync(self, clauses: list[dict],
                             dimension: str) -> list[ClassifyResult]:
        """同步版批量分类（供后台任务使用）"""
        dim_labels = {
            "dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"
        }
        dim_label = dim_labels.get(dimension, dimension)

        items = "\n".join(
            f"{i+1}. [ID:{c['clause_id']}] {c['content'][:200]}"
            for i, c in enumerate(clauses)
        )
        prompt = (
            f"你是施工规范分类助手。为以下条文标注{dim_label}维度。\n"
            f"{items}\n\n"
            f"请以 JSON 格式返回分类结果: "
            f'[{{"clause_id": <id>, "label": "<分类标签>", "confidence": <0.0-1.0>}}]'
        )

        import json
        resp = self._run_cli(prompt, timeout=120)
        if resp.success:
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
                logger.error(f"CLI 返回解析失败: {e}")
        return []

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [self.command, "--version"], capture_output=True,
                timeout=5, text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_cli(self, prompt: str, work_dir: str | None = None,
                 timeout: int = 60) -> CLIResponse:
        import time
        start = time.time()
        try:
            cmd = [self.command, "--print", prompt]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=work_dir
            )
            duration = (time.time() - start) * 1000
            if result.returncode == 0:
                return CLIResponse(success=True, content=result.stdout.strip(), duration_ms=duration)
            else:
                return CLIResponse(success=False, content="", error=result.stderr.strip(), duration_ms=duration)
        except subprocess.TimeoutExpired:
            return CLIResponse(success=False, content="", error="CLI 调用超时", duration_ms=timeout*1000)
        except FileNotFoundError:
            return CLIResponse(success=False, content="", error=f"{self.command} 命令未找到，请确认已安装")


class ClaudeCodeCLI(CLIBackend):
    command = "claude"

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)


class CodexCLI(CLIBackend):
    command = "codex"

    async def ask(self, prompt: str, context: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n---\n\n请基于以上上下文回答：{prompt}"
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)


def get_backend(name: str) -> CLIBackend:
    if name == "codex":
        return CodexCLI()
    return ClaudeCodeCLI()
