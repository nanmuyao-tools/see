# 更好的数据源：现状产出率与候选源实测

调研日期：2026-10-28。出发点是一周验收（10-20～10-27）暴露的两个问题：一是每天只新增 1～3 条需求；二是清单头部几乎全是「给已有项目提的功能请求」，不是「有人想要一个还不存在的产品」。

「更好」按四条衡量：
① 未被满足、可做成产品的需求占比高；
② 能持续供给增量；
③ 有热度或互动信号可用来排序；
④ 获取方式和条款可以接受。

标注约定：`[一手]` 指官方文档、robots.txt、API 实测返回；`[二手]` 指第三方转述；`[推断]` 是我的推理；`[人工判断]` 是我逐条读样本得出的粗估，没有跑模型。

## 结论摘要

1. **头部被 GitHub 占满，主因是排序公式，不是缺数据源。** 热度项是 `log1p(score)`：一个 1500 赞的 issue 拿 7.3 分，HN 评论、StackExchange 和 Reddit 帖几乎都是 0 分。分项打分每差一档才 1～1.5 分，所以清单前 12 名全部是 GitHub。**这件事先修，比加源见效快**：按来源类型分榜，或者热度在各源内部归一化。
2. **增量少，是上游本身就少。** GitHub 源按设计不用游标，每天重抓同一份 19 条榜单，全是重复，8 天里只新增 1 条。softwarerecs 整个站最近 7 天只有 2 个新问题 `[一手]`。HN 现有 5 个短语 7 天合计只命中 12 条 `[一手]`。
3. **StackExchange 扩站没有意义。** superuser 每周 39 个新问题，askubuntu 66 个，其余站点个位数，标题含「is there」的几乎为 0 `[一手]`。
4. **推荐立刻做，零代码或小改动**：① 分榜 / 归一化热度；② HN 加 Ask HN（每周约 237 帖，先在本地按关键词筛）和两个短语；③ Mastodon `#askfedi` 和 V2EX「问与答」，这两个直接用现有 `rss` 源订阅就能接，只改 `sources.toml`。后两个的条款有不清楚的地方，要你确认。
5. **有条件**：Reddit（Arctic Shift，已实现，在库里产出率最高，等你定条款）；Bluesky 搜索（未登录能用，但接口脆弱、条款页拿不到）；GitHub Discussions（必须带 token）。
6. **不建议**：Product Hunt、Indie Hackers、Lobsters、AlternativeTo、Obsidian 论坛、知乎、小红书、即刻，以及 Canny / UserVoice 这类产品反馈站。原因是条款明确不允许、要 key，或者内容本质还是「给已有产品提需求」。
7. 粗估：按建议全部接入后，每周进清单的「新产品类」需求从现在的约 5 条升到 **30～60 条**。其中 V2EX 和 Mastodon 贡献最多，但这是按样本密度外推的 `[推断]`。

## 一、现状基线（MongoDB 实测，只读）

数据来自容器 `demand-radar-mongo-1`，库 `demand_radar`，查询 `demands` 和 `runs` 两个集合。「进清单」的条件和 `store._listed` 一致：`is_request ∧ buildable ∧ confidence ≥ 0.5 ∧ 未命中有害规则`。排序公式按 `rank.py` 的默认权重在 mongosh 里重算 `[一手]`。

### 1.1 各源产出率

