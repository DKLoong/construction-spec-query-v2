import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.ai.text_clean import strip_html

logger = logging.getLogger(__name__)

# 分类维度 → 中文标签（供 prompt 描述）
_DIM_LABELS = {"dim4": "所属专业", "dim5": "工程部位", "dim6": "材料/工艺"}

# Few-shot 标注样例（工程规范分类，贴合真实场景；每条带维度上下文）
_FEW_SHOT_EXAMPLES = [
    {
        "dimension": "dim4",
        "dim_label": "所属专业",
        "clause": "框架柱纵向受力钢筋应采用热轧带肋钢筋。",
        "label": "结构",
        "confidence": 0.95,
    },
    {
        "dimension": "dim5",
        "dim_label": "工程部位",
        "clause": "屋面卷材防水层应铺贴在干燥的基层上。",
        "label": "屋面",
        "confidence": 0.92,
    },
    {
        "dimension": "dim6",
        "dim_label": "材料/工艺",
        "clause": "混凝土浇筑完成后应及时进行保湿养护。",
        "label": "混凝土",
        "confidence": 0.97,
    },
]


def build_classify_prompt(clauses: list[dict], dimension: str,
                          candidate_labels: list[str] | None = None) -> str:
    """构建批量分类 prompt（CLI 与 API 后端共用）

    相较旧实现，改进四点：
    - 清理条文内容中的 HTML 残留（OCR/markdown 转换残留标记不进入 prompt）
    - 附带规范编号/名称/条文号上下文，帮助 AI 结合规范语境判断
    - 给出该维度已有标签候选集，约束 AI 标签口径，减少造词
    - 加入 Few-shot 标注样例（放在输出格式说明之后、正式任务之前），
      约束输出 JSON 结构，减少 AI 输出额外文字导致的解析失败

    Args:
        clauses: 待分类批次（get_pending_batch 返回，含 clause_id/content，
                 可选 spec_code/spec_title/clause_no）
        dimension: dim4 / dim5 / dim6
        candidate_labels: 该维度已有标签（规则 pattern + 库内已有值去重）
    """
    dim_label = _DIM_LABELS.get(dimension, dimension)

    lines = []
    for i, c in enumerate(clauses):
        content = (strip_html(c.get("content") or "") or "")[:200]
        ctx = []
        if c.get("spec_code"):
            ctx.append(str(c["spec_code"]))
        if c.get("spec_title"):
            ctx.append(str(c["spec_title"]))
        if c.get("clause_no"):
            ctx.append(f"条文号 {c['clause_no']}")
        prefix = f"{i+1}. [ID:{c['clause_id']}]"
        if ctx:
            prefix += f"（{' '.join(ctx)}）"
        lines.append(f"{prefix} {content}")

    parts = [f"你是施工规范分类助手。请为以下条文标注「{dim_label}」维度。"]
    if candidate_labels:
        parts.append(
            "候选标签（请优先从其中选择；若确实不匹配可新建更贴切标签）:\n"
            + "、".join(candidate_labels)
        )
    # 输出格式说明（Few-shot 样例之前）
    parts.append(
        "输出格式：请仅以 JSON 数组返回分类结果，不要输出其它说明文字:\n"
        '[{"clause_id": <id>, "label": "<标签>", "confidence": <0.0-1.0>}]'
    )
    # Few-shot 标注样例（放在输出格式说明之后、正式任务之前）
    parts.append("标注样例（每条样例带维度上下文，请严格模仿其输出格式与标签口径）:")
    example_lines = []
    for ex in _FEW_SHOT_EXAMPLES:
        example_lines.append(
            f'【维度 {ex["dimension"]}（{ex["dim_label"]}）】\n'
            f'输入: {ex["clause"]}\n'
            f'输出: [{{"clause_id": 9001, "label": "{ex["label"]}", "confidence": {ex["confidence"]}}}]'
        )
    parts.append("\n\n".join(example_lines))
    parts.append("条文列表:")
    parts.append("\n".join(lines))
    parts.append("请仅以 JSON 数组返回分类结果，不要输出其它说明文字。")
    return "\n\n".join(parts)


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
                  system_prompt: str = "",
                  work_dir: str | None = None) -> CLIResponse: ...

    def classify_batch_sync(self, clauses: list[dict], dimension: str,
                             candidate_labels: list[str] | None = None
                             ) -> list[ClassifyResult]:
        """同步版批量分类（供后台任务使用）"""
        prompt = build_classify_prompt(clauses, dimension, candidate_labels)

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
                encoding="utf-8", errors="replace",
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
                  system_prompt: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        # CLI 后端无 system message 通道，把 system prompt 作为指令区拼入首部
        parts = []
        if system_prompt:
            parts.append(f"[系统指令]\n{system_prompt}")
        if context:
            parts.append(f"[参考上下文]\n{context}")
        parts.append(f"[用户问题]\n{prompt}")
        full_prompt = "\n\n---\n\n".join(parts)
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)


class CodexCLI(CLIBackend):
    command = "codex"

    async def ask(self, prompt: str, context: str = "",
                  system_prompt: str = "",
                  work_dir: str | None = None) -> CLIResponse:
        # CLI 后端无 system message 通道，把 system prompt 作为指令区拼入首部
        parts = []
        if system_prompt:
            parts.append(f"[系统指令]\n{system_prompt}")
        if context:
            parts.append(f"[参考上下文]\n{context}")
        parts.append(f"[用户问题]\n{prompt}")
        full_prompt = "\n\n---\n\n".join(parts)
        return self._run_cli(full_prompt, work_dir=work_dir, timeout=60)


def get_backend(name: str | None = None) -> CLIBackend:
    """获取 AI 后端实例

    Args:
        name: 后端名称 (claude/codex/doubao/deepseek/glm/kimi/custom)
              为空时从 settings 表读取 ai.backend
    """
    from app.ocr.paddle_api import get_setting

    if name is None:
        name = get_setting("ai.backend") or "claude"

    if name == "codex":
        return CodexCLI()
    elif name == "claude":
        return ClaudeCodeCLI()
    elif name == "custom":
        from app.ai.api_client import APIBackend
        return APIBackend(
            base_url=get_setting("ai.custom.base_url"),
            api_key=get_setting("ai.custom.api_key"),
            model=get_setting("ai.custom.model") or "gpt-3.5-turbo",
        )
    else:
        # 预置厂商 (doubao/deepseek/glm/kimi)
        from app.ai.provider_presets import PROVIDERS
        from app.ai.api_client import APIBackend

        preset = PROVIDERS.get(name)
        if preset:
            api_key = get_setting(f"{preset['setting_key_prefix']}.api_key")
            return APIBackend(
                base_url=preset["base_url"],
                api_key=api_key,
                model=preset["default_model"],
            )
        # 未知后端，回退到默认
        return ClaudeCodeCLI()
