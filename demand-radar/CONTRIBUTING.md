# 参与贡献

欢迎提 issue 和 PR。动手前请先读这一页：它写了这个项目不能丢的三件事、怎么搭开发环境、怎么加数据源，以及改动要过的检查。

## 三条底线

这三点是项目和同类工具最大的区别，任何改动都不能破坏：

1. **全本地模型**：默认路径不需要 API key、不花钱。云模型只能作为可选项。
2. **一条命令部署**：`docker compose up` 就能跑。新增依赖要说明对镜像体积和启动步骤的影响。
3. **质量可量化**：动了抽取相关的代码，要用评测命令跑出数字，并写进 PR。

## 开发环境

需要 Python 3.11 或更新。

```bash
cd demand-radar
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock -e ".[dev]"
```

每次提交前跑这两条。它们离线运行，不需要网络、MongoDB 或模型：

```bash
ruff check demand_radar tests docker
python -m unittest discover -s tests
```

CI 会在 Python 3.11 和 3.12 上各跑一遍，并检查打包和 Docker 镜像。

## 代码在哪

| 目录 / 文件 | 内容 |
|---|---|
| `demand_radar/sources/` | 数据源。`http.py` 是共用的请求函数，限速和重试都在这里 |
| `demand_radar/filters.py` | 规则预过滤 |
| `demand_radar/extract.py`、`excerpt.py`、`safety.py` | 模型抽取、原文摘录、有害规则 |
| `demand_radar/store.py` | MongoDB 读写 |
| `demand_radar/rank.py`、`cluster.py`、`report.py` | 排序、相似需求合并、清单和周报 |
| `demand_radar/api.py`、`web/` | Web 看板 |
| `eval/` | 标注数据：抽取 116 条，合并 23 对 |
| `docker/entrypoint.py` | 容器入口：等模型服务、拉模型、检查输出目录 |

## 加一个数据源

### 方式一：RSS / Atom 订阅，不写代码

在 `sources.toml` 末尾加一块：

```toml
[[rss]]
name = "v2ex/create"                          # 来源名，会显示在清单和看板里
url = "https://www.v2ex.com/feed/create.xml"
```

单独跑一下这个源，确认能抓到：

```bash
demand-radar run --source rss --limit 5
```

注意几点：

- **名字定了就别改**。去重的依据是「来源名 + 条目 id」，改名后同一批帖子会被当成新内容再处理一遍。
- **Reddit 订阅按 `reddit/<版块>` 命名**。这样和 `arctic_shift` 源抓到的同一条帖子会互相去重。
- **发布产品的站点**（例如 Product Hunt）发的是产品而不是需求，把名字加进 `[filters]` 的 `supply_sources`，整源跳过。
- **RSS 没有热度信息**，热度记为 0，排序会靠后。也不支持需要登录的订阅。
- **本地文件**：`url` 写成 `file:///绝对路径` 也能读。用 Docker 时要把文件挂载进容器。

### 方式二：写一个源模块

对方没有 RSS，或者你需要热度、增量抓取、历史回填时，写一个模块：

1. **新建模块**：在 `demand_radar/sources/` 下新建文件，实现 `fetch(limit, conf, cursors)`，返回 `list[SourceItem]`。
   - 必填：`source`（来源名）、`external_id`（源内唯一 id）、`title`、`text`、`url`。
   - 有热度填 `score`。评论填 `kind="comment"`，这时标题是所在帖子的标题。有发布时间填 `created_at`（Unix 秒）。
   - 请求一律用 `http.get` / `http.get_json`，**不要自己用 urllib 或 requests**。限速、限流冷却和重试都在里面。正文用 `http.clean` 去掉 HTML 并截断。
   - 单个查询失败时，只要还有别的查询成功，就跳过它继续抓。全部失败才抛异常。
2. **增量抓取**（可选）：接口能按时间过滤的话，用 `cursor.advance(cursors, 查询名, items)` 记录抓到哪里。游标由框架在整批处理完后统一落库，源里不要自己存。
3. **注册**：在 `sources/__init__.py` 的 `REGISTRY` 里注册，并在 `sources.toml` 加一个同名的配置段。
4. **历史回填**（可选）：实现 `units(conf)`、`page_before(conf, unit, before, pagesize)`、`interval()`，并登记到 `BACKFILL`。
5. **测试**：用一份裁剪过的真实响应做离线测试，参照 `tests/test_arctic_shift.py`。测试里不要发真实请求。
6. **评测样本**：给 `eval/eval_set.json` 补至少 10 条这个源的标注样本，然后跑 `eval`，确认抽取质量没有下降。

可以参照的写法：`hn_algolia.py` 最简单，`arctic_shift.py` 最完整。

### 对数据源要客气

- 只用公开接口和订阅。不绕过登录、限流或反爬。
- 站点有限额就按限额配间隔。已知站点的默认间隔在 `http.py` 的 `HOST_INTERVALS`，用户可以在 `sources.toml` 的 `[http]` 里调大。
- 新接一个平台前，先看它的服务条款和 robots.txt，把结论写进 PR。参考 `docs/arctic-shift.md` 的写法。

## 改了抽取相关的代码

`extract.py`、`filters.py`、`safety.py`、`excerpt.py`、`models.py` 和提示词都属于抽取相关代码。改动后要跑评测，把前后的数字写进 PR：

```bash
demand-radar eval --lang zh -v
demand-radar eval --lang en -v
```

要求：

- **F1 不低于改动前**。
- **有害需求进清单必须是 0**。
- **规则误杀必须是 0**。

动了相似需求合并（`cluster.py`）就跑 `demand-radar cluster --eval -v`。

**有害需求一律用规则判断，不要改成让模型判断。** 原因和实测数据写在 `safety.py` 开头。

## 更新依赖

依赖声明在 `pyproject.toml`，精确版本锁在 `requirements.lock`。Docker 镜像和 CI 都按锁文件安装。锁文件要在 Linux 里生成，macOS 上解析出来的版本可能不同：

```bash
docker run --rm -v "$PWD":/src:ro python:3.12-slim sh -c \
  'cp -r /src /build && cd /build && pip install -q ".[web]" && pip freeze --exclude demand-radar' \
  > requirements.lock.new
```

把文件头的注释保留下来，用新内容替换下面的版本列表。然后跑测试、构建镜像，再提交。

## 提交

- **提交信息**：用中文，第一行写清楚做了什么，例如 `feat: …`、`fix: …`、`docs: …`。
- **改一处同步一处**：改了用户能感知的行为，同步更新 `README.md`、`README.en.md` 和 `CHANGELOG.md`。
- **一个 PR 只做一件事**。
