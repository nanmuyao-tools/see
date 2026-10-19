"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen2.5:7b
  - 云:   openai/gpt-4o-mini
"""

from __future__ import annotations

from .models import Demand, SourceItem

# 写明 buildable 的判断标准并给出正反例。不写标准时 qwen2.5:3b 几乎全判 false：
# 在 14 条真实样本上，有标准答案的 10 条从判对 5 条提升到 9 条。
_SYSTEM = (
    "你是帮独立开发者发现产品机会的分析助手。给你一条社区帖子或评论，判断它是否在表达"
    "『想要某个工具或功能』，并归纳成结构化需求。\n"
    "判断『个人开发者能否做成』（buildable）的标准：一个人用几周到几个月，靠公开的 API、开源库、"
    "常规的 Web / 移动 / 桌面 / 命令行开发就能做出可用版本，就算能做（true）。大多数软件工具类需求都属于这种。\n"
    "只有这些情况才算做不了（false）：需要突破性的科学或医疗能力；必须由某个平台方修改自己的产品；"
    "需要大量专有数据或特殊资质；或者做出来会侵犯他人隐私、造成伤害。\n"
    "例子：『有没有获取当前窗口信息的命令行工具』→ true；『帮我挑选合适大模型的工具』→ true；"
    "『能消灭癌细胞的软件』→ false；『让某个 App 去掉广告』→ false（得平台方改）。\n"
    "注意区分真实需求和情绪发泄：『卡、广告多、太贵、客服差』这类不是需求。"
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
    if provider.startswith("ollama/"):
        # instructor 对 qwen2.5 默认用 TOOLS 模式，但小模型常返回空内容、不调用工具；
        # 实测 JSON 模式稳定得多
        kwargs = {"mode": instructor.Mode.JSON}
        if ollama_base_url:
            kwargs["base_url"] = ollama_base_url
        return instructor.from_provider(provider, **kwargs)
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
        # 温度 0：同一条内容每次结论一致。默认温度下实测同一评测集两次 F1 相差 8 个百分点
        temperature=0,
    )
