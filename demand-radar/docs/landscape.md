# 需求雷达：同类项目调研与方向分析

调研时间：2026-10-19。项目信息来自各仓库 README 和搜索结果，星数、功能以仓库当前状态为准。

## 一、同类开源项目

| 项目 | 数据源 | 模型 | 特色 | 部署 | 规模 / 协议 |
|---|---|---|---|---|---|
| [Reddit_Scrapper](https://github.com/Mohamedsaleh14/Reddit_Scrapper) | Reddit（PRAW，要 key） | OpenAI / Anthropic 批量 API | **多维打分**：相关度、痛点清晰度、情绪强度、可实现性、技术深度，加权；**便宜模型先筛、强模型再细看**；Streamlit 看板 | 本地 Python，定时任务未完成 | 214★，**仅限非商业使用** |
| [pain-miner](https://github.com/AdvancingTitans/pain-miner) | Reddit（[Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift) API）、HN、**V2EX** | 作为 AI agent 的技能，核心分析用规则 | **不用单一黑盒分数**，分项给证据和反证；跨社区对比 | Python CLI / agent skill | 11★，MIT |
| [sift](https://github.com/Gitter09/sift) | G2、App Store、Google Play、HN、GitHub issues、Product Hunt 等；Reddit 暂停 | OpenAI 兼容接口，可接 Ollama | **embedding + UMAP + HDBSCAN 聚类**；入库时匿名化；多产品对比 | pip 安装的 CLI，无看板 | 1★，MIT |
| [reddit-pain-points](https://github.com/lefttree/reddit-pain-points) | Reddit | Claude / Gemini | FastAPI + React 看板、综合机会分 | 本地，无 Docker | 6★，MIT |
| [idea-pain-point-finder](https://github.com/stevenzhang070302/idea-pain-point-finder) | Reddit | GPT-4o，LangGraph 多智能体 | 痛点强度打分、相似抱怨聚类 | — | 小 |
| [redditlens](https://github.com/0xMassi/redditlens) | Reddit（Serper + .json） | Claude Code 技能 | 找痛点并起草回复（偏获客） | CLI | 11★，MIT |

**相邻方向**：[mnemox-ai/idea-reality-mcp](https://github.com/mnemox-ai/idea-reality-mcp)（MIT，827★，截至 2026-10-20；原文写的「290+★」来自它仓库描述里没更新的文字）做的是"动手之前查一下这个点子是不是已经有人做了"（扫 GitHub、HN、npm、PyPI、Stack Overflow）。这正是我们路线图里"已有方案提示"那一步。2026-10-20 实测补充：2026-08 起维护模式；默认调作者托管的 Claude Haiku 扩写需求；Product Hunt 源已在代码里永久停用；中文需求提取不出关键词。结论是不集成、只借鉴思路，见 [research/2026-10-20-existing-solutions.md](research/2026-10-20-existing-solutions.md)。同标签下的 [MaxKmet/idea-validation-agents](https://github.com/MaxKmet/idea-validation-agents)（MIT，487★）只是给 Claude Code 等用的提示词，没有可集成的代码。

## 二、商业产品

- **GummySearch**：曾经是这个领域最知名的产品，**2025 年底因拿不到 Reddit 商业 API 授权而关停**，留下了空缺。
- 付费替代品：Reddinbox（$39–99/月）、PainOnSocial、Xpoz 等；Apify 上有多个托管的"痛点挖掘"actor；NeedSonar、PainHunt、BigIdeasDB 等网站。

## 三、我们和它们的区别

| | 我们（需求雷达） | 同类项目普遍情况 |
|---|---|---|
| 模型 | **全本地**（Ollama + Qwen），不需要任何 key | 大多依赖 OpenAI / Claude / Gemini 的 API |
| 部署 | **`docker compose up` 一条命令** | 本地装 Python，几乎都没有 Docker |
| 质量 | **有标注评测集和 `eval` 命令**，F1 可量化、可复现 | 没看到哪个项目公开评测方法或准确率 |
| 数据源 | HN、Reddit RSS、**StackExchange softwarerecs**、**GitHub issues** | 绝大多数只有 Reddit |
| 积累 | MongoDB 去重存储（增量、聚类还在路线图） | 多为一次性生成报告 |

搜索时也明确看到一句结论：**没有找到同时具备 Ollama 本地模型、Docker 部署、HN + Reddit 多源的项目。**

## 四、风险

1. **赛道拥挤但没人做成。** 同类项目很多，最大的才 214★，其余都是个位数到十几颗星。可能是"需求清单"本身吸引力有限，也可能只是还没有人做好。这是最大的不确定性。
2. **Reddit 数据获取不稳定。** GummySearch 因此关停；我们的 RSS 也遇到过 429 限流。不能把 Reddit 当成唯一的支柱。
3. **价值验证缺位。** 所有同类项目都在"找出痛点"，但没有一个证明过这些清单真的帮人做出了产品。

## 五、值得借鉴的做法

| 来源 | 做法 | 对我们的意义 |
|---|---|---|
| Reddit_Scrapper | 先用便宜方法初筛，再用模型细看 | 印证了路线图第 1 步"规则预过滤"的方向 |
| Reddit_Scrapper | 分项打分（痛点清晰度、情绪强度、可实现性……） | 可以解决我们"可信度几乎都是 0.9、没有区分度"的问题 |
| pain-miner | 不给单一分数，给证据和反证 | 清单里附原文摘录，方便人工核对 |
| pain-miner | 用 Arctic Shift 获取 Reddit 数据 | **可能解决 Reddit 限流，还能拿到历史数据用于回填**，需要实测确认是否公开可用、条款是否允许 |
| sift | embedding + HDBSCAN 聚类 | 第 3 步"相似需求合并"可以直接参考这套方法 |
| sift | 入库时匿名化，只留来源链接 | 和我们"只展示归纳结果 + 原帖链接"的合规思路一致，可以做得更彻底 |
| idea-reality-mcp | 查点子是否已有人做；测量失败不当成 0 | "已有方案提示"只借鉴思路，不直接集成（2026-10-20 评估，见 [research/2026-10-20-existing-solutions.md](research/2026-10-20-existing-solutions.md)） |

注意：Reddit_Scrapper 的协议是"仅限非商业使用"，只借鉴思路，不复制代码。

## 六、方向选项

**A. 继续独立做，突出差异化**（推荐）
定位成"**完全本地、一条命令部署、质量可量化**的需求发现工具"。这三点同类项目都没做到，而且正好是我们已经做好的部分。后续按路线图推进，同时吸收上面借鉴的做法。

**B. 转为给现有项目做贡献**
例如给 pain-miner（MIT，已经支持 HN 和 V2EX）贡献本地模型支持、评测集。更贴近"给开源社区做贡献"的初衷，但要先和作者沟通，方向不完全由我们决定。

**C. 缩小范围，专做一个细分**
比如只做 **GitHub issues 里高赞但一直没实现的功能请求**，给开源维护者和贡献者用。同类项目几乎都盯着 Reddit，这个方向更空，但用户群也更窄。

## 七、建议

**选 A，同时保持和 B 的连接：**
1. 继续按路线图走，第 1 步规则预过滤的方向被同类项目印证了。
2. 把"分项打分"和"附原文摘录"提前，它们直接解决当前清单的短板。
3. 实测 Arctic Shift，如果可用，Reddit 的稳定性和历史回填问题能一起解决。
4. 做出可用版本后，主动联系 pain-miner、sift 等项目的作者，看能不能互相集成。比如他们用我们的评测方法，我们用他们的数据源。
5. 尽早验证价值：自己用清单挑一个点子真正做出来，这是所有同类项目都缺的证据。
