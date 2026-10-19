"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen2.5:3b
  - 云:   openai/gpt-4o-mini

输出语言由 language 决定（zh / en），只影响 need、domain、pain_point；
excerpt 始终保留原文语言，并由程序校验确实出自原文。
"""

from __future__ import annotations

import re

from .models import Demand, SourceItem

LANGUAGES = ("zh", "en")
MAX_TOKENS = 512
REQUEST_TIMEOUT = 120  # 秒

# 写明 buildable 的判断标准并给出正反例。不写标准时 qwen2.5:3b 几乎全判 false：
# 在 14 条真实样本上，有标准答案的 10 条从判对 5 条提升到 9 条。
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
        "need、domain、pain_point 用简体中文写；excerpt 照抄原文，不要翻译。"
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
        "Write need, domain and pain_point in English; copy excerpt verbatim, do not translate."
    ),
}

# 摘录比对前的规范化：统一大小写、空白和常见的弯引号、长短横线
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_SPACE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _SPACE.sub(" ", s.translate(_QUOTES)).strip().lower()


def excerpt_in_source(excerpt: str, item: SourceItem) -> bool:
    """摘录是否真的出自原文（标题或正文）。去掉首尾引号和省略号后做规范化子串比对。"""
    e = _norm(excerpt).strip("\"'“”.… ")
    return bool(e) and e in _norm(f"{item.title}\n{item.text}")


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


def extract_raw(client, item: SourceItem, language: str = "zh") -> Demand:
    """调模型，返回未经后处理的结果。评测用它来统计摘录有效率。"""
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
    """把一条 SourceItem 抽成 Demand。摘录不在原文里就清空，宁缺毋滥。"""
    d = extract_raw(client, item, language)
    if d.excerpt and not excerpt_in_source(d.excerpt, item):
        d.excerpt = ""
    return d
