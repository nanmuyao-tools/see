# 第一课：暴力检索

## 目标
搞清楚「语义检索」到底在做什么，亲手把它拆成几步，每一步都能在代码里找到。

## 链路
```
需求一句话 ──嵌入模型──▶ 1024 个数（向量） ──存进 MongoDB 的 embedding 字段
查询一句话 ──同一个嵌入模型──▶ 查询向量
        对每条需求：算 查询向量 · 需求向量（归一化后点积 = 余弦相似度）
        排序，取前 k，同一簇只留一条，过滤掉低于下限的
```

## 代码在哪
| 步骤 | 位置 |
|---|---|
| 文本变向量 | `demand_radar/cluster.py` 的 `embedder`（走 Ollama 的 `/v1/embeddings`） |
| 向量存哪 | MongoDB `demands.embedding`，同时记 `embedding_model`（见 `store.save_embedding`） |
| 余弦相似度 | `cluster.unit`、`cluster._dot`（归一化后点积，用 C 实现的 `math.sumprod`） |
| 暴力检索 | `demand_radar/retriever.py` 的 `BruteForceRetriever` |
| 过滤和展示 | `demand_radar/search.py`（榜单、领域、来源、天数、相似度标签） |
| 给 agent 用 | `demand_radar/mcp_server.py`（MCP 工具 `search_demands`） |

## 动手
```bash
docker compose run --rm app search "能追踪快递包裹的工具" -k 5 --board wish
docker compose run --rm app search "浏览器扩展 让多个 AI 同时回答" -k 3 --json
```
结果里每条有「相似度」和相关度标签（较相关 ≥ 0.7、弱相关 0.55–0.7、疑似无关 < 0.55）。

## 要看出来的几件事
1. **向量只是一串数。** 打开 MongoDB 看一条 `embedding`，是 1024 个浮点数，没有任何魔法。
2. **检索就是排序。** `BruteForceRetriever.query` 只有十几行：算分、排序、去重。
3. **同一个嵌入模型。** 换了模型，分数没有可比性，所以只在 `embedding_model` 相同的需求里找。
4. **没有匹配时它也照样给结果。** 找一个库里根本没有的主题，看看最高分是多少（我们测到约 0.55–0.58）。
   这就是为什么要设相似度下限、给结果打标签，以及为什么 agent 必须读内容确认。
5. **标签会误报。** 泛泛的短查询（如「自托管的密码管理器」）会把只是沾边的需求打到 0.7 以上（实测 0.706）。

## 练习
1. 给 `BruteForceRetriever` 加一个关键词加分：标题里含查询词的相似度 +0.05。分数分布怎么变？用的是什么思路——这就是「混合检索」的雏形。
2. 把相似度下限从 0.5 改成 0.7，用 5 条你自己的查询，数一数「该找到的没找到」有几次。
3. 找一条查询，让相关度标签打出「较相关」但内容其实不相关。为什么会这样？

## 它的局限（引出第二课）
- 每次查询都要把所有候选的向量从 MongoDB 读出来。几千条没问题，几十万条就不行了。
- 没有索引：条数翻倍，耗时翻倍（线性）。
- 过滤只能先读出来再在 Python 里筛。

→ [第二课：向量库](02-vector-db.md)
