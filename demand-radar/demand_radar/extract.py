"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen2.5:3b
  - 云:   openai/gpt-4o-mini

输出语言由 language 决定（zh / en），只影响 need、domain、pain_point。
摘录不由模型写，由程序从原文挑选（见 excerpt.py），始终是原文原话。
"""

from __future__ import annotations

from . import excerpt
from .models import Demand, SafetyCheck, SourceItem

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
        "Write need, domain and pain_point in English."
    ),
}

# 有害判断单独调一次模型，只问这一个问题。试过把 harmful 作为字段放进主抽取：3b 模型同时判十来个
# 字段，有害判断只挡住 4 条里的 2 条，还把一条正常的科研需求判成有害；同时连带 buildable 判断变差，
# 45 条真实样本上 F1 从 83.7% 掉到 76.0%。
# 只判断不输出给读者，两种语言共用一份英文提示词。
_SAFETY = (
    "You review product ideas for safety. Given a request for a tool, decide whether the tool, as "
    "described, would mainly be used to harm other people.\n"
    "harmful = true when its main use is:\n"
    "- tracking, monitoring or reading the messages of a specific person without their knowledge or consent\n"
    "- finding or publishing private information about specific people, such as a home address or the "
    "real identity behind an anonymous account\n"
    "- harassing or silencing people, such as mass reporting or mass messaging\n"
    "- deceiving people, such as impersonating a bank or company, phishing, fake reviews or scams\n"
    "- breaking into accounts or devices that are not your own\n"
    "harmful = false for ordinary tools, including ones that handle personal data with consent: managing "
    "your own data, accounts, devices or deliveries; protecting your own privacy or security; sharing "
    "information with people who agreed to it; scientific, medical or educational software.\n"
    "Most tool requests are not harmful. Answer true only when the request itself says or clearly implies "
    "that the tool is meant to be used against a specific other person, or to deceive people.\n"
    "First say in one short sentence who would use the tool and what for, then decide."
)


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


def check_harmful(client, item: SourceItem, need: str = "") -> bool:
    """单独判断这个需求做出来是否主要用来伤害他人。"""
    content = f"Title: {item.title}\nBody: {item.text}\nSummarized need: {need}".strip()
    r = client.chat.completions.create(
        response_model=SafetyCheck,
        messages=[
            {"role": "system", "content": _SAFETY},
            {"role": "user", "content": content},
        ],
        max_retries=2,
        temperature=0,
        max_tokens=MAX_TOKENS,
        timeout=REQUEST_TIMEOUT,
    )
    return r.harmful


def is_candidate(d: Demand) -> bool:
    """会进清单的候选：只有这些需要做有害判断，其他的不额外调模型。"""
    return d.is_request and d.buildable


def judge(client, item: SourceItem, language: str = "zh") -> Demand:
    """主抽取，候选再加一次有害判断。不含摘录。"""
    d = extract_raw(client, item, language)
    if is_candidate(d):
        d.harmful = check_harmful(client, item, d.need)
    return d


def extract(client, item: SourceItem, language: str = "zh") -> Demand:
    """把一条 SourceItem 抽成 Demand，并附上程序从原文挑出的摘录。"""
    d = judge(client, item, language)
    d.excerpt = excerpt.pick(item)
    return d