| 来源 | 入库 | 抽取 | 判为需求 | 进清单 | 进清单 / 抽取 | 前 12 名占几席 | 10-20～10-27 新进清单 | 进清单的里「新产品类」`[人工判断]` |
|---|---|---|---|---|---|---|---|---|
| github | 19 | 19 | 17 | 12 | 63% | **12 / 12** | 1 | 约 0 / 12，全是给 VSCode、terraform、actions/runner 等提的功能 |
| hackernews | 69（过滤 1） | 68 | 30 | 19 | 28% | 0 | 4 | 约 8 / 19，另有几条是噪声（如「一个服务来跟踪 SWE-2 的表现」） |
| stackexchange/softwarerecs | 15 | 15 | 13 | 12 | **80%** | 0 | 4 | 约 10 / 12 |
| reddit/SomebodyMakeThis | 15（过滤 3） | 12 | 7 | 5 | 42% | 0 | 0（10-21 起关闭） | 约 4 / 5 |
| reddit/androidapps | 12 | 12 | 12 | 8 | 67% | 0 | 0（同上） | 约 6 / 8 |
| **合计** | 130 | 126 | 79 | 56 | 44% | | 9 | 约 28 / 56 |

清单数说明：上表是 56 条，`runs.listed` 最后一次是 55。差的 1 条是相似需求合并后折叠掉的那一对（每簇只列代表），两个数字并不矛盾。

### 1.2 每日增量（`runs` 集合，定时任务 03:00 UTC，`--limit 10`）

| 日期 | GitHub 抓到 | HN 抓到 | softwarerecs 抓到 | 真正要处理的（todo） |
|---|---|---|---|---|
| 10-21 | 19 | 4 | 2 | 6 |
| 10-22 | 19 | 1 | 1 | 2 |
| 10-23 | 19 | 2 | 0 | 2 |
| 10-24 | 19 | 2 | 失败（SSL EOF） | 2 |
| 10-25 | 19 | 1 | 2 | 3 |
| 10-26 | 19 | 2 | 0 | 2 |
| 10-27 | 19 | 1 | 0 | 1 |
| 10-28 | 失败 | 失败 | 失败 | — |

- GitHub 每天抓 19 条，todo 里没有一条来自它。`sources/github.py` 的注释写明「GitHub 不用游标」，每次重取 reactions 榜单 `[一手]`。它的「增量」只有老 issue 新冲进榜单的那种情况，8 天 1 条。
- 10-28 03:00 那一轮三个源全部 `SSL: UNEXPECTED_EOF_WHILE_READING`，10-24 softwarerecs 也失败过一次。这里只记录现象，可能和本机代理有关 `[推断]`，没有碰 `dr-weekly`。

### 1.3 为什么头部全是 GitHub

排序分 = clarity×1 + intensity×1.5 + feasibility×1 + log1p(热度)×1。

| | 典型热度 | 热度项得分 |
|---|---|---|
| GitHub 功能请求 | 500～1500 赞 | 6.2～7.3 |
| HN 评论 | Algolia 不给评论分数，恒为 0 | 0 |
| softwarerecs 新问题 | 0～1 票 | 0～0.7 |
| Reddit 新帖（Arctic Shift） | 36 小时内基本是 0～1 | 0～0.7 |

满分 5 分的分项打分，每差一档才 1～1.5 分。所以只要热度项不归一化，任何带大数字的源都会压在最上面。换别的源也一样：Product Hunt 的票数、Bluesky 的点赞会重演这个问题。

## 二、候选源总表

「每周量」是源头的原始量，「需求浓度」是人工读 ≤30 条样本的粗估，「预计每周进清单」= 量 × 浓度 × 能接入的比例，属于 `[推断]`。

