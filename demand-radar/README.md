# 需求雷达 MVP

抓取社区里用户对工具的吐槽 / 许愿，用本地 LLM 归纳成结构化「需求」，输出按可信度排序的清单。

方案与选型见 [`../docs/ideas/demand-radar.md`](../docs/ideas/demand-radar.md) 和 [`../docs/ideas/demand-radar-tech.md`](../docs/ideas/demand-radar-tech.md)。

## 做了什么

```
多个公开数据源（见下表，在 sources.toml 配置）
  → Instructor + 本地 Qwen(Ollama) 抽取成 Demand
  → 存入 MongoDB（按来源去重，已分析过的不再重复调用模型）
  → 过滤噪音（is_request / buildable / confidence）
  → 输出按可信度、热度排序的 Markdown 需求清单
```

## 数据源

全部是公开接口，不需要注册。配置在 [`sources.toml`](sources.toml)，加源只改这个文件。

| 源 | 内容 | 热度字段 | 限额 |
|---|---|---|---|
| `softwarerecs` | StackExchange 软件推荐站：「有没有能做 X 的软件」 | 投票数 | 300 次/天；设 `SE_API_KEY` 提到 1 万 |
| `hn_algolia` | Hacker News 帖子和评论，按 "I wish there was" 等短语搜索 | 得分 | 宽松 |
| `github` | 点赞多的 feature request issue | 👍 数 | 10 次/分钟；设 `GITHUB_TOKEN` 提到 30 |
| `rss` | 任意 RSS / Atom 订阅，默认配了 Reddit 的 r/SomebodyMakeThis 和几个板块内搜索 | — | 视站点 |

某个源失败（网络、限流）只跳过它，其他源照常跑。只跑部分源：`--source github,rss`。

## 快速开始（推荐：Docker，一条命令）

只需装好 [Docker](https://docs.docker.com/get-docker/)，不用自己装 Python、MongoDB、Ollama：

```bash
cd demand-radar
docker compose up        # 首次会拉取模型（较慢），之后走缓存
```

三个容器自动起好：**app**（抓取+抽取）、**mongo**（存储）、**ollama**（本地模型）。
跑完一轮后 app 退出，结果在 **`./out/demands.md`**，全程本地、不花钱、数据不出本机。

再跑一次 / 改参数：

```bash
docker compose run --rm app --limit 20 --min-confidence 0.6
```

想提高 GitHub / StackExchange 限额，运行前设置环境变量即可（可选）：

```bash
export GITHUB_TOKEN=ghp_...     # GitHub 个人 token，无需任何权限
export SE_API_KEY=...           # https://stackapps.com/apps/oauth/register
docker compose up
```

> 低配机器提示：CPU 跑 7B 模型较慢。可在 `docker-compose.yml` 把 `OLLAMA_MODEL`
> 和 `DR_PROVIDER` 改成 `qwen2.5:3b`；或已装本机 Ollama 时注释掉 ollama 服务、
> 把 `OLLAMA_API`/`OLLAMA_BASE_URL` 指向宿主机。

## 本地开发模式（不走 Docker）

```bash
cd demand-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 自备 MongoDB 与 Ollama
docker run -d -p 27017:27017 --name mongo mongo:7
ollama pull qwen2.5:7b        # 内存小可改 qwen2.5:3b

python -m demand_radar run --limit 10   # 结果写到 out/demands.md
```

运行测试（离线，不需要网络、MongoDB 或模型）：

```bash
python -m unittest discover -s tests
```

## 切换到云模型（低配机器先验证效果时）

```bash
export OPENAI_API_KEY=sk-...
python -m demand_radar run --provider "openai/gpt-4o-mini" --limit 30
```

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit` | 10 | 每个源最多抓多少条（RSS 按每个订阅、HN/GitHub 按每个查询计） |
| `--provider` | `ollama/qwen2.5:7b` | Instructor 的模型标识，一处切换本地/云 |
| `--source` | `all` | `all` = sources.toml 里启用的全部源；也可写 `github,rss` |
| `--sources-file` | `sources.toml` | 数据源配置文件 |
| `--min-confidence` | 0.5 | 低于此可信度的需求被过滤 |
| `--ollama-base-url` | `http://localhost:11434/v1` | 本地模型服务地址 |
| `--mongo-uri` | `mongodb://localhost:27017` | MongoDB 连接串 |
| `--db-name` | `demand_radar` | 数据库名 |
| `--output` | `out/demands.md` | 清单输出路径 |

命令行参数也可用环境变量设置（Docker 用）：`DR_SOURCE`、`DR_SOURCES_FILE`、`DR_LIMIT`、
`DR_PROVIDER`、`DR_MIN_CONFIDENCE`、`OLLAMA_BASE_URL`、`MONGO_URI`、`DR_DB_NAME`、`DR_OUTPUT`。

## 目录结构

```
demand_radar/
  cli.py            # 入口与编排
  config.py         # 运行配置
  models.py         # Demand 数据结构（Pydantic）
  extract.py        # Instructor + 本地/云模型抽取
  store.py          # MongoDB 存储
  report.py         # Markdown 清单输出
  sources/
    __init__.py     # 注册表、读取 sources.toml、逐源抓取（单源失败不影响其他）
    http.py         # 共用的请求与文本清洗
    softwarerecs.py # StackExchange
    hn_algolia.py   # Hacker News 全文搜索
    github.py       # GitHub issues
    rss.py          # 通用 RSS / Atom
tests/              # 离线单元测试，fixture 取自各源真实返回
sources.toml        # 数据源配置
Dockerfile
docker-compose.yml  # app + mongo + ollama 一键编排
docker/entrypoint.py
```

## 状态

MVP 骨架。判断标准：自己看输出的清单，是否觉得「这些点子真能做」。是则加数据源、加聚类、加看板；否则调 prompt / schema / 过滤策略。
