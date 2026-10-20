"""抽取层：用 Instructor 让 LLM 把一条内容归纳成 Demand。

provider 字符串决定跑本地还是云（Instructor 统一接口）：
  - 本地: ollama/qwen3:4b-instruct-2507-q4_K_M
  - 云:   openai/gpt-4o-mini

输出语言由 language 决定（zh / en），只影响 need、domain、pain_point。
摘录不由模型写，由程序从原文挑选（见 excerpt.py），始终是原文原话。
可能有害的需求由规则标记（见 safety.py），不交给模型判断。
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import excerpt, safety
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

# 应用商店的低分评论单独一份提示词。帖子的提示词问「这是不是一个能独立做的工具需求」，
# 评论几乎都是「这个 app 该改」，原样套用时 150 条只放过 7 条，而且全判错了可做性
# （见 docs/research/2026-10-20-app-store-reviews.md 4.2）。评论要问的是：这条抱怨说明
# 同类产品有什么机会——做一个替代品、插件或配套工具能不能解决它。
# 例子特意选了评测集（eval/review_set.json）里没有的说法，免得评测数字虚高。
_REVIEW_SYSTEM = {
    "zh": (
        "你是帮独立开发者发现产品机会的分析助手。给你一条应用商店里的低分评论（标题里有 app 名和星级），"
        "判断它是否暴露了一个『别人可以做个替代品、插件或配套工具来解决』的痛点，并归纳成结构化需求。\n"
        "算痛点（is_request=true）：\n"
        "1. 说清了缺什么功能或想要什么工作流，例如『希望有深色模式』『想在 Apple Watch 上快速记一笔』；\n"
        "2. 对产品方向的不满，同类用户换一个 app 就能解决，例如开屏广告太多、核心功能被锁进会员、"
        "强推 AI、涨价、改版后变难用、数据被锁在 app 里导不出来。\n"
        "不算痛点（false）：崩溃、闪退、同步失败、登录不上这类具体 bug；纯情绪、没有信息量；"
        "退款、客服、账号问题。\n"
        "need 写成一个独立开发者可以去做的东西，不要写成『让某 app 改』，例如『一款没有广告、"
        "基础功能免费的天气 app』『能把笔记离线备份成 Markdown 的工具』。\n"
        "buildable 只看这个替代品或配套工具一个人能不能做出可用版本，大多数为 true。\n"
        "domain 写 app 所属的品类，如记账、笔记。need、domain、pain_point 用简体中文写。"
    ),
    "en": (
        "You help indie developers find product opportunities. Given a low-rated app store review (the "
        "title has the app name and star rating), decide whether it reveals a pain point that someone else "
        "could solve with an alternative app, a plugin or a companion tool, and summarize it as a structured need.\n"
        "Counts as a pain point (is_request=true):\n"
        "1. it names a missing feature or wanted workflow, e.g. 'please add a dark mode', "
        "'I want to log an expense from my Apple Watch';\n"
        "2. it dislikes the product's direction in a way users could escape by switching apps, e.g. "
        "too many ads, core features moved behind a paywall, forced AI, price hikes, a redesign that made "
        "it harder to use, data locked in with no export.\n"
        "Not a pain point (false): specific bugs such as crashes, sync failures or login problems; pure "
        "venting with no information; refunds, support or account issues.\n"
        "Write need as something an indie developer could build, not 'make this app change', e.g. "
        "'a weather app with no ads and a free core', 'a tool that backs up notes to Markdown offline'.\n"
        "buildable only asks whether one person could ship a usable version of that alternative or companion "
        "tool; most are true.\n"
        "domain is the app's category, e.g. budgeting, note-taking. Write need, domain and pain_point in English."
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
            "缺少 instructor 依赖，请先 `pip install -e .`（在 demand-radar 目录）"
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
            {"role": "system", "content": (_REVIEW_SYSTEM if item.kind == "review" else _SYSTEM)[language]},
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
    """把一条 SourceItem 抽成 Demand，附上程序从原文挑出的摘录和有害规则的命中情况。"""
    d = extract_raw(client, item, language)
    d.excerpt = excerpt.pick(item)
    d.review_cue = safety.needs_check(item)
    return d


def extract_many(client, items: list[SourceItem], language: str = "zh",
                 workers: int = 2) -> Iterator[tuple[SourceItem, Demand | None, Exception | None]]:
    """线程池同时发多个模型请求，按完成顺序产出 (item, demand, error)，后两者必有一个为 None。

    只并发模型调用；写库由调用方在主线程做，避免多线程写同一条。
    提升多少取决于模型服务：Ollama 默认一次只跑一个请求（OLLAMA_NUM_PARALLEL），
    纯 CPU 上可能看不出差别，云模型则接近线性。
    """
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {pool.submit(extract, client, it, language): it for it in items}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                yield it, fut.result(), None
            except Exception as e:
                yield it, None, e
    finally:
        # 中断（Ctrl-C）时取消还没开始的，只等正在跑的那几条结束
        pool.shutdown(wait=True, cancel_futures=True)