| 候选 | 内容类型 | 实测每周量 | 需求浓度 `[人工判断]` | 预计每周进清单 | 获取方式 | key | 热度信号 | 条款 / robots | 接入成本 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| **排序修正（非数据源）** | — | — | — | 让现有约 28 条新产品类需求浮上来 | 改 `rank.py` / 清单分组 | — | — | — | 小 | **立刻做** |
| HN Ask HN 帖子 | 提问、求推荐、少量愿望 | 237 帖 `[一手]` | 约 3 / 30 | 10～20（先本地关键词筛） | Algolia `tags=ask_hn` | 否 | points、评论数 | 与现有 HN 源相同 | 小：`hn_algolia` 加一种查询模式 | **立刻接入** |
| HN 加短语 `wish someone would`、`does anyone know of` | 评论里的愿望 | 6 + 4 条 `[一手]` | 约 5 / 30 | 约 2 | 现有源 | 否 | 评论无分数 | 同上 | 零：改 `sources.toml` | **立刻接入** |
| HN 短语 `alternative to` | 产品发布、顺口提及 | 77 条 | **0 / 30** | 0 | — | — | — | — | — | 不建议 |
| Mastodon `#askfedi` 标签 | 求推荐、求助、少量愿望 | 约 140 条（30 条跨 36 小时） | 约 4 / 30 | 约 10～18 | 标签 RSS `mastodon.social/tags/askfedi.rss`（实测 200） | 否 | 公开 API 有收藏数，RSS 没有 | robots 只禁 AI 爬虫名单和几个路径；ToS 只约束注册用户，无抓取条款 `[一手]` | 零：加一个 `[[rss]]` | **有条件**：社区普遍反感抓取，见第五节 |
| V2EX「问与答」节点 | 中文求推荐、求助、吐槽 | 约 240 帖（50 条跨约 35 小时） | 约 5 / 30 | 约 20～40（中文） | Atom `v2ex.com/feed/qna.xml`（实测 200，一次 50 条） | 否 | 无（RSS 不含回复数） | JSON API 被 Cloudflare 挡（403）；robots.txt 和关于页都 403，**没拿到条款** | 零：加一个 `[[rss]]`，但每轮条数要放到 ≥50 | **有条件** |
| Reddit（Arctic Shift） | 愿望帖、求 app | 未在本次测（遵照 10-21 暂停） | 库内实测：抽取 24 条进清单 13 条 | 5～15 `[推断]` | 已实现 `arctic_shift` 源 | 否 | score（36 小时后才准） | 灰色，见 `docs/arctic-shift.md` | 零：改 `enabled = true` | **有条件**（等你定） |
| Bluesky 搜索 | 短帖愿望 | 「is there an app」约 14 条（29 条跨 14 天） | 约 8 / 29 | 每个短语约 3～4 | `api.bsky.app/xrpc/app.bsky.feed.searchPosts` | 否 | 点赞、回复数 | 官方限额页抓回为空，开发者指南 404；`public.api.bsky.app` 403；未登录翻页 403 `[一手]` | 中：新写源，约 80 行 | 有条件，优先级低 |
| GitHub Discussions（Ideas 分类） | 对项目的想法 | 未测 | — | — | 只有 GraphQL | **要 token**（官方文档原文）`[一手]` | 有 | GitHub ToS 同现有源 | 中 | 有条件：只作带 `GITHUB_TOKEN` 时的可选项 |
| StackExchange 其他站 | 技术问答 | softwarerecs 2、superuser 39、askubuntu 66、apple 10、android 9、unix 14、softwareengineering 4、webapps 3、hardwarerecs 3、ux 1 `[一手]` | — | ≈0 | 现有源加站 | 否 | 有 | 已在用 | 零 | 不建议：量太少 |
| Lemmy（lemmy.world `selfhosted`） | 发布、求助、求推荐 | 约 31 帖 | 约 2 / 28 | ≈2 | `/api/v3/post/list`，公开 | 否 | score | robots `Crawl-delay: 60`；ToS 无抓取条款，要求「good faith」`[一手]` | 中 | 不建议：量小 |
| Obsidian 论坛 `plugins-idea` | 插件愿望，几乎条条是需求 | 最新一帖 2026-07-07，**分类基本停更** | 约 25 / 30 | ≈0（存量 1772 帖） | Discourse JSON，robots 不禁 | 否 | 点赞、回复 | Obsidian ToS 禁止「spiders or other screen scraping … without the express consent」`[一手]` | 小 | 不建议 |
| GitHub `open-source-ideas/ideas` | 点子仓库 | 半年 10 个 issue | 低，多为 AI 生成长文 | ≈0 | 现有源改查询 | 否 | 有 | 同现有源 | 零 | 不建议 |
| Raycast 扩展请求（GitHub） | 扩展愿望 | 历史共 79 个标题含「extension request」 | — | ≈0 | 同上 | 否 | 有 | 同上 | 零 | 不建议：已不在 GitHub 收集 `[推断]` |
| Product Hunt | 产品发布和评论 | 未测 | 低 `[推断]`：发布帖为主 | — | API v2 | **要 token** | 票数 | API 文档原文「must not be used for commercial purposes」；网站有 Cloudflare 质询 `[一手]` | 中 | 不建议 |
| Indie Hackers | 创业讨论 | 未测 | — | — | 无公开 API | — | — | robots 禁 `/api/`，并按名字禁 ClaudeBot、GPTBot 等 AI 爬虫 `[一手]` | 高：要抓 HTML | 不建议 |
| Lobsters | 技术链接和讨论 | 未测 | — | — | — | — | — | robots `User-agent: * Disallow: /`，另有 `Content-Signal: ai-input=no` `[一手]` | — | 不建议 |
| AlternativeTo | 替代品目录 | 未测 | — | — | — | — | — | robots 禁 `/api/`、`/discussions/` `[一手]` | — | 不建议 |
| Canny / UserVoice / featurebase | 已有产品的反馈板 | 未测 | 和 GitHub 功能请求同类 | — | Canny API 要板主 key（无 key 实测 400）`[一手]` | 要 | 投票 | 各站不同 | 中 | 不建议：本质是问题二 |
| 应用商店、Chrome / VS Code 插件评论 | 已有产品的差评 | 见 App 评论调研 | 17% 说清了缺什么 | — | 非公开接口 | — | — | 苹果、Google 条款不利，见 `2026-10-20-app-store-reviews.md` | — | 维持默认关闭；插件市场没测 |
| 知乎 / 小红书 | 中文问答、种草 | 未测 | — | — | — | — | — | robots 对 `*` 都是 `Disallow: /` `[一手]` | — | 不建议 |
| 即刻 | 中文动态 | 未测 | — | — | 要登录 | — | — | — | — | 不建议 |
| 少数派 | 文章 | 未测 | 低：文章不是愿望 | — | 有文章 RSS | 否 | — | robots 只禁部分路径 | 零 | 不建议：内容类型不对 |
| YouTube / 播客评论 | 评论 | 未测 | — | — | YouTube Data API 要 key `[推断]` | 要 | 有 | 未查 | 中 | 不建议进默认路径 |

