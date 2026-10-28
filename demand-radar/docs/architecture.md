# 需求雷达：系统架构

对应 2026-10-28 的主线（v1.0.0 之后的未发布改动，见 [`CHANGELOG.md`](../CHANGELOG.md)）。全文是纯 Markdown：流程图用文本画，不依赖任何渲染，也没有图片文件，GitHub 上直接看。

## 一、部署架构

所有东西都在你自己的机器上跑。对外只有一种连接：读各平台的公开接口，所有请求按域名统一限速。

```text
公开数据源（只读，sources.toml 里每个源有 enabled 开关）
  开放     hn_algolia（含 Ask HN）· bluesky · github
  有条件   softwarerecs（CC BY-SA，要署名）
  灰色     arctic_shift · rss（Reddit / V2EX）· appstore · googleplay   ← 默认全关
        │
        │  HTTP 只读，按域名限速
        ▼
docker compose（app / scheduler / web 共用镜像 demand-radar:local，以 uid 1000 运行）
  │
  ├─ app            run / eval / digest / backfill / cluster / search / index …（跑完即退出）
  ├─ scheduler      每天 run、每周 digest（常驻；现在跑成容器 dr-weekly，restart: unless-stopped）
  ├─ web            FastAPI 看板，127.0.0.1:8501，只对本机开放（常驻，可选）
  ├─ mongo          MongoDB 7：demands / clusters / cursors / runs（常驻，数据卷 mongo-data）
  ├─ ollama 0.40.6  抽取 qwen3:4b-instruct-2507 · 嵌入 qwen3-embedding:0.6b（常驻，数据卷 ollama-models）
  └─ qdrant         向量库 v1.19.2（可选，--profile qdrant，默认不启动，数据卷 qdrant-data）

Mac 宿主机（可选，叠加 docker-compose.mac-gpu.yml）
  └─ mlx-lm :8765   Apple GPU 上跑抽取模型；它没有嵌入接口，嵌入仍走 ollama

模型服务有三种选法（--provider 切换，都是 OpenAI 兼容接口）
  ollama 容器（默认；有 NVIDIA 显卡的 Linux 叠加 docker-compose.nvidia.yml 用 GPU）
  Mac 上原生的 mlx-lm（Apple GPU）
  云模型 openai/…（可选；需要 API key、会产生费用，数据会发给服务商——与「全本地」相反，默认不用）

输出
  ./out/demands.md · ./out/digest-日期.md        MCP 服务（stdio）→ Claude Code 等 agent
```

| 组件 | 什么时候在跑 | 说明 |
|---|---|---|
| app | 每次 `docker compose run --rm app <命令>` | 跑完一轮就退出；入口脚本先检查 `./out` 可写、等 ollama、按命令拉需要的模型 |
| scheduler | `--profile cron` 时常驻 | 和 app 同一份代码，自己 sleep 到点，不靠系统 cron；默认跑 `sources.toml` 里启用的全部源 |
| web | `--profile web` 时常驻 | 只读库、不调模型；排序和合并逻辑与清单共用；端口只绑本机，带健康检查 |
| ollama | 默认常驻 | 版本固定 0.40.6；模型都在本地，不需要 API key |
| mlx-lm | Mac 上可选 | Docker 拿不到 Apple GPU，所以模型在宿主机原生跑 |
| nvidia 叠加文件 | NVIDIA 显卡的 Linux 上可选 | `docker-compose.nvidia.yml` 让容器里的 ollama 用 GPU，不改其他服务 |
| MongoDB | 常驻 | 数据卷 `mongo-data`，删容器不丢数据；端口不对外；**唯一的数据来源** |
| Qdrant | `--profile qdrant` 时才起 | 可选的向量库后端，只是 MongoDB 向量的**派生索引**，丢了用 `index --rebuild` 重建；`app` 不依赖它，不起它一切照旧 |

**不用 Docker**：`pip install -e ".[web]"` 之后有 `demand-radar` 命令，自备 MongoDB 和 Ollama（或任意 OpenAI 兼容服务）。要用 Qdrant 加 `.[qdrant]`。在仓库外运行时，没有本地的 `sources.toml` 和评测集就用安装包自带的默认版本。

**数据源由用户自己选**：每个源标了风险档（开放 / 有条件 / 灰色 / 看订阅），灰色来源随包发行的默认配置里一个都不开，开了之后每次运行会提醒；`demand-radar sources` 列出所有源的状态、风险档和条款链接。风险档是我们对条款的理解，不是法律意见。

