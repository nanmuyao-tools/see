"""需求雷达：抓取社区吐槽/许愿，用 LLM 归纳成结构化需求。"""

import sys

# 用到了 3.11 才有的 tomllib。在这里拦下来，比在导入深处报 ModuleNotFoundError 友好
if sys.version_info < (3, 11):  # noqa: UP036  给 3.9/3.10 用户友好提示，而不是 tomllib 导入错误  # pragma: no cover
    sys.exit(
        f"需求雷达需要 Python 3.11 或更新，当前是 {sys.version.split()[0]}。"
        "macOS 自带的 3.9 不够，请用 brew install python@3.12 装一个，再用 python3.12 运行。"
    )

__version__ = "0.3.0"