## 三、实测记录

所有请求都是公开、未登录的只读请求。UA 用 `demand-radar-research/0.1`，每次间隔 1.2～3 秒。没有绕过任何 Cloudflare 质询或鉴权，被挡住的就记为挡住。脚本在 scratchpad，没有进仓库。

| 目标 | 请求数 | 结果 |
|---|---|---|
| HN Algolia | 16 | 12 个短语各查 7 天命中数；Ask HN 7 天数；抽了 3 批各 30 条样本 |
| StackExchange API | 30 | 10 个站各查 7 天总量和标题命中数 |
| 各站 robots.txt | 约 20 | Product Hunt、V2EX 返回 Cloudflare 质询页 |
| Lemmy | 3 | 1 次拉帖，2 次社区搜索（结果都为空） |
| Bluesky | 7 | `public.api` 403 两次；`api.bsky.app` 搜索 200；带 cursor 翻页 403；`since` 过滤可用 |
| Mastodon | 4 | 全文搜索未登录返回空数组；标签时间线 200；ToS 接口 2 次 |
| Obsidian 论坛（Discourse） | 3 | 分类列表，以及按创建时间排序的 `plugins-idea` |
| GitHub | 6 | 查 Raycast 标签和搜索，查 open-source-ideas；GraphQL 未登录返回限流提示（匿名额度已被前几次用完），没测出鉴权结论，改以文档为准 |
| V2EX | 6 | `/api/topics/latest.json` 403；`/feed/qna.xml` 200（50 条）；`/feed/ideas.xml` 200；robots、about 都 403 |
| Canny | 1 | 无 key 调 `posts/list` 返回 400 |
| **合计** | 约 96 | |

