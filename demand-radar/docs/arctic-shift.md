# Arctic Shift 作为 Reddit 数据源：调研与结论

更新：2026-10-19。路线图 v0.2 的「实测 Arctic Shift」一项。本文回答：它是什么、能不能用、条款允不允许、怎么接。

## 结论：有条件接入，已实现为 `arctic_shift` 源，RSS 保留做兜底

- **能用**：API 公开、无需 key、无需注册；30 次只读请求没遇到一次 429（RSS 今天实跑又被限）；能按版块 + 时间范围取帖，能按时间往回翻历史；字段齐（标题、正文、时间、score、评论数、版块、id）。
- **三个条件**：
  1. 它是一个人维护的免费服务，文档原话「No uptime or performance guarantees :)」——不能作为 Reddit 的唯一来源，RSS 订阅保留兜底，两边按同一 id 去重。
  2. 服务端关键词搜索（`title=` / `query=`）实测 4 次全部超时，活跃版块的 `before` 翻页也两次超时——按版块拉全量、关键词在本地筛；回填对活跃版块可能走不通，小版块正常。
  3. 条款是灰色地带：Arctic Shift 自己没有任何使用条款，Reddit 的政策管的是直接访问 reddit.com 和使用官方 API 的人，对「二手归档的下游使用者」没有明文——**是否可接受由你判断**，第四节列了具体要点。

## 一、它是什么

