"""AI 厂商预置配置"""

PROVIDERS = {
    "doubao": {
        "name": "豆包 (字节跳动)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-1.5-pro-32k",
        "setting_key_prefix": "ai.doubao",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "setting_key_prefix": "ai.deepseek",
    },
    "glm": {
        "name": "GLM (智谱)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "setting_key_prefix": "ai.glm",
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "setting_key_prefix": "ai.kimi",
    },
}
