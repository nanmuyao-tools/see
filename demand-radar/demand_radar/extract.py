"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen2.5:3b
  - 云:   openai/gpt-4o-mini

输出语言由 language 决定（zh / en），只影响 need、domain、pain_point。
摘录不由模型写，由程序从原文挑选（见 excerpt.py），始终是原文原话。
"""

from __future__ import annotations

from . import excerpt
from .models import Demand, SourceItem

LANGUAGES = ("zh", "en")
MAX_TOKENS = 512
REQUEST_TIMEOUT = 120  # 秒

# 写明 buildable 的判断标准并给出正反例。不写标准时 qwen2.5:3b 几乎全判 false：
# 在 14 条真实样本上，有标准答案的 10 条从判对 5 条提升到 9 条。
# 两种语言各用一份完整的提示词。试过英文模式也用中文写的判断标准、只换输出语言那一句，
# 英文模式 F1 从 81.0% 掉到 64.7%，所以不混用。
_SYSTEM = {
    "zh": (
        "你是帮独立开发者发现产品机会的分析助手。给你一条社区帖子或评论，判断它是否在表达"
        "『想要某个工具或功能』，并归纳成结构化需求。\n"
        "判断『个人开发者能否做成』（buildable）的标准：一个人用几周到几个月，靠公开的 API、开源库、"
        "常规的 Web / 移动 / 桌面 / 命令行开发就能做出可用版本，就算能做（true）。大多数软件工具类需求都属于这种。\n"
        "只有这些情况才算做不了（false）：需要突破性的科学或医疗能力；必须由某个平台方修改自己的产品；"
        "需要大量专有数据或特殊资质；或者做出来会侵犯他人隐私、造成伤害。\n"
        "例子：『有没有获取当前窗口信息的命令行工具』→ true；『帮我挑选合适大模型的工具』→ true；"
        "『能消灭癌细胞的软件』→ false；『让某个 App 去掉广告』→ false（得平台方改）。\n"
        "注意区分真实需求和情绪发泄：『卡、广告多、太贵、客服差』这类不是需求。\n"
        "harmful 单独判断：做出来主要用于追踪、曝光、骚扰或欺骗特定的人（偷偷定位别人、查别人住址、"
        "公开某些人的行踪、批量举报、群发垃圾信息、诈骗），就是 true；用户处理自己的数据不算。\n"
        "need、domain、pain_point 用简体中文写。"
    ),
    "en": (
        "You help indie developers find product opportunities. Given a community post or comment, "
        "decide whether it expresses wanting a tool or feature, and summarize it as a structured need.\n"
        "How to judge buildable: if one person could ship a usable version in a few weeks to a few months "
        "with public APIs, open-source libraries and ordinary web / mobile / desktop / command-line "
        "development, it is buildable (true). Most software tool requests are like this.\n"
        "Only these are not buildable (false): it needs a scientific or medical breakthrough; a specific "
        "platform must change its own product; it needs large proprietary data or special licenses; or "
        "building it would violate others' privacy or cause harm.\n"
        "Examples: 'a command-line tool to get the focused window info' → true; 'a tool to help me pick "
        "the right LLM' → true; 'software that destroys cancer' → false; 'make some app remove its ads' "
        "→ false (only the platform can do it).\n"
        "Tell real needs apart from venting: 'slow, too many ads, too expensive, bad support' are not needs.\n"
        "Judge harmful separately: true if the tool would mainly be used to track, expose, harass or deceive "
        "specific people (covertly locating someone, finding someone's address, publishing certain people's "
        "movements, mass reporting, spam, fraud). People handling their own data is not harmful.\n"
        "Write need, domain and pain_point in English."
    ),
}

def build_client(provider: str, ollama_base_url: str | None = None):
    """按 provider 构造 Instructor 客户端。延迟导入，缺依赖时报错更清晰。

    本地模型需要指定服务地址（容器里指向 ollama 服务）；云 provider 忽略该参数。
    `local/<模型>` 用于任何 OpenAI 兼容的本地服务（mlx-lm、LM Studio、llama.cpp），
    instructor 的 ollama 实现本身就是通用的 OpenAI 兼容客户端，这里直接复用。
    """
    try:
        import instructor
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "缺少 instructor 依赖，请先 `pip install -r requirements.txt`"
        ) from e
    if provider.startswith("local/"):
        provider = "ollama/" + provider.removeprefix("local/")
    if provider.startswith("ollama/"):
        # instructor 对 qwen2.5 默认用 TOOLS 模式，但小模型常返回空内容、不调用工具；
        # 实测 JSON 模式稳定得多
        kwargs = {"mode": instructor.Mode.JSON}
        if ollama_base_url:
            kwargs["base_url"] = ollama_base_url
        return instructor.from_provider(provider, **kwargs)
    return instructor.from_provider(provider)


def extract_raw(client, item: SourceItem, language: str = "zh") -> Demand:
    """调模型，返回模型给出的结果（不含摘录）。"""
    if language not in LANGUAGES:
        raise ValueError(f"不支持的输出语言: {language}；可用: {', '.join(LANGUAGES)}")
    content = f"标题 / Title: {item.title}\n正文 / Body: {item.text}".strip()
    return client.chat.completions.create(
        response_model=Demand,
        messages=[
            {"role": "system", "content": _SYSTEM[language]},
            {"role": "user", "content": content},
        ],
        max_retries=2,  # schema 校验失败自动重试
        # 温度 0：同一条内容每次结论一致。默认温度下实测同一评测集两次 F1 相差 8 个百分点
        temperature=0,
        # 防止模型失控：qwen2.5:0.5b 实测会一直生成停不下来，单条卡了 10 分钟。
        # 正常结果只有一两百个 token，512 足够；超时后该条记为失败、跳过
        max_tokens=MAX_TOKENS,
        timeout=REQUEST_TIMEOUT,
    )


def extract(client, item: SourceItem, language: str = "zh") -> Demand:
    """把一条 SourceItem 抽成 Demand，并附上程序从原文挑出的摘录。"""
    d = extract_raw(client, item, language)
    d.excerpt = excerpt.pick(item)
    return d
