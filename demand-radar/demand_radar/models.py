"""核心数据结构。

Demand 的字段说明用英文写：它们会作为 JSON schema 发给模型，用中性的英文，
输出中文还是英文由系统提示决定（见 extract.py），两种语言共用一套结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema


class Demand(BaseModel):
    """从一条评论/帖子里抽取出的需求。字段设计服务于「筛掉噪音」和「排序」。"""

    is_request: bool = Field(
        description="Whether the post expresses wanting a tool/feature, or complains that "
        "existing tools lack something. Pure venting (slow, too many ads, bad support) is false."
    )
    need: str = Field(description="What the user wants, in one sentence. Empty if is_request is false.")
    domain: str = Field(description="Short category, e.g. screenshot tool, productivity, developer tools.")
    pain_point: str = Field(description="What is wrong with existing solutions; empty if none.")
    # 摘录由程序从原文挑选（见 excerpt.py），不进发给模型的 schema
    excerpt: SkipJsonSchema[str] = ""
    buildable: bool = Field(
        description="Whether a solo developer could build it as a standalone tool or product."
    )
    # 是否有害由单独一次模型调用判断（见 extract.check_harmful），不进主抽取的 schema
    harmful: SkipJsonSchema[bool] = False
    clarity: int = Field(ge=1, le=5, description="How clearly the need is described, 1 (vague) to 5 (specific).")
    intensity: int = Field(ge=1, le=5, description="How strongly the user wants it, 1 (idle thought) to 5 (urgent, would pay).")
    feasibility: int = Field(ge=1, le=5, description="How easy it is for a solo developer to build, 1 (very hard) to 5 (easy).")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence that this is a real, buildable need, a decimal between 0 and 1, e.g. 0.8.",
    )

    # 小模型常见的输出偏差，在校验前纠正，避免整条白白重试失败
    @field_validator("need", "domain", "pain_point", "excerpt", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v  # 「留空」被理解成 null

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent_to_ratio(cls, v):
        if isinstance(v, (int, float)) and 1 < v <= 100:
            return v / 100  # 按百分制给出，如 80
        return v

    @field_validator("clarity", "intensity", "feasibility", mode="before")
    @classmethod
    def _to_1_5(cls, v):
        if v is None:
            return 1
        if isinstance(v, (int, float)):
            return min(5, max(1, round(v)))  # 给了 0、6 或小数，夹到 1–5
        return v


class SafetyCheck(BaseModel):
    """有害判断的输出。先写理由再下结论，小模型判得更稳。"""

    reason: str = Field(description="One short sentence: what the poster says they want to do with the tool.")
    harmful: bool = Field(description="Whether the tool would mainly be used to harm other people.")

    @field_validator("reason", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v


@dataclass
class SourceItem:
    """数据源抓取到的一条原始内容，送去抽取前的统一形态。"""

    source: str          # 来源名，如 hackernews、github、reddit/macapps
    external_id: str     # 源内唯一 id
    title: str
    text: str
    url: str             # 原帖链接
    score: int = 0       # 热度：GitHub 点赞、HN 得分、StackExchange 投票；RSS 无此信息为 0
    kind: str = "post"   # post 帖子本身；comment 帖子下的评论（标题是所在帖子的标题）
