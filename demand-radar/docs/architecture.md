# 需求雷达：系统架构

更新：2026-10-20。图片由折叠在每张图下面的 Mermaid 源码生成（GitHub 对这几张图的 Mermaid 渲染会失败，所以提交图片）。改图时先改源码，再在本目录执行：

```bash
npx -y @mermaid-js/mermaid-cli@11.4.2 -i architecture.md -o /tmp/arch.md -e png -s 2 -b white
# 生成的 /tmp/arch-1.png … arch-4.png 依次覆盖 images/architecture-{deployment,pipeline,modules,storage}.png
```

## 一、部署架构

所有东西都在你自己的机器上跑。对外只有一种连接：读各平台的公开接口。

![部署架构](images/architecture-deployment.png)

<details>
<summary>Mermaid 源码（改图时改这里，再重新生成图片）</summary>

```mermaid
flowchart LR
    subgraph EXT["公开数据源（只读，sources.toml 配置）"]
        SE["StackExchange<br/>softwarerecs"]
        HN["Hacker News<br/>Algolia 搜索"]
        GH["GitHub<br/>issue 搜索"]
        AS["Arctic Shift<br/>Reddit 归档"]
        RSS["RSS / Atom<br/>Reddit 等"]
    end

    subgraph DC["docker compose"]
        APP["app<br/>run / eval / digest<br/>backfill / cluster<br/>（跑完即退出）"]
        SCH["scheduler（可选，--profile cron）<br/>每天 run，每周 digest"]
        WEB["web（可选，--profile web）<br/>FastAPI 看板 :8501"]
        OLL["ollama<br/>抽取：qwen3:4b-instruct-2507<br/>嵌入：qwen3-embedding:0.6b"]
        MDB[("MongoDB<br/>demands / clusters<br/>cursors / runs")]
    end

    subgraph HOST["Mac 宿主机（可选，docker-compose.mac-gpu.yml）"]
        MLX["mlx-lm :8765<br/>Qwen3-4B，Apple GPU"]
    end

    OUT["./out<br/>demands.md<br/>digest-日期.md"]
    USER(("你"))

    EXT -->|HTTP 只读| APP
    EXT --> SCH
    APP -->|抽取 / 向量化| OLL
    SCH --> OLL
    APP -.->|Mac GPU 模式下替代 ollama 做抽取| MLX
    APP --> MDB
    SCH --> MDB
    WEB -->|只读| MDB
    APP --> OUT
    SCH --> OUT
    OUT --> USER
    WEB --> USER
```

</details>

| 组件 | 什么时候在跑 | 说明 |
|---|---|---|
| app | 每次 `docker compose run --rm app <命令>` | 跑完一轮就退出；入口脚本先等 ollama、按命令拉需要的模型 |
| scheduler | `--profile cron` 时常驻 | 和 app 同一份代码，自己 sleep 到点，不靠系统 cron |
| web | `--profile web` 时常驻 | 只读库、不调模型；排序和合并逻辑与清单共用 |
| ollama | 默认常驻 | 模型都在本地，不需要 API key |
| mlx-lm | Mac 上可选 | Docker 拿不到 Apple GPU，所以模型在宿主机原生跑；它没有嵌入接口，合并仍走 ollama |
| MongoDB | 常驻 | 数据卷 `mongo-data`，删容器不丢数据 |

## 二、一轮 run 的处理流水线

![处理流水线](images/architecture-pipeline.png)

<details>
<summary>Mermaid 源码（改图时改这里，再重新生成图片）</summary>

```mermaid
flowchart TD
    A["sources/*<br/>按游标增量抓取<br/>单个源失败只跳过它"] --> B{"store.should_process<br/>处理过的跳过<br/>失败的最多重试 3 次"}
    B -->|新的 / 待重试| C["filters<br/>规则预过滤<br/>空帖、Show HN、自我推广、机器人内容"]
    B -->|已处理| SKIP(["跳过"])
    C -->|命中规则| F1[("status = filtered")]
    C -->|通过| D["extract（并发 --workers）<br/>本地模型输出结构化需求：<br/>是不是需求、个人能否做成、三项打分"]
    D --> E1["excerpt<br/>程序从原帖挑一句原话<br/>（不让模型抄写）"]
    D --> E2["safety<br/>规则标记有害需求<br/>（追踪、监视、曝光特定的人）"]
    E1 --> G[("store.save<br/>status = extracted")]
    E2 --> G
    D -->|模型出错| F2[("status = retry / failed")]
    G --> H["rank<br/>三项打分加权 + log(热度)"]
    H --> I["cluster（可选）<br/>向量化 + 余弦相似度 ≥ 0.78 归簇<br/>每簇只留排序分最高的代表"]
    I --> J["report<br/>清单 demands.md / 周报 digest"]
    I --> K["api + web<br/>看板"]
    E2 -.->|命中有害规则| HELD["「待人工确认」区<br/>不进正文"]
    J --> L[("runs<br/>各源抓取数、过滤 / 抽取 / 失败数、耗时")]
```

