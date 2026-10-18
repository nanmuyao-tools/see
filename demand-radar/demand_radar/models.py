"""核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import BaseModel, Field


class Demand(BaseModel):
    """从一条评论/帖子里抽取出的需求。字段设计服务于「筛掉噪音」。"""

    is_request: bool = Field(
        description="这条内容是否在表达『想要一个工具/功能』或抱怨现有工具缺某功能。"
        "纯粹的情绪发泄（卡、广告多、客服差）应为 False。"
    )
    need: str = Field(description="用户想要什么，一句话。若 is_request 为 False 则留空。")
    domain: str = Field(description="所属领域，如『截图工具』『效率』『开发工具』。")
    pain_point: str = Field(description="现有方案的痛点；没有则留空。")
    buildable: bool = Field(
        description="是否可由个人开发者做成一个独立小工具/产品。需要大量数据、资质、"
        "平台方权限的需求应为 False。"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="对『这是一个真实、可做的需求』的信心，0 到 1 之间的小数，如 0.8。",
    )


@dataclass
class SourceItem:
    """数据源抓取到的一条原始内容，送去抽取前的统一形态。"""

    source: str          # 来源名，如 hackernews、github、reddit/macapps
    external_id: str     # 源内唯一 id
    title: str
    text: str
    url: str             # 原帖链接
    score: int = 0       # 热度：GitHub 点赞、HN 得分、StackExchange 投票；RSS 无此信息为 0