### 3.1 HN：现有短语、候选短语、Ask HN

7 天命中数，`search_by_date`，`tags=(story,comment)`，统计时间 2026-10-28：

| 短语 | 7 天命中 | 在用？ |
|---|---|---|
| I wish there was | 7 | 是 |
| is there a tool | 0 | 是 |
| is there an app | 0 | 是 |
| someone should build | 0 | 是 |
| would pay for | 5 | 是 |
| wish someone would | 6 | 否 |
| does anyone know of | 4 | 否 |
| looking for a tool | 1 | 否 |
| I'd pay for | 1 | 否 |
| why is there no / is there any software | 0 / 0 | 否 |
| alternative to | 77 | 否 |
| Ask HN 帖子（`tags=ask_hn`） | 237 | 否 |

样本 `[人工判断]`：

- **`wish someone would`**：30 条里约 5 条可算产品愿望，其余是牢骚或修辞。
  - 「I wish someone would reverse engineer Plex and copy its protocols to Jellyfin so you can use all the Plex Tv clients but on a Jellyfin server」
  - 「I wish someone would revisit the idea of a single, global, P2P file search mesh like Gnutella/Gnutella2 but using a modern P2P stack」
  - 「I wish someone would do this for Apple Music. Their app is dreadful!」
- **`alternative to`**：30 条里 0 条是需求，几乎都是「Show HN: XX 的开源替代」和评论里顺口一提。不要加。
- **Ask HN 最近 7 天的前 30 帖**：约 3 条接近需求，其余是讨论、吐槽 HN 本身、广告码。
  - 「Ask HN: Do u think there should be a auto recovery tool for agents? … devs need to write checkpointers, recovery logic, and deal with idempotency by theirselves」
  - 「Ask HN: Has anyone used Phoenix, Langfuse, or OpenObserve? What worked well, and what issues…」（求对比，弱需求）

  Ask HN 量大、浓度低，建议先在本地按关键词筛（is there / looking for / tool / app / recommend / should there be），再交给模型。帖子带 points，有热度信号。

### 3.2 StackExchange 各站 7 天新问题数

`/2.3/questions?fromdate=<7 天前>&filter=total`，加上 `search/advanced?title=is there`：

| 站 | 7 天新问题 | 标题含「is there」 |
|---|---|---|
| softwarerecs | **2** | 0 |
| superuser | 39 | 0 |
| askubuntu | 66 | 0 |
| apple | 10 | 1 |
| android | 9 | 0 |
| unix | 14 | 0 |
| softwareengineering | 4 | 0 |
| webapps | 3 | 0 |
| hardwarerecs | 3 | 0 |
| ux | 1 | 0 |

`[推断]`：StackExchange 整体新问题量已经很低，再加站也补不了量。softwarerecs 浓度最高（库内 80% 进清单），继续保留，但别指望它供量。

### 3.3 Mastodon `#askfedi`

标签时间线 30 条，跨约 36 小时 → 每周约 140 条。约 4 条接近产品需求 `[人工判断]`：

- 「What do y'all use for recipe organization? I copy and paste to Notes and the wife still uses Pinterest. You can't rely on links and AI slop recipes are everywhere.」
- 「Anywone know a reliable way to bridge Mastodon dm's into Matrix? It's litteraly the only direct comunication thing we have to open another app for」
- 「anyone have a recommendation for a #vacuumrobot ? Ideally with no app, cloud and AI bullshit.」（硬件，个人做不了）

其余是生活提问、政治、求书单。全文搜索未登录时返回空数组 `{"statuses":[]}` `[一手]`，所以只能按标签订阅。标签 RSS `https://mastodon.social/tags/askfedi.rss` 返回 200（`application/rss+xml`），现有 `rss` 源可以直接吃。

