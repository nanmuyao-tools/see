"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen2.5:7b
  - 云:   openai/gpt-4o-mini
"""

from __future__ import annotations

from .models import Demand, SourceItem

_SYSTEM = (
    "你是帮独立开发者发现产品机会的分析助手。"
    "给你一条社区帖子/评论，判断它是否在表达『想要某个工具或功能』，"
    "并归纳成结构化需求。注意区分真实需求和单纯的情绪发泄："
    "『卡、广告多、太贵、客服差』这类不是可做的需求。"
)


def build_client(provider: str, ollama_base_url: str | None = None):
    """按 provider 构造 Instructor 客户端。延迟导入，缺依赖时报错更清晰。

    本地 ollama 需要指定服务地址（容器里指向 ollama 服务）；云 provider 忽略该参数。
    """
    try:
        import instructor
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "缺少 instructor 依赖，请先 `pip install -r requirements.txt`"
        ) from e
    if provider.startswith("ollama/") and ollama_base_url:
        return instructor.from_provider(provider, base_url=ollama_base_url)
    return instructor.from_provider(provider)


def extract(client, item: SourceItem) -> Demand:
    """把一条 SourceItem 抽成 Demand。"""
    content = f"标题: {item.title}\n正文: {item.text}".strip()
    return client.chat.completions.create(
        response_model=Demand,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": content},
        ],
        max_retries=2,  # schema 校验失败自动重试
    )
