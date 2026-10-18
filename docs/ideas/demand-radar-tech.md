# 需求雷达：技术选型

方案构思见 [demand-radar.md](demand-radar.md)。本文件确定具体技术栈，指导 MVP 实现。

## 总原则

**全链路开源、可本地部署**：评论数据不出本机，无 API 费用，无隐私顾虑。同时保留切换到云 API 的能力，便于低配机器先验证。

## 选型一览

| 层 | 选型 | 协议 | 理由 |
|---|---|---|---|
| 语言 | **Python** | — | 抓取 + LLM 生态最顺手 |
| 抓取（v0） | **Hacker News 官方 API** | 公开 | 完全公开、无需 key、无需注册，最适合先跑通 |
| 抓取（后续） | Reddit API (PRAW)、V2EX API | — | 需各自注册应用拿 key，v1 之后接入 |
| 抽取库 | **[Instructor](https://github.com/567-labs/instructor)** | MIT | Python 事实标准；Pydantic 定义 schema，校验失败自动重试；一行切换模型（本地/云） |
| 模型服务 | **[Ollama](https://github.com/ollama/ollama)** | MIT | 一条命令把开源模型跑成本地服务，开发最省事；可换 vLLM/llama.cpp |
| 模型 | **Qwen 2.5（默认 7B，可配置）** | Apache 2.0 | 中英文俱佳，处理中文评论优于 Llama；协议宽松 |
| 存储 | **MongoDB** | SSPL | 文档型，需求字段会不断演进（加标签、聚类 id、多源原文），无需改表结构，更灵活 |
| 输出（v0） | **Markdown 清单** | — | 最快看到结果；Web 看板留到 v4 |

## 为什么不是其他选择

- **抽取不用传统 NLP / 训练模型**：直接用 LLM 按固定 schema 抽取，效果好、零训练。
- **库不用 LangChain**：这个场景只需「文本进、结构化 JSON 出」，Instructor 更轻、更专注。
- **本地模型服务 Ollama 优先于 vLLM**：开发阶段省事；生产或要吞吐再换 vLLM。
- **模型选 Qwen 而非 Llama/Gemma**：数据源含中文社区，Qwen 中文更好，且 Apache 2.0 最宽松。

## 抽取的数据结构（Pydantic schema，初版）

```python
class Demand(BaseModel):
    need: str          # 用户想要什么（一句话）
    domain: str        # 所属领域，如「截图工具」「效率」「开发工具」
    pain_point: str    # 现有方案的痛点
    buildable: bool    # 是否能做成一个独立产品
    confidence: float  # 模型对「这是个真实可做需求」的信心 0–1
```

`buildable=false` 或 `confidence` 低的用于**过滤噪音**（「卡、广告多、客服差」这类）。这是项目成败关键，schema 要为筛噪音服务。

## 切换本地 / 云

用了 Instructor，切换只改一处配置：

```python
# 本地（默认，全程不出本机）
client = instructor.from_provider("ollama/qwen2.5:7b")

# 云（低配机器先验证效果时用）
client = instructor.from_provider("openai/...")  # 填 API key
```

## 硬件参考

| 模型 | 大致内存 | 适合 |
|---|---|---|
| Qwen 2.5 3B | 2–4GB | 低配机器；抽取/分类够用 |
| Qwen 2.5 7B | ~8GB | 效果与资源的甜点，**默认** |
| 14B+ | 16GB+ | 本任务用不上 |

Apple Silicon + 16GB 以上可直接跑 7B。

## MVP 范围（v0）

```
HN 官方 API 拉最近 N 条帖子（Ask HN / Show HN / 评论）
  → Instructor + 本地 Qwen 抽取成 Demand
  → 存入 MongoDB（原文 + 抽取结果 + 来源链接，按 source+external_id 去重）
  → 过滤掉 buildable=false / 低 confidence
  → 输出按 confidence 排序的 Markdown 需求清单
```

**判断标准**：自己看这份清单，是否觉得「这些点子真能做」。是 → 加源加看板；否 → 调整 schema / prompt / 过滤策略。

## 配置项（便于切换，不写死）

- 模型 provider 与名称（默认 `ollama/qwen2.5:7b`）
- MongoDB 连接串与库名（默认 `mongodb://localhost:27017` / `demand_radar`）
- 数据源与抓取条数
- 过滤阈值（confidence、buildable）
- 输出路径