### 3.4 V2EX「问与答」

Atom feed 一次 50 条，30 条跨约 21 小时 → 每周约 240 帖。约 5 条是可做成产品或服务的需求 `[人工判断]`：

- 「请教一下使用 GPT 开发网页、APP，怎么提升 UI 美感 … 有什么 skill 或者其他的可以扔给他参考」
- 「有没有哪家支持中转站+代充+开专票呢？求问一下，找了好多都找不到三方能集齐的提供商」
- 「国产各大品牌手机的系统日历 App 对于 ics 日历订阅功能 支持的怎么样？… webcal 开头的链接在 iOS 的 Safari 里可以直接弹窗提示订阅。但是安卓支持这种吗？」

其余是生活、情感、运营商、求购。feed 里没有回复数，热度只能记 0。另测了「奇思妙想」节点（`/feed/ideas.xml`）：30 条跨 5 周多，多数是空谈和推广，不建议。

注意：定时任务的 `DR_LIMIT=10` 对 RSS 是「每个订阅取前 10 条」。按每天约 34 帖的量，每天只跑一次会漏掉大半。接入时这个订阅要单独放大条数（见第六节）。

### 3.5 Bluesky 搜索

- `public.api.bsky.app` 的 `searchPosts` 返回 403，响应头 `server: BunnyCDN` `[一手]`。
- `api.bsky.app` 未登录返回 200 `[一手]`。
- 带 `cursor` 翻第二页返回 403 `[一手]`。第三方文章说 2026-07 起未登录搜索不能翻页 `[二手]`，和实测一致。
- `since=` 时间过滤可用，所以按天增量能做，但每个查询每轮只能拿第一页（≤100 条）。

「"is there an app"」英文最新 29 条跨 14 天，约 8 条是需求 `[人工判断]`：

- 「Is there an app that redirects u to a given wiki when u try to visit the fandom wiki if it knows one exists」（已有 Indie Wiki Buddy，说明需求真实但已被满足）
- 「Is there an app that does what Seagate Seatools does (drive info, testing, etc) that doesnt suck shit?」
- 「Is there an app or website that will show me photos of common birds in my area so I have a cheat sheet to get started?」

帖子短、上下文少，带点赞数。官方限额页 `bsky.network/docs/advanced-guides/rate-limits` 抓回为空；`bsky.social/about/support/developer-guidelines` 404；ToS 正文里没有自动化访问条款。网上有「每 IP 5 分钟 3000 次」的说法，只有一个来源且没引官方 `[二手，未核实]`。

### 3.6 Lemmy `selfhosted@lemmy.world`

去掉 2 条置顶，28 条跨 6.3 天 → 每周约 31 帖。约 2 条是需求（「what is a good self hostable alternative to facebook messenger」「looking for cloud file storage」），而且都已有现成方案。其余是发布、教程、漏洞讨论。社区搜索 API 两次都返回空。robots `Crawl-delay: 60` `[一手]`。

### 3.7 Obsidian 论坛 `plugins-idea`

- 按创建时间倒序的前 30 帖，约 25 条是清楚的插件愿望，例如「Markdown Tables with Spreadsheet-Like Formatting — Without Turning Every Row into a Note」「Management of scanned paper documents」。
- 但最新一帖是 **2026-07-07**，前一帖 2026-06-08，分类基本停更，没有增量。
- 存量 1772 帖做一次性回填浓度很高，但 Obsidian ToS 禁止未经同意的爬取（原文见第二节），不建议。
- 同一类「插件 / 扩展生态里的空白」值得留意：个人开发者正好能做，又不是「给已有项目提功能」。可惜这次没找到条款友好、还在活跃的此类来源。

## 四、推荐排序与接入顺序

打分是 1～3 的粗档 `[推断]`，越高越好；条款风险越低分越高。

