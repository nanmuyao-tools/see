# 需求雷达：系统架构

更新：2026-10-20。图用 Mermaid 写，GitHub 上直接显示成图；改代码时顺手改这里。

## 一、部署架构

所有东西都在你自己的机器上跑。对外只有一种连接：读各平台的公开接口。

```mermaid
flowchart TB
    EXT["公开数据源（只读，sources.toml 配置）<br/>StackExchange softwarerecs · Hacker News（Algolia）· GitHub issue<br/>Reddit（Arctic Shift 归档 / RSS）· 任意 RSS / Atom"]

    subgraph DC["docker compose（你的机器）"]
        direction TB
        subgraph RUNNERS[" "]
            direction LR
            APP["app<br/>run / eval / digest / backfill / cluster<br/>跑完即退出"]
            SCH["scheduler（--profile cron）<br/>每天 run，每周 digest"]
        end
        OLL["ollama<br/>抽取 qwen3:4b-instruct-2507<br/>嵌入 qwen3-embedding:0.6b"]
        MDB[("MongoDB<br/>demands · clusters · cursors · runs")]
        WEB["web（--profile web）<br/>FastAPI 看板 :8501"]
    end

    MLX["mlx-lm :8765（Mac 可选）<br/>Qwen3-4B 跑在 Apple GPU<br/>docker-compose.mac-gpu.yml"]
    OUT["./out<br/>demands.md · digest-日期.md"]
    USER(("你"))

    EXT -->|HTTP 只读| RUNNERS
    RUNNERS -->|抽取 / 向量化| OLL
    RUNNERS -.->|Mac 上替代 ollama 做抽取| MLX
    RUNNERS -->|写| MDB
    RUNNERS -->|写| OUT
    MDB -->|只读| WEB
    OUT --> USER
    WEB --> USER
```

| 组件 | 什么时候在跑 | 说明 |
|---|---|---|
| app | 每次 `docker compose run --rm app <命令>` | 跑完一轮就退出；入口脚本先等 ollama、按命令拉需要的模型 |
| scheduler | `--profile cron` 时常驻 | 和 app 同一份代码，自己 sleep 到点，不靠系统 cron |
| web | `--profile web` 时常驻 | 只读库、不调模型；排序和合并逻辑与清单共用 |
| ollama | 默认常驻 | 模型都在本地，不需要 API key |
| mlx-lm | Mac 上可选 | Docker 拿不到 Apple GPU，所以模型在宿主机原生跑；它没有嵌入接口，合并仍走 ollama |
| MongoDB | 常驻 | 数据卷 `mongo-data`，删容器不丢数据 |

## 二、一轮 run 的处理流水线

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

几个设计取舍：

- **游标在整批处理完才落库**：中途崩溃就不记，下次重抓同一批，已处理的被去重挡住，不丢也不重复。
- **有害判断用规则不用模型**：小模型要么漏判要么把正常工具也判成有害，实测见 `demand_radar/safety.py` 顶部。
- **排序分读取时计算、不入库**：调权重不用重新抽取。
- **合并只看进清单的需求**：待人工确认的不参与合并。

## 三、代码模块

```mermaid
flowchart TB
    subgraph L1["入口"]
        direction LR
        CLI["cli.py<br/>七个命令"]
        CFG["config.py<br/>命令行 > 环境变量 > 默认值"]
        SCHD["schedule.py<br/>常驻定时"]
    end
    subgraph L2["采集"]
        SRC["sources/<br/>softwarerecs · hn_algolia · github · arctic_shift · rss<br/>cursor.py 增量游标 · http.py 限流重试"]
    end
    subgraph L3["处理"]
        direction LR
        FIL["filters.py<br/>规则预过滤"]
        EXT["extract.py<br/>本地模型抽取"]
        EXC["excerpt.py<br/>挑原文摘录"]
        SAF["safety.py<br/>有害规则"]
    end
    subgraph L4["存储"]
        STO["store.py<br/>MongoDB 读写、去重、状态、游标、运行记录"]
    end
    subgraph L5["排序、合并、输出"]
        direction LR
        RNK["rank.py<br/>排序分"]
        CLU["cluster.py<br/>相似需求合并"]
        REP["report.py<br/>清单 / 周报"]
        API["api.py + web/<br/>看板"]
    end
    subgraph L6["评测"]
        EVA["evaluate.py + cluster --eval<br/>eval_set.json 116 条 · cluster_pairs.json 23 对"]
    end

    L1 --> L2 --> L3 --> L4 --> L5
    L1 --> L6
    L6 -.->|调用同一套抽取 / 嵌入| L3
```

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

## 五、相关文档

| 文档 | 内容 |
|---|---|
| [`tech-plan.md`](tech-plan.md) | 技术路线、每个版本的验收标准 |
| [`model-selection.md`](model-selection.md) | 模型选型和实测数字 |
| [`arctic-shift.md`](arctic-shift.md) | Reddit 数据源调研与条款 |
| [`research/`](research/) | 专题调研报告 |
