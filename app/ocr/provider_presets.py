"""OCR 后端预置配置 — 参考 AI 模块的 provider_presets 模式"""

OCR_PROVIDERS = {
    "paddle-vl": {
        "name": "PaddleOCR-VL (推荐)",
        "description": "文档解析引擎，输出结构化 Markdown（表格/标题/公式/图表），异步处理",
        "submit_url": "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task",
        "query_url": "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser/task/query",
        "setting_key_prefix": "ocr.paddle-vl",
        "is_async": True,
        "default_params": {
            "analysis_chart": "true",
            "merge_tables": "true",
            "relevel_titles": "true",
            "recognize_seal": "false",
        },
    },
    "accurate-basic": {
        "name": "accurate_basic (旧版)",
        "description": "通用文字识别基础版，仅返回纯文本，无格式保留",
        "url": "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic",
        "setting_key_prefix": "ocr.accurate-basic",
        "is_async": False,
        "default_params": {
            "language_type": "CHN_ENG",
            "detect_direction": "true",
            "paragraph": "true",
        },
    },
    "custom": {
        "name": "自定义",
        "description": "自定义 OCR 端点，支持任意兼容 API 的后端服务",
        "setting_key_prefix": "ocr.custom",
        "is_async": False,
    },
}