## 二、配置与模型选型

行为全部由配置决定，没有写死在代码里。优先级：命令行 > 环境变量（`DR_*`、`OLLAMA_*`）> 默认值。

```text
sources.toml    数据源和行为配置（挂载进容器，改完无需重建镜像）
  各源的开关和查询    [softwarerecs] [hn_algolia(.ask_hn)] [github] [bluesky] [arctic_shift] [[rss]] [appstore] [googleplay]
  [http]              按域名限速（没单独配置的用默认间隔）
  [filters]           规则预过滤：可关闭某条规则；supply_sources 列出「发布产品而非需求」的来源，整源跳过
  [ranking]           排序权重：clarity / intensity / feasibility / heat / heat_wish / cluster_size

models.toml     模型档位表：按本机配置从上往下取第一个满足条件的档位
  条件               apple_silicon / min_vram_gb / min_ram_gb
  内容               运行方式（ollama / mlx）、抽取模型、嵌入模型、合并阈值、模型出品地（--exclude-origin cn 可跳过）

recommend 命令 ── hardware.py 检测内存 / CPU / Apple 芯片 / NVIDIA 显存（Docker 里看不到 Mac 的 GPU，可手动指定）
              └─→ 查 models.toml ─→ 推荐抽取 + 嵌入模型 ─→ --write-env 写进 .env，docker compose 自动读取

.env / 环境变量   DR_MODEL、DR_OUTPUT_LANGUAGE、DR_LIMIT、DR_CLUSTER、DR_RETRIEVER、DR_QDRANT_URL …
```

## 三、一轮 run 的处理流水线（写入侧）

```text
sources/*  按游标增量抓取，按域名限速；单个源失败只跳过它
   │
   ▼
store.should_process   处理过的跳过；失败的最多重试 3 次
   │
   ▼
filters   规则预过滤：空帖、Show HN、自我推广、机器人内容
   │         命中规则 ──→ status = filtered
   ▼
extract（并发 --workers）   本地模型输出结构化需求：是不是需求、个人能否做成、三项打分
   │         模型出错 ──→ status = retry / failed
   ├─→ excerpt   程序从原帖挑一句原话（不让模型抄写，抄写时约 29% 会被翻译或改写）
   └─→ safety    规则标记有害需求（追踪、监视、曝光、骚扰、欺骗特定的人）
          │          命中 ──→ review_cue 非空，进「待人工确认」区，不进正文
          ▼
   store.save  status = extracted  ──→ MongoDB.demands
          │
          ▼
   cluster（可选，run --cluster）  向量化 + 余弦相似度 ≥ 0.78 归簇；向量存回 demands.embedding
          │
          ▼
   runs  各源抓取数、过滤 / 抽取 / 失败数、耗时
```

几个设计取舍：

- **游标在整批处理完才落库**：中途崩溃就不记，下次重抓同一批，已处理的被去重挡住，不丢也不重复。
- **有害判断用规则不用模型**：小模型要么漏判要么把正常工具也判成有害，实测见 `demand_radar/safety.py` 顶部。
- **排序分读取时计算、不入库**：调权重不用重新抽取。
- **合并只看进清单的需求**：待人工确认的不参与合并，也永远不会被检索到。
- **`run` / `schedule` 不会自动建 Qdrant 索引**：要用就手动 `index`，默认行为不变。

## 四、读取侧：排序、合并、检索、输出

这一侧都不调抽取模型，全部是对 MongoDB 里已有数据的读取和计算。

```text
MongoDB.demands ──┬─→ rank     排序分 = 三项打分加权 + log(1+热度)×热度权重
                  │             分两个榜（按来源，不看内容）：
                  │               愿望榜     hn / bluesky / softwarerecs / reddit …   热度权重 0.3
                  │               功能请求榜 github                                    热度权重 1.0
                  │             两个榜的排序分不可比，清单 / 周报 / 看板分开展示
                  │
                  ├─→ cluster  相似需求合并：簇里只要有一条愿望，整簇进愿望榜；
                  │             簇的排序分 = 代表的排序分 + log(成员数)×0.5
                  │
                  └─→ search   语义检索：用一句话找最像的需求
                                  │  查询先经嵌入模型（ollama 的 qwen3-embedding）变成向量，
                                  │  必须和库里的向量是同一个模型
                                  ▼
                           Retriever 接口（两个后端，结果一致，有测试保证）
                             ├─ BruteForceRetriever  默认。从 MongoDB 读向量，内存里算余弦
                             └─ QdrantRetriever      可选。HNSW 近似索引 + payload 过滤
                                  │
                                  ▼
                           命中的 id → 回 MongoDB 取内容并核对「仍然入选」→ 附相关度标签

输出
  清单 demands.md · 周报 digest-日期.md       report.py，中 / 英文，愿望榜在前
  Web 看板 :8501                               api.py + web/index.html，有「榜单」下拉框
  MCP 服务（stdio）                            mcp_server.py：search_demands / get_demand / list_top
  search 命令                                  命令行里直接检索

Claude Code 怎么连上 MCP 服务
  Claude Code ──stdio──▶ docker compose run --rm -i -T --entrypoint python app -m demand_radar mcp
  （Mongo、Ollama 没映射宿主机端口，所以服务必须在容器里跑；绕过入口脚本，因为它会往 stdout 打印、污染协议）
```

