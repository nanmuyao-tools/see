# 需求雷达 MVP

抓取社区里用户对工具的吐槽 / 许愿，用本地 LLM 归纳成结构化「需求」，输出按可信度排序的清单。

方案与选型见 [`../docs/ideas/demand-radar.md`](../docs/ideas/demand-radar.md) 和 [`../docs/ideas/demand-radar-tech.md`](../docs/ideas/demand-radar-tech.md)。

## v0 做了什么

```
Hacker News 官方 API 拉 Ask HN 帖子
  → Instructor + 本地 Qwen(Ollama) 抽取成 Demand
  → 存入 MongoDB
  → 过滤噪音（buildable / confidence）
  → 输出 Markdown 需求清单
```

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
docker compose run --rm app --limit 50 --min-confidence 0.6
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

python -m demand_radar run --limit 30   # 结果写到 out/demands.md
```

## 切换到云模型（低配机器先验证效果时）

```bash
export OPENAI_API_KEY=sk-...
python -m demand_radar run --provider "openai/gpt-4o-mini" --limit 30
```

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit` | 30 | 抓取并分析多少条 |
| `--provider` | `ollama/qwen2.5:7b` | Instructor 的模型标识，一处切换本地/云 |
| `--source` | `hackernews` | 数据源（v0 仅 HN） |
| `--min-confidence` | 0.5 | 低于此可信度的需求被过滤 |
| `--ollama-base-url` | `http://localhost:11434/v1` | 本地模型服务地址 |
| `--mongo-uri` | `mongodb://localhost:27017` | MongoDB 连接串 |
| `--db-name` | `demand_radar` | 数据库名 |
| `--output` | `out/demands.md` | 清单输出路径 |

命令行参数也可用环境变量设置（Docker 用）：`DR_LIMIT`、`DR_PROVIDER`、
`DR_MIN_CONFIDENCE`、`OLLAMA_BASE_URL`、`MONGO_URI`、`DR_DB_NAME`、`DR_OUTPUT`。

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
    hackernews.py   # Hacker News 官方 API
Dockerfile
docker-compose.yml  # app + mongo + ollama 一键编排
docker/entrypoint.py
```

## 状态

MVP 骨架。判断标准：自己看输出的清单，是否觉得「这些点子真能做」。是则加数据源、加聚类、加看板；否则调 prompt / schema / 过滤策略。
