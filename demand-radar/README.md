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

## 快速开始

### 1. 装依赖

```bash
cd demand-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 准备本地模型（默认全本地，无需任何 API key）

```bash
# 安装 Ollama：https://ollama.com
ollama pull qwen2.5:7b     # 内存小可改用 qwen2.5:3b
```

### 2b. 准备 MongoDB

```bash
# 本地起一个（任选其一）
docker run -d -p 27017:27017 --name mongo mongo:7
# 或装本机 MongoDB 服务，默认连 mongodb://localhost:27017
```

### 3. 运行

```bash
python -m demand_radar run --limit 30
# 结果写到 out/demands.md，原始数据存 data/demands.db
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
| `--mongo-uri` | `mongodb://localhost:27017` | MongoDB 连接串 |
| `--db-name` | `demand_radar` | 数据库名 |
| `--output` | `out/demands.md` | 清单输出路径 |

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
```

## 状态

MVP 骨架。判断标准：自己看输出的清单，是否觉得「这些点子真能做」。是则加数据源、加聚类、加看板；否则调 prompt / schema / 过滤策略。
```