**检索的约定**（两个后端都遵守）：只返回入选的需求，待人工确认的永远不返回；只在 `embedding_model` 相同的需求里找；同一簇只留最像的一条；相似度是余弦，低于下限的不返回。结果带相关度标签：较相关（≥ 0.7）、弱相关（0.55–0.7）、疑似无关（< 0.55）。这几个阈值是在 47 条数据上看出来的经验值，泛泛的短查询会误报，所以标签只是辅助。

**Qdrant 这一支为什么这样设计**：MongoDB 始终是唯一来源；点 ID 用 `uuid5(需求 id)`，让重复 `index` 幂等；榜单、领域、来源、时间、可信度、簇 key 放 payload 并建 payload 索引；用 Cosine，分数和暴力检索同一量纲；选了 qdrant 却连不上会报错，**不会悄悄退回暴力检索**。教学见 [`lessons/`](lessons/README.md)。

## 五、代码模块

```text
docker/entrypoint.py      容器入口：检查输出目录、等模型、拉模型
cli.py                    demand-radar 命令入口，编排各步骤
config.py                 命令行 > 环境变量 > 默认值；默认配置随包分发

sources/                  数据源，各自适配成统一的 SourceItem
  softwarerecs · hn_algolia · bluesky · github · arctic_shift · rss · appstore · googleplay
  __init__.py             注册表 + 每个源的风险档（PROVIDERS）
  http.py                 按域名限速、限流冷却、重试
  cursor.py               增量游标

models.py                 核心数据结构：SourceItem（各源统一的输入）、Demand（模型输出的 pydantic schema，会作为 JSON schema 发给模型）
filters.py                规则预过滤
extract.py                Instructor + OpenAI 兼容接口，本地模型抽取
  excerpt.py · safety.py  原话摘录 · 有害规则
store.py                  MongoDB 读写

rank.py                   排序分、两个榜
cluster.py                向量化、相似需求合并
retriever.py              Retriever 接口 + 暴力检索后端
qdrant_backend.py         Qdrant 后端：index 同步 + 检索（可选，懒导入 qdrant-client）
search.py                 语义检索：嵌入查询 → Retriever → 回库核对 → 相关度标签
mcp_server.py             MCP 服务（stdio，最小 JSON-RPC，不依赖 mcp SDK）

hardware.py               检测本机内存 / CPU / Apple 芯片 / NVIDIA 显存（recommend 用，只用标准库）
recommend.py              recommend 命令：按 models.toml 档位表推荐模型，可写 .env

report.py                 清单、周报
api.py + web/index.html   Web 看板
schedule.py               常驻调度
evaluate.py               eval 命令；eval/ 下有评测集：eval_set.json 116 条、cluster_pairs.json 23 对、review_set.json 150 条

bench/vector_bench.py     暴力检索 vs HNSW 的基准（不在包里）

仓库根目录的其他东西
  sources.toml · models.toml                配置（见第二节）
  Dockerfile · docker-compose.yml           部署；另有 .mac-gpu.yml / .nvidia.yml 两个叠加文件
  requirements.lock · requirements-qdrant.lock   精确依赖版本（后者只在构建带 Qdrant 的镜像时用）
  eval/                                     评测集（见第七节）
  out/                                      清单和周报的输出目录（挂载进容器）
```