| 项 | 情况 |
|---|---|
| 项目 | [github.com/ArthurHeitmann/arctic_shift](https://github.com/ArthurHeitmann/arctic_shift)，1,686★，2023-08 创建，最近一次推送 2026-10-11（活跃） |
| 维护者 | 个人（Arthur Heitmann），靠捐赠；联系方式是邮箱 / Discord / GitHub issue |
| 定位 | README：「Makes Reddit data accessible to researchers, moderators, and others, through large dumps, an API, or a web interface」。Pushshift 2023 年被 Reddit 切断后的继任者之一 |
| 数据来源 | `file_content_explanations.md` 原话：「All data is retrieved through the official reddit API」；私有和隔离（quarantined）版块不含 |
| 延迟 | 维护者在 [issue #2](https://github.com/ArthurHeitmann/arctic_shift/issues/2)：「there's only about a 15s delay between when a thing was created and when it was archived」。实测 10 条样本 `retrieved_on − created_utc` = 14–28 秒 |
| 二次抓取 | 2023-11 起每条内容 36 小时后再抓一次，更新 `score`、`num_comments`、`upvote_ratio` 等；API 从 2024-05 起返回更新后的值。**刚发的帖 score 基本是 0 或 1**，热度要等 36 小时后才准 |
| 协议 | 仓库**没有 LICENSE 文件**，代码和数据都没有声明协议 |
| 删除 | 提供 Google 表单供用户申请移除自己的内容；网站可按用户名查自己的数据是否被收录 |

## 二、API：公开、无 key、限额动态

文档只有一份：[`api/README.md`](https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md)。Base URL `https://arctic-shift.photon-reddit.com`，我们只用 `/api/posts/search`：

| 参数 | 说明（文档 + 实测） |
|---|---|
| `subreddit` | 版块名 |
| `after` / `before` | 发布时间范围。接受 Unix 秒、毫秒、ISO 日期、相对偏移（`7d`）。**实测两者都不含端点**（`before=X` 不返回 `created_utc == X` 的那条），拼游标不用 ±1 |
| `sort` | `asc` / `desc`，按 `created_utc` |
| `limit` | 1–100，默认 25；`auto` 视服务器负载给 100–1000（实测给了 100） |
| `fields` | 只返回这些字段，文档建议用它减小响应、避免超时。**只接受白名单**：帖子可选 `author, author_fullname, author_flair_text, created_utc, distinguished, id, retrieved_on, subreddit, subreddit_id, score, crosspost_parent, link_flair_text, num_comments, over_18, post_hint, selftext, spoiler, title, url`。`name`、`permalink`、`is_self`、`removed_by_category`、`_meta` 都会 400 |
| `title` / `selftext` / `query` | 关键词搜索，支持短语引号、OR、`-排除`。文档：「Post title search only works alongside author or subreddit, and is not supported for very active users or subreddits」。**实测 4 次全部 422**（见下） |

限额，文档原话：

> Rate limits are dynamic, based on server load and request complexity. If you receive a 429 rate limit error code, you have to wait until your limits reset.

> Remember, this is a free service, so be considerate. If you want to process massive amounts of data, use the monthly dumps instead. … a couple requests per second [is fine]

> No uptime or performance guarantees :)

响应头只有 `X-RateLimit-Reset`（距下个窗口的秒数，实测按 60 秒倒数）和 `X-RateLimit-Reset-At`，没有剩余次数。

**实测记录**（2026-10-19，共 30 次只读请求，UA `demand-radar/0.2 (research probe)`，间隔 1.5–2 秒）：

| 请求 | 结果 |
|---|---|
| `subreddit=SomebodyMakeThis&limit=2` 不带 `fields` | 200，每条 105 个字段，约 5 KB/条；最新一条发布于 45 分钟前 |
| `subreddit=software&limit=100&fields=id,created_utc` | 200，100 条，4 KB，1.7–2.0 秒。这 100 条跨 44 小时 → r/software 约 55 帖/天 |
| `after=<Unix 秒>` / `before=<Unix 秒>`，小版块 | 200，按时间倒序，端点不含 |
| `before=7d`，小版块 | 200，7 天前的帖子 score 0–1、评论 0–6（这个版块本来就冷，看不出二次抓取是否生效） |
| `title="is there an app"` 或 `query=…`，r/macapps；`title=app&after=30d`，r/SomebodyMakeThis | **4 次全部 422** `{"error": "Timeout. Maybe slow down a bit"}`，约 1 秒内返回 |
| `before=7d`，r/software（活跃） | **2 次 422**（间隔 30 秒）同样报错 |
| 任意版块、任意参数 | **随机 422**：2026-10-20 主会话实测 16 次请求 7 次 422，含最简单的 `subreddit=…&limit=5`，同一条查询几秒后重发就 200；服务端查询超时约 1 秒，和参数无关 |
| 429 | 30 次内没遇到 |

## 三、字段够不够用

| 我们要的 | Arctic Shift 字段 | 备注 |
|---|---|---|
| 唯一 id | `id` → 拼成 `t3_<id>` | 和 RSS 源 Atom 里的 `<id>t3_…</id>` 一致，两边同一帖子入库时去重 |
| 标题 / 正文 | `title` / `selftext` | 转帖（crosspost）正文为空（父帖内容在 `crosspost_parent_list` 里，不在白名单，拿不到）；被删帖正文是 `[removed]` / `[deleted]` 占位，当作空处理 |
| 发布时间 | `created_utc` | Unix 秒，直接做游标 |
| 热度 | `score`、`num_comments` | 发帖 36 小时内基本是 0/1，之后才是真实值 → **日常增量抓到的新帖没有热度优势，回填的历史数据才有** |
| 版块 | `subreddit` | 规范大小写 |
| 链接 | `permalink` 不可选 | 用 `https://www.reddit.com/r/<版块>/comments/<id>/` 拼，Reddit 会跳到带标题 slug 的地址 |
| 按版块 + 时间范围查 | ✓ | `subreddit` + `after` / `before` |
| 按关键词查 | ✗ | 服务端搜索超时，本地筛 |
| 翻历史 | ✓ | `before` 往回切，每页 ≤ 100；活跃版块可能 422 |

## 四、使用条款

### Arctic Shift 自己的要求

- **没有服务条款、没有 LICENSE、README 没有任何使用限制**，对第三方工具既没禁止也没授权。唯一的要求是上面引用的三句话：免费服务请体谅、大量数据用月度 dump、不保证可用。
- 同类开源项目 pain-miner（MIT）已经把它当 Reddit 数据源用，README 也没提条款。
- 它不能授予我们任何对内容的权利：Reddit 的内容归发帖用户所有，Arctic Shift 只是归档。

### Reddit 的政策

Reddit 自己的页面（`redditinc.com`、`support.reddithelp.com`）对非浏览器访问要么 403 要么 404，下面 1、2 是从 `www.redditinc.com/policies/` 拿到的原文；3 没拿到原文，用的是当时的报道。

**1. User Agreement**（适用于访问 reddit.com 的任何人）。原文：

> Access, search, or collect data from the Services by any means (automated or otherwise) except as permitted in these Terms or in a separate agreement with Reddit (we conditionally grant permission to crawl the Services in accordance with the parameters set forth in our robots.txt file, but scraping the Services without Reddit's prior written consent is prohibited)

而 `reddit.com/robots.txt` 现在是：

```
User-agent: *
Disallow: /
```

并在注释里指向 Public Content Policy。这一条管的是**直接访问 reddit.com** 的行为。走 Arctic Shift 时我们不碰 reddit.com，它对我们不直接适用（对 Arctic Shift 自己适用）。反过来，**现有的 RSS 抓取**才是在这一条下面：RSS 是 Reddit 提供给订阅器的，但 robots.txt 全禁、条款禁止未经书面同意的 scraping，程序定时拉 `.rss` 算不算「订阅器」没有定论。

**2. Data API Terms**（适用于使用官方 Data API 的人）。关键句：

> You may use the Data APIs and any other Materials you access from our Services, whether using the Data APIs or not, only in accordance with the Data API Terms.

> Reddit grants you a … revocable license to copy and display the User Content using the Data API solely as necessary to develop, deploy, distribute, and run your App to your App Users. You may not modify the User Content except to format it for such display.

> If you are interested in using the Data APIs for commercial purposes, research in excess of rate limits, or for any use that is not expressly permitted under the Data API Terms, then you will need to enter into a separate agreement with Reddit.

> [You will not] use or retain any User Content, Materials, or data accessed through the Data APIs beyond your approved use case, and you must immediately delete any data not required for it

> [You will not] use the Data APIs to encourage or promote illegal activity or violation of third party rights (including using User Content to train a machine learning or AI model without the express permission of rightsholders in the applicable User Content)

我们不用官方 API，没有接受这份条款；它约束的是 Arctic Shift（它通过官方 API 抓）。但「whether using the Data APIs or not」表明 Reddit 的立场是：从它的服务拿到的内容都按这套规则用。**它对二手数据的下游使用者有没有约束力，不确定。**

**3. Public Content Policy**（2024-05 发布，robots.txt 注释指向它）。按 [TechCrunch](https://techcrunch.com/2024/05/09/reddit-locks-down-its-public-data-in-new-content-policy-says-use-now-requires-a-contract) 和 Axios 当时的报道（非原文）：商业用途需要和 Reddit 签约；「users, mods, researchers, and other good-faith, non-commercial actors」继续可以访问；禁止用内容识别个人、做背景调查、监控；合作方必须尊重用户的删除。另有报道说 2026-05 起未认证的 `.json` 访问返回 403（单一来源，未核实）。

### 对我们这种用法的影响

我们是：开源工具，用户自己运行；用本地模型做抽取（推理，不训练）；输出只有归纳后的需求、一句原文摘录、原帖链接；原文存在用户自己的 MongoDB 里（抽取失败要重试）。

| 要点 | 判断 | 确定程度 |
|---|---|---|
| 工具本身是开源、自托管、非商业 | 更接近 Reddit 政策里「good-faith, non-commercial」一类 | 较确定，但**用户拿它做商业产品就是另一回事**，那是 Reddit 明确要求签约的用途 |
| 本地模型抽取 | 是推理不是训练，不属于「train a machine learning or AI model」 | 我们这么理解，Reddit 对「AI 用途」整体敏感，不确定它怎么看 |
| 只展示摘录 + 链接 | 和 Data API Terms 的「display … format it for such display」精神一致 | 较确定 |
| **已删帖子** | Arctic Shift 发帖 15 秒就归档，作者之后删帖，归档里仍在（`_meta.was_deleted_later` 不在白名单，拿不到）。走归档会处理并存下作者已删的内容；走 RSS 不会 | **这是归档路线和直连路线最大的差别**，Reddit 政策明确要求尊重删除。我们没有同步删除的机制 |
| 存原文到本地库 | Data API Terms 要求「delete any data not required for [approved use case]」；我们存原文是为了重试和摘录 | 不确定是否算「required」 |
| 成人内容 | 代码跳过 `over_18` 的帖子 | 已处理 |

**需要你自己判断的点**：(1) 是否接受「可能处理到作者已删内容」；(2) 若将来商业化，Reddit 要求签约的门槛是否适用；(3) 是否接受依赖一个没有条款、没有协议的第三方服务；(4) 现有 RSS 路线在 robots.txt 全禁之后是否还想保留。本文不替你下结论。

## 五、和 RSS 的关系：RSS 保留，Arctic Shift 优先

| | RSS（现状） | Arctic Shift |
|---|---|---|
| 限流 | 常 429 | 30 次未遇到 |
| 每次能拿 | 最新 25 条 | 最新 100 条；`after=` 增量 |
| 历史 | 无 | `before=` 往回翻 |
| 热度 | 无 | `score`（36 小时后才准） |
| 关键词 | Reddit 站内搜索，可用 | 服务端超时，本地筛 |
| 已删帖 | 不会抓到 | 会抓到 |
| 依赖 | Reddit 官方端点（robots.txt 全禁，条款灰色） | 志愿者免费服务（无承诺，无条款） |

两边产出完全一样的 `source`（`reddit/<版块>`）和 `external_id`（`t3_…`），`fetch_all` 按这个键去重，一次运行里同一帖子只处理一次；注册顺序 `arctic_shift` 在 `rss` 之前，所以重复时保留带 score 和正文的那条。**RSS 的四个 Reddit 订阅保留**：Arctic Shift 挂了还能拿到最新 25 条；代价只是每次多 4 个请求和偶尔的 429 日志。嫌吵可以删掉 `sources.toml` 里的 `[[rss]]` Reddit 块。

## 六、接入设计（已实现：`demand_radar/sources/arctic_shift.py`）

```toml
[arctic_shift]
enabled = true
subreddits = ["SomebodyMakeThis", "macapps", "androidapps", "software"]

[arctic_shift.keywords]        # 本地过滤，只作用于日常抓取；没列的版块全要
macapps = ["is there a", "looking for", "any app", "recommend"]
```

- **日常抓取**：每个版块一次请求，`sort=desc&limit=<--limit>`，有游标加 `after=<游标>`；游标按抓到的全部帖子推进（关键词没命中的也算见过）。和其他源一样每次只取最新 `limit` 条，版块日发帖量超过 `limit` 时中间的会跳过，调大 `--limit`（≤ 100）即可。版块之间歇 1 秒。
- **回填**：`before=<进度>` 往回切，每页 ≤ 100，页间隔 2 秒（文档说每秒几次没问题，宁慢勿快）。**不做关键词过滤**：回填进度按页上最早一条推进，整页被筛空会被误判为「到头」；历史也本来就该完整拉回来。
- **限流 / 超时**：429 按 `X-RateLimit-Reset` 等到窗口重置再试一次（上限 60 秒）；422 按 5 / 10 / 20 秒退避重试三次（2026-10-20 真机：同一条 `subreddit=SomebodyMakeThis&sort=desc&limit=5` 连续 3 次 422，8 秒后原样重发就 200，其他版块、`sort=asc`、`after=` 也会随机 422——和参数无关，像是首次查询慢、后台预热后可用）。日常抓取里一个版块重试耗尽只跳过它（游标不动，下次再拉），全部版块都失败才算该源失败；回填则这一页下次再拉、先换下一个版块。
- **解析**：跳过 `over_18`；`[removed]` / `[deleted]` 当空正文（标题像求助的由 `empty_body` 规则放行）；转帖正文为空。
- 不改 `http.py`：它按 `Retry-After` 重试，这个服务不发这个头，所以在源模块里再按 `X-RateLimit-Reset` 等一回。

真机验证（Docker 用 `docker compose run --rm app <命令>`，本地 venv 用 `python -m demand_radar <命令>`）：

```bash
python -m demand_radar run --source arctic_shift --limit 5          # 四个版块各 5 条，看 [1/4] 抓取数
python -m demand_radar run --source arctic_shift --limit 5          # 再跑一次：应只抓到游标之后的新帖，多半是 0
python -m demand_radar backfill --source arctic_shift --pages 2 --limit 20   # 往回翻 2 页；r/software 可能 422，看它是否跳过并继续
```

**没做 / 待观察**：活跃版块 `before` 超时的原因；转帖补拿父帖正文（`/api/posts/ids` 可查，每次多一个请求）；已删帖同步；`limit=auto`。

## 七、如果你决定不用 Arctic Shift

- **Reddit 官方 Data API**：注册一个 script 类型的 app 拿 client id/secret，OAuth 后免费档每个 client 每分钟 100 次（Reddit 公布的免费额度，未在本次核实）；代码上标准库 `urllib` 就能做，不用加依赖。代价：每个用户都要去 Reddit 注册并接受 Data API Terms，和「一条命令部署」冲突；同样面对「商业用途要签约」。
- **改进 RSS**：失败时按 `Retry-After` 退避已经有；可以把四个订阅的间隔从 2 秒拉到 10 秒以上、自定义更具体的 UA、一次只拉一个订阅。治标，且 robots.txt 全禁的问题还在。
- **月度 dump**：Arctic Shift 的 `.zst` 全量文件，适合一次性回填指定版块，不适合日常；要自己下载解压，不符合一条命令部署。

## 参考

- 仓库与 API 文档：https://github.com/ArthurHeitmann/arctic_shift ，`api/README.md`，`file_content_explanations.md`，issue #2（延迟与二次抓取）
- Reddit：`https://www.redditinc.com/policies/user-agreement`、`https://www.redditinc.com/policies/data-api-terms`、`https://www.reddit.com/robots.txt`；Public Content Policy 原文在 `support.reddithelp.com/hc/en-us/articles/26410290525844`（程序访问 403）
- 同类项目用法：pain-miner（`docs/landscape.md`）