| 顺序 | 做什么 | 需求浓度 | 增量 | 条款 | 接入成本 | 预计每周进清单 | 分榜归属 |
|---|---|---|---|---|---|---|---|
| 1 | **分榜 + 热度按源归一化** | — | — | 3 | 3 | 不加数据，让现有新产品类需求进头部 | — |
| 2 | HN 加两个短语 | 2 | 1 | 3 | 3 | 约 2 | 愿望 |
| 3 | HN Ask HN + 本地关键词筛 | 1 | 3 | 3 | 2 | 10～20 | 愿望 / 求推荐 |
| 4 | V2EX「问与答」RSS | 2 | 3 | 1（没拿到条款） | 3 | 20～40 | 愿望 / 求推荐（中文） |
| 5 | Mastodon `#askfedi` RSS（可再加 `#askmastodon` 等，未测） | 1 | 2 | 2 | 3 | 10～18 | 愿望 / 求推荐 |
| 6 | Reddit（Arctic Shift，恢复） | 3 | 2 | 1 | 3 | 5～15 | 愿望 |
| 7 | Bluesky 搜索（新源） | 2 | 2 | 1 | 1 | 每个短语 3～4 | 愿望 |
| — | GitHub（保留） | 1（对「新产品」而言） | 1 | 3 | — | ≈0 | **单独一榜：已有项目的功能请求** |

### 分榜建议

| 榜 | 放什么 | 来源 |
|---|---|---|
| 想要一个新工具 | 愿望帖、「有没有能做 X 的工具」、求推荐但没人给出满意答案的 | softwarerecs、HN、Ask HN、Reddit、V2EX、Mastodon、Bluesky |
| 生态空白 | 插件 / 扩展愿望 | 目前没有合适的活跃来源，先空着 |
| 已有项目的功能请求 | 给具体开源项目或平台提的功能 | GitHub、以后的 Discussions |

- 最省事的是按来源分：GitHub 一榜，其他一榜。
- 更准的是让抽取多输出一个字段，区分「对已有产品的请求」和「想要新东西」。116 条评测集里 GitHub 那批已经在区分「平台功能请求」和「给开源项目提的请求」，可以在这个基础上加。但这会改提示词，要过 `eval`。
- 热度归一化可以先做最简单的一版：热度项按源内分位数算，或者每个源单独设 `heat` 权重。`[ranking]` 已经支持覆盖权重，但现在是全局的，不分源。

## 五、需要用户判断的点

1. **V2EX**：feed 公开可读，但 robots.txt 和关于页都被 Cloudflare 挡住（403），没看到条款原文。程序每天读一次 Atom feed，算不算正常订阅，由你判断。JSON API 被挡，不建议绕。
2. **Mastodon**：mastodon.social 的 ToS 只写给注册用户，没有抓取条款；robots 只禁一份 AI 爬虫名单，并写了「You are not allowed to use the contents of this website for LLM training」。我们是本地推理不是训练，但联邦宇宙社区整体反感被抓取和被 AI 处理 `[推断]`，依据是 robots 注释和 Lobsters、Indie Hackers 同类做法。要不要接，看你对社区观感的容忍度。
3. **Reddit**：结论不变，见 `docs/arctic-shift.md`。新信息：2025-11 Reddit 发布 Responsible Builder Policy，新申请官方 API 要先审批，不再自助 `[二手]`（来源是 r/redditdev 公告的第三方镜像，没拿到官方原文）。所以官方 API 这条路对「一条命令部署」更不现实，Arctic Shift 仍是唯一零 key 方案。
4. **Indie Hackers**：robots 按名字禁了 ClaudeBot、GPTBot 等 AI 爬虫，没禁通用 UA。我们这个「抓下来给本地模型抽取」的工具算不算 AI 爬虫，没有定论。加上没有 API，本文已归为不建议。如果你想要它，需要你先判断这一点。
5. **GITHUB_TOKEN**：GitHub Discussions 只能走 GraphQL，官方文档写明「This API is available for authenticated users, OAuth apps, and GitHub Apps」。要接就只能做成「配了 token 才启用」的可选源，和「默认零 key」不冲突，但会多一条配置说明。