</details>

几个设计取舍：

- **游标在整批处理完才落库**：中途崩溃就不记，下次重抓同一批，已处理的被去重挡住，不丢也不重复。
- **有害判断用规则不用模型**：小模型要么漏判要么把正常工具也判成有害，实测见 `demand_radar/safety.py` 顶部。
- **排序分读取时计算、不入库**：调权重不用重新抽取。
- **合并只看进清单的需求**：待人工确认的不参与合并。

## 三、代码模块

![代码模块](images/architecture-modules.png)

<details>
<summary>Mermaid 源码（改图时改这里，再重新生成图片）</summary>

```mermaid
flowchart LR
    CLI["cli.py<br/>命令入口"] --> CFG["config.py<br/>命令行 > 环境变量 > 默认值"]
    CLI --> SRC["sources/<br/>softwarerecs · hn_algolia · github<br/>arctic_shift · rss<br/>cursor · http"]
    CLI --> FIL["filters.py"]
    CLI --> EXT["extract.py<br/>Instructor + OpenAI 兼容接口"]
    EXT --> EXC["excerpt.py"]
    EXT --> SAF["safety.py"]
    CLI --> STO["store.py<br/>MongoDB 读写"]
    CLI --> RNK["rank.py"]
    CLI --> CLU["cluster.py"]
    CLI --> REP["report.py"]
    CLI --> SCHD["schedule.py"]
    CLI --> API["api.py + web/index.html"]
    CLI --> EVA["evaluate.py<br/>eval 命令"]
    EVA --> DATA[("eval/eval_set.json 116 条<br/>eval/cluster_pairs.json 23 对")]
    API --> STO
    API --> RNK
    API --> CLU
    CLU --> STO
```

</details>

| 命令 | 做什么 | 要模型吗 |
|---|---|---|
| `run` | 抓取 → 过滤 → 抽取 → 存库 → 出清单（`--cluster` 顺手合并） | 抽取模型 |
| `backfill` | 按页往回拉一个源的历史，可中断续跑 | 抽取模型 |
| `digest` | 汇总最近 N 天，出周报 | 不要 |
| `cluster` | 向量化 + 合并相似需求，重写清单；`--eval` 在标注对上评测 | 嵌入模型 |
| `eval` | 在 116 条标注样本上评测抽取质量 | 抽取模型 |
| `schedule` | 常驻，每天 run、每周 digest | 抽取模型 |
| `serve` | Web 看板 | 不要 |

## 四、数据存储

MongoDB 一个库 `demand_radar`，四个集合：

![数据存储](images/architecture-storage.png)

<details>
<summary>Mermaid 源码（改图时改这里，再重新生成图片）</summary>

```mermaid
erDiagram
    demands {
        string _id "来源:原帖id"
        string status "extracted / filtered / retry / failed"
        string need "一句话需求"
        string domain "领域"
        int clarity "清晰度 1-5"
        int intensity "迫切度 1-5"
        int feasibility "可实现性 1-5"
        int score "原帖热度"
        string excerpt "原文摘录"
        string review_cue "命中的有害规则，空 = 正常"
        array embedding "向量（合并用）"
        string cluster_id "所属簇"
    }
    clusters {
        string _id "c:种子需求id"
        string rep_id "代表需求"
        array members "成员id"
        array sources "来源（去重）"
        int total_score "总热度"
        string model "嵌入模型"
    }
    cursors {
        string _id "run:源:查询 / backfill:源:查询"
        int value "上次抓到的发布时间"
    }
    runs {
        string command "run / backfill / cluster"
        string status "running / done / failed"
        array fetched "各源抓取数"
        int extracted "抽取数"
        float duration_s "耗时"
    }
    clusters ||--|{ demands : "members"
```

</details>

## 五、相关文档

| 文档 | 内容 |
|---|---|
| [`tech-plan.md`](tech-plan.md) | 技术路线、每个版本的验收标准 |
| [`model-selection.md`](model-selection.md) | 模型选型和实测数字 |
| [`arctic-shift.md`](arctic-shift.md) | Reddit 数据源调研与条款 |
| [`research/`](research/) | 专题调研报告 |
