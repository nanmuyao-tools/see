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

会起三个容器：**app**（抓取 + 抽取）、**mongo**（存储）、**ollama**（本地模型）。跑完一轮 app 自动退出，结果在 **`./out/demands.md`**。

之后常用的命令：

```bash
docker compose run --rm app                        # 再抓一轮（已分析过的不会重复处理）
docker compose run --rm app digest                 # 生成最近 7 天的周报 → out/digest-<日期>.md
docker compose run --rm app digest --days 14 --lang en   # 最近 14 天，英文周报
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
```

## 数据源

全部是公开接口，不需要注册。在 [`sources.toml`](sources.toml) 里配置，加源只改这个文件。

| 源 | 内容 | 热度 | 限额 |
|---|---|---|---|
| `softwarerecs` | StackExchange 软件推荐站：「有没有能做 X 的软件」 | 投票数 | 300 次/天；设 `SE_API_KEY` 提到 1 万 |
| `hn_algolia` | Hacker News 帖子和评论，按 "I wish there was" 等短语搜索 | 得分 | 宽松 |
| `github` | 点赞多的功能请求 issue | 👍 数 | 10 次/分钟；设 `GITHUB_TOKEN` 提到 30 |
| `rss` | 任意 RSS / Atom 订阅，默认配了 Reddit 的 r/SomebodyMakeThis 和几个板块内搜索 | — | 视站点 |

某个源失败（网络、限流）只跳过它，其他源照常跑。限流时会按服务器要求等待后自动重试。

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

Docker Desktop 在 Mac 上不能把 GPU 交给容器，容器里的模型只能用 CPU。想用 Apple GPU，可以让模型服务在 macOS 上原生运行，MongoDB 仍然用 Docker。任何 OpenAI 兼容的本地服务都能接（mlx-lm、LM Studio、llama.cpp），provider 写成 `local/<模型名>`：

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen2.5-3B-Instruct-4bit --port 8080

python -m demand_radar eval --provider local/mlx-community/Qwen2.5-3B-Instruct-4bit \
  --base-url http://localhost:8080/v1
```

模型选型的调研见 [`docs/model-selection.md`](docs/model-selection.md)。

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
| `--days` | 7 | `digest`：汇总最近多少天 |
| `--sources-file` | `sources.toml` | 数据源、过滤规则、排序权重的配置文件 |
| `--mongo-uri` / `--db-name` | `mongodb://localhost:27017` / `demand_radar` | 数据库 |
| `--base-url` | `http://localhost:11434/v1` | 本地模型服务地址（旧名 `--ollama-base-url` 仍可用） |

这些参数也都能用环境变量设置（Docker 部署时用）：`DR_SOURCE`、`DR_LIMIT`、`DR_PROVIDER`、`DR_OUTPUT_LANGUAGE`、`DR_MIN_CONFIDENCE`、`DR_SOURCES_FILE`、`MONGO_URI`、`DR_DB_NAME`、`OLLAMA_BASE_URL`、`DR_OUTPUT`。

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

只使用各平台的公开接口和 RSS，遵守各自的调用限额。工具由你自己运行，请同时遵守各数据源平台的服务条款。生成的清单只包含归纳后的需求、一句原文摘录和原帖链接。

## 文档

| 文档 | 内容 |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | 路线图和最新评测结果 |
| [`docs/tech-plan.md`](docs/tech-plan.md) | 技术路线规划 |
| [`docs/landscape.md`](docs/landscape.md) | 同类项目调研 |
| [`docs/market.md`](docs/market.md) | 市场调研与潜力分析 |
| [`docs/model-selection.md`](docs/model-selection.md) | 模型选型（含 Mac GPU） |

## 目录结构

```
demand_radar/
  cli.py            # 入口：run / eval / digest 三个命令
  config.py         # 运行配置（命令行 > 环境变量 > 默认值）
  sources/          # 数据源：softwarerecs、hn_algolia、github、rss
  filters.py        # 规则预过滤
  extract.py        # 模型抽取
  excerpt.py        # 从原文挑摘录
  models.py         # 数据结构
  store.py          # MongoDB 存储与处理状态
  rank.py           # 综合排序
  report.py         # 清单与周报（中英文）
  evaluate.py       # 评测
eval/eval_set.json  # 评测标注集
tests/              # 离线单元测试
sources.toml        # 数据源、过滤规则、排序权重
docker-compose.yml  # app + mongo + ollama
```

## 协议

[MIT](LICENSE)