## 六、建议的路线图改动

只是建议，ROADMAP.md 没改，由主会话决定。

1. 在「进行中」加一项：**清单分榜 + 热度按源归一化**，排在任何新数据源前面。验收：清单前 20 名里非 GitHub 来源 ≥ 一半；`eval` 不受影响（只动排序，不动抽取）。
2. `hn_algolia` 加 Ask HN 模式（`tags=ask_hn` + 本地关键词筛）；`sources.toml` 的 HN 查询加 `wish someone would`、`does anyone know of`。
3. `rss` 源支持按订阅设置条数（例如 `[[rss]] limit = 50`），否则 V2EX、Mastodon 这类每天几十帖的 feed 在 `--limit 10` 下会漏。接入 V2EX「问与答」、Mastodon `#askfedi`，**等你确认第五节 1、2 两点后再开**。
4. 「待定事项」里「V2EX 按关键词过滤」改为：V2EX 用节点 feed（问与答），不走 API；中文源里知乎、小红书 robots 全禁，即刻要登录，不再考虑。
5. Reddit 恢复与否仍按 `docs/arctic-shift.md` 由你定。验收窗口已于 10-27 结束，技术上随时可开。
6. 定时任务 10-24、10-28 出现 `SSL: UNEXPECTED_EOF`，10-28 三个源全挂。建议查一下容器出网（代理）是否稳定，否则任何新源都会受影响。

## 七、本次改动的其他文件

- `docs/market.md`「待核实」第二条：补上本次 V2EX、即刻等中文源的部分结论，并指向本文。

## 参考链接（访问日期均为 2026-10-28）

- HN Algolia API：https://hn.algolia.com/api
- StackExchange API：https://api.stackexchange.com/docs
- Bluesky `searchPosts`：https://docs.bsky.app/docs/api/app-bsky-feed-search-posts ；限额页（抓回为空）：https://bsky.network/docs/advanced-guides/rate-limits ；ToS：https://bsky.social/about/support/tos
- Bluesky 未登录搜索不能翻页的第三方说法 `[二手]`：https://dev.to/cooki/nobody-is-monitoring-bluesky-so-i-built-a-mentions-scraper-for-it-19mi
- Mastodon 标签 RSS：https://mastodon.social/tags/askfedi.rss ；ToS：https://mastodon.social/terms-of-service （正文经 `/api/v1/instance/terms_of_service` 获取）；robots：https://mastodon.social/robots.txt
- V2EX 节点 feed：https://www.v2ex.com/feed/qna.xml
- Lemmy API：https://lemmy.world/api/v3/post/list ；lemmy.world ToS：https://legal.lemmy.world/tos/ ；robots：https://lemmy.world/robots.txt
- Obsidian 论坛：https://forum.obsidian.md/c/plugins-idea/10 ；Obsidian ToS：https://obsidian.md/terms ；robots：https://forum.obsidian.md/robots.txt
- GitHub Discussions GraphQL：https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
- Product Hunt API v2：https://api.producthunt.com/v2/docs
- robots.txt：https://lobste.rs/robots.txt 、https://www.indiehackers.com/robots.txt 、https://alternativeto.net/robots.txt 、https://www.zhihu.com/robots.txt 、https://www.xiaohongshu.com/robots.txt 、https://sspai.com/robots.txt
- Reddit Responsible Builder Policy 公告（第三方镜像）`[二手]`：https://r.datuan.dev/r/redditdev/comments/1oug31u/introducing_the_responsible_builder_policy_new/nobt04v
- 项目内：`demand_radar/rank.py`、`demand_radar/sources/github.py`、`demand_radar/sources/rss.py`、`docs/arctic-shift.md`、`docs/research/2026-10-20-app-store-reviews.md`
