# 需求雷达 Demand Radar

[English](README.en.md) | 中文

从社区里用户的吐槽和许愿中发现「值得做的需求」：抓取 Hacker News、Reddit、StackExchange、GitHub 等公开内容，用**本地大模型**归纳成结构化需求，打分排序，每周出一份周报。

- **全本地**：模型跑在你自己的机器上（Ollama + Qwen），不需要任何 API key，数据不出本机
- **一条命令部署**：`docker compose up`
- **质量可量化**：人工标注的评测集 + `eval` 命令，给出可复现的准确率

## 它会产出什么

每条需求附**原帖原话**，你可以一眼核对模型有没有理解偏：

```markdown
### 一个可以监控特定应用资源占用的菜单栏工具

- 领域：开发工具
- 现有痛点：活动监视器的数据难以解读，也不能只盯一个应用
- 原文：“Is there an app that monitors a specific app from the menu bar?”
- 清晰度 4 · 迫切度 3 · 可实现性 5 · 排序分 13.5
- 来源：[reddit/macapps](https://www.reddit.com/r/macapps/...) Is there an app that monitors...
```

## 快速开始（Docker，一条命令）

只需装好 [Docker](https://docs.docker.com/get-docker/)，不用自己装 Python、MongoDB、Ollama：

```bash
cd demand-radar
docker compose up        # 首次会下载模型（约 2GB），之后走缓存
```

会起三个容器：**app**（抓取 + 抽取）、**mongo**（存储）、**ollama**（本地模型）。跑完一轮 app 自动退出，结果在 **`./out/demands.md`**。想每天自动跑，见下文「定时运行」。

之后常用的命令：

```bash
docker compose run --rm app                        # 再抓一轮（已分析过的不会重复处理）
docker compose run --rm app digest                 # 生成最近 7 天的周报 → out/digest-<日期>.md
docker compose run --rm app digest --days 14 --lang en   # 最近 14 天，英文周报
docker compose run --rm app backfill --source softwarerecs --pages 20   # 往回拉历史，可中断续跑
docker compose run --rm app eval                   # 在标注集上评测当前模型
```

**机器配置**：默认模型 qwen2.5:3b 约 2GB，8GB 内存的电脑可以跑。低配机器可在 `docker-compose.yml` 里把模型换成 `qwen2.5:1.5b`（更快，但准确率低一些）。已经装了 Ollama 的话，可以注释掉 ollama 服务，把 `OLLAMA_API` / `OLLAMA_BASE_URL` 指向本机。

## 工作流程

```
公开数据源（sources.toml 配置）
  → 规则预过滤：空帖、作品发布帖（Show HN、「我做了一个」）、机器人内容等不交给模型
  → 本地模型抽取：是不是需求、个人能否做成、三项打分
  → 有害标记：提到特定他人的需求（偷偷定位、查别人住址、批量举报、冒充银行等）不进正文，
    放到报告末尾的「待人工确认」区，写明命中了哪句话，由你决定是否发布
  → 原文摘录：程序从原帖挑一句最像需求的原话，不让模型抄写（小模型抄写时常会翻译或改写）
  → 存入 MongoDB：每条都记处理状态，已处理的不再重复；失败的下次重试，最多 3 次
  → 综合排序：三项打分加权 + 原帖热度（取对数）
  → 全量清单 out/demands.md ／ 周报 out/digest-<日期>.md
  → 每次运行在 MongoDB 的 runs 集合记一条：各源抓取数、过滤 / 抽取 / 失败数、耗时
```

出了问题先看运行记录（`status` 停在 `running` 说明进程中途死了）：

```bash
docker compose exec mongo mongosh demand_radar --eval 'db.runs.find().sort({started_at:-1}).limit(5)'
```

## 数据源

全部是公开接口，不需要注册。在 [`sources.toml`](sources.toml) 里配置，加源只改这个文件。

| 源 | 内容 | 热度 | 限额 |
|---|---|---|---|
| `softwarerecs` | StackExchange 软件推荐站：「有没有能做 X 的软件」 | 投票数 | 300 次/天；设 `SE_API_KEY` 提到 1 万 |
| `hn_algolia` | Hacker News 帖子和评论，按 "I wish there was" 等短语搜索 | 得分 | 宽松 |
| `github` | 点赞多的功能请求 issue | 👍 数 | 10 次/分钟；设 `GITHUB_TOKEN` 提到 30 |
| `arctic_shift` | Reddit 帖子，走 [Arctic Shift](docs/arctic-shift.md) 第三方归档 API：按版块拉最新帖、关键词本地筛、能回填历史 | 点赞数（发帖 36 小时后才准） | 动态，每秒几次以内；免费服务无可用性承诺 |
| `rss` | 任意 RSS / Atom 订阅，默认配了 Reddit 的 r/SomebodyMakeThis 和几个板块内搜索（和 `arctic_shift` 覆盖同样的版块，作它的兜底） | — | 视站点 |

某个源失败（网络、限流）只跳过它，其他源照常跑。限流时会按服务器要求等待后自动重试。

**增量抓取**：每个源的每个查询都记住上次抓到的最新一条（存在 MongoDB 的 `cursors` 集合），下次只抓更新的内容；RSS 没有按时间过滤的参数，靠入库去重。首次运行没有游标，行为和以前一样抓最新 N 条。游标在整批处理完才写入，中途崩溃下次会重抓这一批（已处理过的不会重复抽取）。抽取失败的条目存了原文，即使之后不再被抓到也会从库里重试，最多 3 次。

## 定时运行

想让它持续积累，起一个常驻的定时服务（默认不启动，用 `cron` profile 打开）：

```bash
docker compose --profile cron up -d scheduler     # 启动先跑一次，之后每天 03:00（UTC）跑 run，每周一再出周报
docker compose logs -f scheduler                   # 看进度
DR_RUN_AT=18:00 DR_DIGEST_WEEKDAY=5 docker compose --profile cron up -d scheduler   # 改成每天 18:00、周五出周报
```

它和 `app` 用同一份配置和挂载，结果照样在 `./out/`。环境变量：`DR_RUN_AT`（每天几点，HH:MM，按容器时钟即 UTC）、`DR_DIGEST_WEEKDAY`（ISO 周几出周报，1 周一 … 7 周日，0 不出）、`DR_RUN_ON_START`（启动时先跑一次，默认 1）。单次运行失败只记日志，不会让服务退出。

不用 Docker 时直接 `python -m demand_radar schedule --run-at 03:00 --digest-weekday 1`，或者用系统 cron 分别调 `run` 和 `digest`。

## 历史回填

日常 `run` 只抓新内容；想把一个源的历史积累进来，用 `backfill` 按页往回拉：

```bash
python -m demand_radar backfill --source softwarerecs --pages 20        # 每页 100 条，最多 20 页
python -m demand_radar backfill --source hn_algolia --pages 50 --limit 500
python -m demand_radar backfill --source github --pages 10
python -m demand_radar backfill --source arctic_shift --pages 10       # Reddit 历史，每页最多 100 条、页间隔 2 秒；活跃版块可能超时，会跳过换下一个
```

- 一次一个源，源下的每个查询**轮流**拉，`--pages` 是本次的总页数预算
- 按发布时间往回切而不是翻页码，新帖进来也不会漏或重；拉到空页即视为该查询到头
- **每拉完一页就记进度**（`cursors` 集合），Ctrl-C 或配额用完后再运行同一命令就从上次位置继续；全部到头后再运行会直接退出
- 遵守各源限额：StackExchange 不带 key 每天 300 次，剩 10 次时自动停下留给日常抓取（设 `SE_API_KEY` 提到 1 万）；GitHub 不带 token 每分钟 10 次，翻页间隔 6.5 秒；HN 很宽松
- RSS 订阅没有翻页，不支持回填

## 输出语言

需求描述默认用中文，也可以用英文：`--lang en`，或在 `docker-compose.yml` 里设 `DR_OUTPUT_LANGUAGE: "en"`。**原文摘录始终保留原帖语言，不翻译。**

## 评测

改 prompt、改规则或换模型之后，用标注集量化效果，而不是凭感觉。标注集 [`eval/eval_set.json`](eval/eval_set.json) 有 61 条：46 条人工标注的真实样本，加 15 条构造样本（覆盖「追踪、曝光特定的人」这类有害需求和容易被误判的正常需求）。

```bash
python -m demand_radar eval -v                 # 逐条打印
python -m demand_radar eval --lang en          # 英文输出模式下评测
```

输出：是否需求的准确率、能否做成的准确率、端到端「应入选」的精确率 / 召回率 / F1、规则过滤了多少条（以及有没有误杀）、有害需求有没有混进正文清单（必须为 0）、有多少正常需求被转去人工确认。加 `-v` 会打印每条入选需求的原文摘录，方便人工核对。最新结果见 [`ROADMAP.md`](ROADMAP.md)。

## 不用 Docker 本地运行

需要 **Python 3.11 或更新**（用到了 3.11 才有的 `tomllib`）。macOS 自带的 Python 是 3.9，会报 `No module named 'tomllib'`，请用 Homebrew 装一个：`brew install python@3.12`。

```bash
cd demand-radar
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 自备 MongoDB 与 Ollama
docker run -d -p 27017:27017 --name mongo mongo:7
ollama pull qwen2.5:3b

python -m demand_radar run --limit 10          # 结果写到 out/demands.md
python -m demand_radar digest                  # 周报
```

运行测试（离线，不需要网络、MongoDB 或模型）：

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests
```

## Mac 上用 GPU 跑模型（可选）

Docker Desktop 在 Mac 上不能把 GPU 交给容器，容器里的模型只能用 CPU。想用 Apple GPU，让模型服务在 macOS 上原生运行（mlx-lm），容器只跑 MongoDB 和 app。实测 M5 上 Qwen3-4B 每条约 3 秒，比容器 CPU 里的 qwen2.5:3b（4–6 秒）快，而且评测 F1 高一档（见 [`docs/model-selection.md`](docs/model-selection.md)）。

```bash
# 1. 起模型服务（需要 Python 3.11+；端口别用 8080，Docker Desktop 占着）
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-4B-Instruct-2507-4bit --port 8765

# 2. 叠加 Mac GPU 的覆盖文件运行，其他照旧
docker compose -f docker-compose.yml -f docker-compose.mac-gpu.yml up
docker compose -f docker-compose.yml -f docker-compose.mac-gpu.yml run --rm app digest
```

换模型或端口：`MLX_MODEL=<模型名> MLX_PORT=<端口>` 放在命令前面。不用 Docker 跑 app 的话，直接给 `--provider local/<模型名> --base-url http://localhost:8765/v1`。任何在宿主机上跑的 OpenAI 兼容服务都能这样接（LM Studio、llama.cpp、原生 Ollama）。

## 用云模型（可选）

没有合适的机器时，可以换成云模型（需要 API key，会产生费用，数据会发给服务商）：

```bash
export OPENAI_API_KEY=sk-...
python -m demand_radar run --provider "openai/gpt-4o-mini"
```

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit` | 10 | 每个源最多抓多少条（RSS 按每个订阅、HN/GitHub 按每个查询计） |
| `--source` | `all` | 只跑部分源，如 `github,rss` |
| `--provider` | `ollama/qwen2.5:3b` | 模型，本地和云模型都用这一个参数切换；其他本地服务用 `local/<模型名>` |
| `--lang` | `zh` | 输出语言：`zh` / `en` |
| `--min-confidence` | 0.5 | 低于此可信度的需求不进清单 |
| `--workers` | 2 | 同时发给模型的请求数。Ollama 默认一次只处理一个请求，要在 ollama 服务上设 `OLLAMA_NUM_PARALLEL` 才真正并行；云模型直接生效 |
| `--days` | 7 | `digest`：汇总最近多少天 |
| `--pages` | 10 | `backfill`：本次最多往回拉多少页；`--limit` 在这里是每页条数，默认 100 |
| `--run-at` / `--digest-weekday` | `03:00` / 1 | `schedule`：每天几点跑、ISO 周几出周报（0 不出）；`--no-run-on-start` 启动时不先跑 |
| `--sources-file` | `sources.toml` | 数据源、过滤规则、排序权重的配置文件 |
| `--mongo-uri` / `--db-name` | `mongodb://localhost:27017` / `demand_radar` | 数据库 |
| `--base-url` | `http://localhost:11434/v1` | 本地模型服务地址（旧名 `--ollama-base-url` 仍可用） |

这些参数也都能用环境变量设置（Docker 部署时用）：`DR_SOURCE`、`DR_LIMIT`、`DR_PROVIDER`、`DR_OUTPUT_LANGUAGE`、`DR_MIN_CONFIDENCE`、`DR_WORKERS`、`DR_SOURCES_FILE`、`MONGO_URI`、`DR_DB_NAME`、`OLLAMA_BASE_URL`、`DR_OUTPUT`。

## 和同类项目的区别

GitHub 上已有一些同类项目，比如 [Reddit_Scrapper](https://github.com/Mohamedsaleh14/Reddit_Scrapper)、[pain-miner](https://github.com/AdvancingTitans/pain-miner)、[sift](https://github.com/Gitter09/sift)，都还在早期。

| | 需求雷达 | 同类项目普遍情况 |
|---|---|---|
| 模型 | 全本地，不需要 API key | 大多依赖付费 API |
| 部署 | `docker compose up` | 本地装 Python 环境，几乎都没有 Docker |
| 质量 | 有标注评测集，指标可复现 | 没有公开的评测方法 |
| 数据源 | HN、Reddit、StackExchange、GitHub issues | 大多只有 Reddit |

## 常见问题

**模型下载失败，报错里有 `redirect target not allowed … resolves to non-public 198.18.x.x`**

这是 Clash / mihomo 类代理开了 fake-ip 模式（TUN）的典型现象：容器里的域名被解析成 198.18.0.0/16 的假 IP，而 ollama 下载模型时要跳转到 Cloudflare 的存储域名，它检查到跳转目标不是公网 IP 就拒绝。网络本身是通的，只是这一道检查过不去。两种解法任选其一：

1. **在代理里把这个域名排除出 fake-ip**（推荐，一劳永逸）：在 fake-ip-filter 里加一条 `+.cloudflarestorage.com`。Clash Verge Rev 在「设置 → DNS 覆写」里改；其他客户端改配置文件里的 `dns.fake-ip-filter`。改完重新 `docker compose up`。
2. **给 ollama 容器写死真实 IP**：先用加密 DNS 查出真实地址（不会被劫持）：

   ```bash
   curl -s -H "accept: application/dns-json" \
     "https://1.1.1.1/dns-query?name=dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com&type=A"
   ```

   然后把 `docker-compose.yml` 里 ollama 服务下注释掉的 `extra_hosts` 两行打开，填上查到的 IP。这个 IP 可能会变，失效了重查一次即可。

**本地运行报 `No module named 'tomllib'`**：Python 版本低于 3.11，见上面「不用 Docker 本地运行」。

## 合规

只使用各平台的公开接口和 RSS，遵守各自的调用限额；Reddit 另有一路走 Arctic Shift 第三方归档，它的条款分析和需要你自己判断的点见 [`docs/arctic-shift.md`](docs/arctic-shift.md)。工具由你自己运行，请同时遵守各数据源平台的服务条款。生成的清单只包含归纳后的需求、一句原文摘录和原帖链接。

## 文档

| 文档 | 内容 |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | 路线图和最新评测结果 |
| [`docs/tech-plan.md`](docs/tech-plan.md) | 技术路线规划 |
| [`docs/landscape.md`](docs/landscape.md) | 同类项目调研 |
| [`docs/market.md`](docs/market.md) | 市场调研与潜力分析 |
| [`docs/model-selection.md`](docs/model-selection.md) | 模型选型（含 Mac GPU） |
| [`docs/arctic-shift.md`](docs/arctic-shift.md) | Arctic Shift（Reddit 数据源）调研与条款 |

## 目录结构

```
demand_radar/
  cli.py            # 入口：run / eval / digest / backfill / schedule 五个命令
  schedule.py       # 定时运行：每天 run、每周 digest
  config.py         # 运行配置（命令行 > 环境变量 > 默认值）
  sources/          # 数据源：softwarerecs、hn_algolia、github、arctic_shift、rss；cursor.py 增量游标
  filters.py        # 规则预过滤
  extract.py        # 模型抽取
  excerpt.py        # 从原文挑摘录
  models.py         # 数据结构
  store.py          # MongoDB 存储：条目状态、游标与回填进度、运行记录
  rank.py           # 综合排序
  report.py         # 清单与周报（中英文）
  evaluate.py       # 评测
eval/eval_set.json  # 评测标注集
tests/              # 离线单元测试
sources.toml        # 数据源、过滤规则、排序权重
docker-compose.yml  # app + mongo + ollama，可选的 scheduler（定时）
```

## 协议

[MIT](LICENSE)
