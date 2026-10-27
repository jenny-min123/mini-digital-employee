"""模型调用封装

Key 只从环境变量 / .env 读取，代码里绝不出现明文。
没配 Key 时不会崩：上层会自动切到离线模板写作（demo 和单元测试都靠它）。
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

# 变量名 -> (base_url, 默认模型)。三个变量名任一存在即可，优先级从上到下
PROVIDERS = [
    ("TENCENT_API_KEY", "TENCENT_BASE_URL", "TENCENT_MODEL",
     "https://tokenhub.tencentmaas.com/v1", "hy3-preview"),
    ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
     "https://api.deepseek.com/v1", "deepseek-chat"),
    ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_MODEL",
     "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
]


class LLMNotConfigured(RuntimeError):
    """没有可用的 API Key"""


def resolve() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)，找不到就抛 LLMNotConfigured"""
    for key_var, url_var, model_var, default_url, default_model in PROVIDERS:
        key = os.getenv(key_var, "").strip()
        if key:
            return key, os.getenv(url_var, default_url), os.getenv(model_var, default_model)
    raise LLMNotConfigured(
        "未找到 API Key：请在项目根目录的 .env 里设置 TENCENT_API_KEY / DEEPSEEK_API_KEY / "
        "DASHSCOPE_API_KEY 之一（.env 已在 .gitignore 中，不会进仓库）"
    )


def available() -> bool:
    try:
        resolve()
        return True
    except LLMNotConfigured:
        return False


def model_name() -> str:
    try:
        return resolve()[2]
    except LLMNotConfigured:
        return "离线模板"


def client():
    """构造 OpenAI 兼容客户端。key 只在内存里，不打印、不落盘。"""
    key, base_url, _ = resolve()
    from openai import OpenAI

    return OpenAI(api_key=key, base_url=base_url, timeout=60, max_retries=1)


def chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = False,
         tools: list[dict] | None = None, max_tokens: int = 2000):
    """一次对话调用。json_mode=True 时要求返回 JSON 对象。"""
    _, _, model = resolve()
    cli = client()
    kwargs: dict = {"model": model, "messages": messages, "temperature": temperature,
                    "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        return cli.chat.completions.create(**kwargs)
    except Exception:
        if not json_mode:
            raise
        # 有些中转服务不支持 response_format，去掉再试一次
        kwargs.pop("response_format", None)
        return cli.chat.completions.create(**kwargs)


def chat_text(messages: list[dict], **kwargs) -> str:
    resp = chat(messages, **kwargs)
    return (resp.choices[0].message.content or "").strip()


def chat_json(messages: list[dict], **kwargs) -> dict:
    text = chat_text(messages, json_mode=True, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError(f"模型没有返回合法 JSON：{text[:200]}")
