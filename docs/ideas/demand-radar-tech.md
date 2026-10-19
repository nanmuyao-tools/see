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

## 实测结论（真实 HN + 本地 qwen2.5:3b，4 核 CPU）

- **本地模型要用 JSON 模式**：instructor 对 qwen2.5 默认用 TOOLS 模式，实测 qwen2.5:3b 返回空内容、不调用工具，全部失败；改用 `Mode.JSON` 后 10/10 成功。JSON_SCHEMA 模式偶尔把 confidence 写成 80（超出 0–1）。代码里已对 ollama 固定使用 JSON 模式。
- **速度**：纯 CPU 上 qwen2.5:3b 约 7 秒/条。
- **判断质量**：10 条 Ask HN 中，8 条讨论帖正确判为非需求；2 条求助帖（依赖平台方修改）正确判为个人做不了。
- **数据源信噪比是瓶颈**：Ask HN 首页大多是讨论，这一批产出 0 条可做需求。下一步应接入 r/SomebodyMakeThis 等专门许愿的数据源。

### 第二轮：多数据源 + 改进 prompt（qwen2.5:3b，每个源/查询 4 条）

- **数据源**：换成 softwarerecs、HN 关键词搜索、Reddit RSS 后，抓到 40 条，抽取 0 失败，最终清单 19 条（Ask HN 首页那轮是 0 条）。GitHub 因沙箱限制未能实测。
- **「个人能否做成」的判断**：不写判断标准时 3b 模型几乎全判 false。写明标准并给正反例后，14 条真实样本中有标准答案的 10 条，判对数从 5 升到 9。另试过「先写障碍再判断」，同样 9 条但把侵犯隐私的需求判成可做，未采用。
- **小模型的输出偏差**：非需求时把文本字段填成 null；可信度偶尔给成百分制。已在数据结构里自动纠正。
- **剩余问题**（19 条里约 15 条有用）：混入 1 条 Show HN 作品发布（供给而非需求）；1 条概括偏离原意；1 条把 prompt 原文带进了输出；部分需求描述是英文；可信度几乎都是 0.9，区分度低。后续可继续调 prompt，或换 7b 模型对比。

### 第三轮：v0.1（61 条评测集，qwen2.5:3b，温度 0）

最终结果：中文 F1 83.6%、英文 89.7%，有害需求进清单 0 条，规则零误杀。过程中的几个结论：

- **摘录不能让小模型抄**：让模型从原文摘一句，约 29% 被翻译成中文或改写，程序校验不过。改为程序挑选：帖子标题本身就是求助句时用标题，否则取正文里第一句带「is there / looking for / I wish / 有没有 / 求」等说法的句子。光一个问号不算。
- **中英文提示词不能混用**：试过英文模式也用中文写的判断标准、只换「用英文输出」一句，英文 F1 从 81.0% 掉到 64.7%。两种语言各写一份完整提示词。
- **有害需求不能交给 3b 模型判断**。端到端实跑时，一条「追踪并公开市议员行踪」的评论（OpenFlock）进了周报。试了四种做法：

  | 做法 | 结果 |
  |---|---|
  | `harmful` 作为字段放进主抽取 | 4 条有害只挡住 2 条；连带「能否做成」判断变差，F1 83.7% → 76.0% |
  | 对候选单独再调一次模型 | 有害全挡住，但模型会想象任何软件被滥用：API 测试工具、推送服务器、借款记账都被判有害，中英文误伤 6 / 12 条。改提示词、改成「选择工具用在谁身上」都没改善 |
  | 规则初筛，命中的再问模型 | 模型放过了 OpenFlock，命中规则的 5 条正常真实帖子误判 3 条 |
  | **规则标记，转人工确认**（采用） | 提到特定他人（偷偷定位、查住址、批量举报、刷好评、冒充银行等）的需求不进正文，放到报告末尾「待人工确认」区并写明命中的原文。254 条真实帖子命中 6 条（约 2%）。不多调模型 |

  周报发布前本来要人看一遍，误标的正常需求也看得到，不会悄悄消失。
- **评测集要再扩**：在 60 条左右的样本上，1–2 条的差别分不清是改进还是噪声（中文离验收线 85.1% 只差 1 条）。v0.2 先扩到 100 条以上，再做细调。

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

## 部署：Docker 一键本地运行

**目标**：用户只装 Docker，`docker compose up` 即可运行，不手动装 Python / MongoDB / Ollama。

三个服务编排在 `docker-compose.yml`：

| 服务 | 镜像 | 职责 | 数据卷 |
|---|---|---|---|
| app | 本项目 Dockerfile | 抓取 + 抽取 + 输出，跑完即退出（批处理） | `./out` 挂出需求清单 |
| mongo | `mongo:7` | 存储 | `mongo-data` 持久化 |
| ollama | `ollama/ollama` | 本地模型服务 | `ollama-models` 缓存模型 |

要点：

- **模型自动拉取**：app 的 `docker/entrypoint.py` 先等 ollama 就绪，再调 `/api/pull` 拉模型（首次慢，之后走缓存卷），然后运行。
- **容器间地址靠环境变量注入**：`MONGO_URI=mongodb://mongo:27017`、`OLLAMA_BASE_URL=http://ollama:11434/v1`。配置优先级为 命令行 > 环境变量 > 默认值。
- **全本地**：默认不暴露端口，数据不出本机，无 API 费用。
- **低配 / 已有环境**：可把模型换成 `qwen2.5:3b`；或注释掉 ollama 服务、指向宿主机已装的 Ollama。
- **再次运行**：`docker compose run --rm app --limit 50`。

## 配置项（便于切换，不写死）

- 模型 provider 与名称（默认 `ollama/qwen2.5:7b`）
- MongoDB 连接串与库名（默认 `mongodb://localhost:27017` / `demand_radar`）
- 数据源与抓取条数
- 过滤阈值（confidence、buildable）
- 输出路径