| 命令 | 做什么 | 要模型吗 |
|---|---|---|
| `run` | 抓取 → 过滤 → 抽取 → 存库 → 出清单（`--cluster` 顺手合并） | 抽取模型 |
| `backfill` | 按页往回拉一个源的历史，可中断续跑 | 抽取模型 |
| `digest` | 汇总最近 N 天，出周报 | 不要 |
| `cluster` | 向量化 + 合并相似需求，重写清单；`--eval` 在标注对上评测 | 嵌入模型 |
| `eval` | 在标注样本上评测抽取质量 | 抽取模型 |
| `schedule` | 常驻，每天 run、每周 digest | 抽取模型 |
| `serve` | Web 看板 | 不要 |
| `sources` | 列出所有数据源：是否启用、风险档、条款链接 | 不要 |
| `search` | 语义检索，只读 | 嵌入模型 |
| `mcp` | MCP 服务（stdio），给 Claude Code 等 agent 用，只读 | 嵌入模型 |
| `index` | 把向量同步进 Qdrant（可选），幂等 | 不要 |
| `recommend` | 检测本机配置，推荐抽取和嵌入模型 | 不要 |

## 六、数据存储

MongoDB 一个库 `demand_radar`，四个集合。

**`demands`**：每条抓到的内容一条文档，`_id` 形如 `来源:原帖id`。

| 字段 | 含义 |
|---|---|
| `status` | `extracted` / `filtered` / `retry` / `failed` |
| `is_request` / `buildable` | 是不是需求、个人能否做成；和 `confidence`、`review_cue` 一起决定「入选」 |
| `source` / `kind` / `title` / `url` | 来源、帖子还是评论、原帖标题和链接 |
| `need` / `domain` / `pain_point` | 一句话需求、领域、现有痛点 |
| `clarity` / `intensity` / `feasibility` | 三项打分，1–5 |
| `confidence` | 可信度 |
| `score` | 原帖热度（点赞、得分等） |
| `excerpt` | 程序挑的原帖原话 |
| `review_cue` | 命中的有害规则，空 = 正常 |
| `embedding` / `embedding_model` | 向量（1024 维）和算它的模型；合并和检索用 |
| `cluster_id` | 所属簇 |

榜单（愿望 / 功能请求）**不存库**，由 `source` 在读取时推出。

**`clusters`**：每簇一条，`_id` 形如 `c:种子需求id`。字段：`rep_id`（代表）、`members`、`sources`（去重）、`total_score`（总热度）、`size`、`model`（嵌入模型，换模型要重建）。

**`cursors`**：增量抓取的游标，`run:源:查询` / `backfill:源:查询` → 上次抓到的发布时间。

**`runs`**：每次运行一条，记命令、状态、各源抓取数、过滤 / 抽取 / 失败数、耗时。

**Qdrant（可选）**：一个嵌入模型一个集合，名如 `demand_radar__qwen3_embedding_0_6b`；点 ID 是 `uuid5(需求 id)`，payload 存 `_id`、`board`、`domain`、`source`、`extracted_ts`、`confidence`、`group_key`；显示内容不放这里，回 MongoDB 取。

## 七、质量保障

| 手段 | 内容 |
|---|---|
| 离线测试 | 364 个，用 mongomock 和假嵌入，不碰网络、不起服务；`python -m unittest discover -s tests` |
| 一致性测试 | `tests/test_qdrant_parity.py`：同一批数据，暴力检索和 Qdrant 必须返回相同的 id、顺序、相近的分数（16 种情况） |
| 评测集 | `eval/eval_set.json` 116 条（抽取）、`eval/cluster_pairs.json` 23 对（相似需求合并）、`eval/review_set.json` 150 条（应用商店评论）；`eval` / `cluster --eval` 命令跑出 F1 |
| CI | GitHub Actions：ruff；Python 3.11 / 3.12 上跑测试（`-W error::ResourceWarning`）；打包后在仓库外的全新环境里装上试跑；Docker 镜像能构建、能运行 |
| 默认行为保护 | 有测试保证灰色数据源默认全关；Qdrant 是可选依赖，没装时相关测试跳过、其余照常 |

## 八、相关文档

| 文档 | 内容 |
|---|---|
| [`tech-plan.md`](tech-plan.md) | 技术路线、每个版本的验收标准 |
| [`model-selection.md`](model-selection.md) | 模型选型和实测数字 |
| [`arctic-shift.md`](arctic-shift.md) | Reddit 数据源调研与条款 |
| [`lessons/`](lessons/README.md) | 教学：检索（暴力检索 → 向量库 Qdrant） |
| [`research/`](research/) | 专题调研报告（含 2026-10-28 数据源调研） |
