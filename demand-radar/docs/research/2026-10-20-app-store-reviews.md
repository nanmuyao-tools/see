# App Store / Google Play 评论作为数据源：调研与结论

调研日期：2026-10-20。对应路线图「待定事项」里的「App Store 低分评论（吐槽类）」，以及用户原话：「看看能不能获取到 appstore，googleplay，一些 app 的评论，寻找痛点」。

## 一、结论与建议

1. **技术上拿得到，但两边都不稳、都不是公开 API。**
   - App Store 有苹果自己的老接口「用户评论 RSS」，不用 key。每个 app、每个国家最多拿最近 500 条（50 条一页 × 10 页）。但实测 27 次请求里只有 5 次真的带了评论，其余都是空 feed。空结果被 Akamai CDN 缓存住了，同一个地址隔几分钟再请求还是空的。
   - Google Play 只能靠开源抓取库 google-play-scraper。它调的是 Play 网页自己用的内部接口 `batchexecute`。11 次请求全部成功，一次最多拿到 300 条，能按星级筛、按时间倒序翻页。
2. **条款比 Reddit 那次更明确，而且明确是不利的。**
   - 苹果 Apple Media Services Terms 原文禁止「用软件、自动化流程……抓取、复制，或对内容和服务做测量、分析、监控」。itunes.apple.com 的 robots.txt 也写了 `Disallow: /*/rss/*`。
   - Google 服务条款禁止「违反网页上机器可读指令（如 robots.txt）用自动化手段访问内容」。play.google.com 的 robots.txt 有 `Disallow: /_`，正好覆盖抓取库用的 `/_/PlayStoreUi/data/batchexecute`。
   - 这些条款对「用户自己在本机运行的开源工具」到底约束到什么程度，本文不下结论，列在第五节由你判断。
3. **数据有价值，但形态和现有源不一样。** 人工标了 150 条 1–3 星评论（10 组，覆盖笔记和记账两类 app、两个商店、中英文）：
   - 说清了具体缺什么功能的（N1）占 17%。
   - 方向性的不满（广告、强推 AI、涨价、改版变难用）占 37%。这类单条不算需求，但聚合后是「替代品机会」。
   - bug、崩溃、同步失败占 31%；纯情绪或无关内容占 13%。
   - 中国区 App Store 的记账 app 里，77% 是在骂开屏广告和会员墙。
   - 单条评论几乎都是「这个 app 的厂商该改」。
   - 用现有提示词原样跑这 150 条（Qwen3-4B，本机 mlx）：只有 7 条被判为需求，56 条 N2 全部被判成「不是需求」。另外 `empty_body` 规则会先挡掉 18% 的中文短评。**提示词、过滤规则、评测集都不能直接复用。**
4. **建议：v1.x 不接入默认数据源，也先不写代码。**
   - 如果你接受第五节的条款风险，最小可行做法是加一个默认关闭的 `appreview` 源，只用 App Store RSS 和标准库，不加任何依赖。同时要给评论单独写一套提示词，单独做评测，并按 app 聚合。
   - Google Play 那条路不要直接依赖 google-play-scraper：它一被导入就会**全局关闭 TLS 证书校验**（见 2.2）。
5. 同类做法：sift（landscape 里已有）就是用同一个 App Store RSS 加 Play 网页 HTML 抓取。商业的 Appbot、AppFollow 每月几十到几百美元，主要卖给 app 开发者监控「自己的 app」，不是找新产品点子。学术界「从评论里挖需求」的研究很多。所以这个方向不算空白，我们如果做，差异化仍然只能落在「本地模型 + 可量化」上。

## 二、获取方式逐一对比

### 2.1 总表

| 方式 | 要账号 / 付费 | 能看别人的 app | 字段 | 每次最多 | 按时间增量 | 按国家 / 语言 | 实测 |
|---|---|---|---|---|---|---|---|
| **App Store 用户评论 RSS**（`itunes.apple.com/{cc}/rss/customerreviews/page={1-10}/id={id}/sortby=mostrecent/{json\|xml}`） | 否 | 能 | 评论 id、星级、标题、正文、app 版本、更新时间（带时区）、`voteSum` / `voteCount`（有用票）、作者昵称 | 50 条 / 页，最多 10 页。第 11 页返回 400，正文是 `CustomerReviews RSS page depth is limited to 10` `[一手]` | 没有时间参数，只能按 `mostrecent` 倒序翻到游标为止。每个 app 每个国家最多只能看到最近 500 条 | 按国家（URL 里的 `cc`，`us` / `cn` 都实测过）。没有语言参数，中国区评论基本是中文 | 27 次只有 5 次有数据，见 3.1 |
| App Store Search / Lookup API（`itunes.apple.com/search`、`/lookup`） | 否 | 能 | 只有元数据：`averageUserRating`、`userRatingCount`、类别、版本、发布说明等，**没有评论正文** `[一手]` | search `limit` ≤ 200（文档值，未实测上限） | — | `country=` | 3 次全部成功。用途是查 app id |
| App Store 榜单 RSS | 否 | 能 | app 名、id、类别 | v2（`rss.applemarketingtools.com/api/v2/us/apps/top-free/10/apps.json`）**不能按类别**；老接口（`itunes.apple.com/us/rss/topfreeapplications/limit=10/genre=6015/json`）能按类别，6015 是财务类 `[一手]` | — | 按国家 | 2 次全部成功。可以拿来做「按类别榜单配置要监控的 app」 |
| App Store Connect API `customerReviews` | 要开发者账号（$99/年）和 JWT 密钥 | **不能**，只能看自己团队的 app | 全量评论 | — | — | — | 没试。文档页是前端渲染，没抓到正文，「只能看自己的 app」是 `[推断]`，依据是鉴权模型 |
| `amp-api.apps.apple.com`（App Store 网页版用的接口） | 要从网页里拿 bearer token | 能 | 更全，可翻更深 | — | — | — | **没试**：要拿网页的 token 去调，属于绕过，不考虑 |
| **google-play-scraper（Python，JoMingyu）** | 否 | 能 | `reviewId`、`score`（星级）、`content`、`thumbsUpCount`、`reviewCreatedVersion`、`at`（时间）、`replyContent` / `repliedAt`（开发者回复）、`userName`。**没有标题** `[一手]` | 实测一次请求 `count=300` 返回 300 条。库里的常量是单次最多 4500 | `sort=NEWEST` 倒序 + `continuation_token` 翻页，翻到游标就停。没有服务端时间过滤 | `lang` + `country`。`zh-TW` / `tw` 实测 100 条里 85 条含中文 | 11 次全部成功 |
| google-play-scraper（Node，facundoolano） | 否 | 能 | 同上 | 同上 | 同上 | 同上 | 没装：要 Node 运行时，镜像会多出一大块，不考虑 |
| Google Play Developer API `reviews` | 要 Play 开发者账号和服务账号 | **不能**，只能看自己的 app | 只返回「最近一周内创建或修改的、带正文的」评论，GET 每个 app 每小时 200 次 `[一手]` [文档](https://developers.google.com/android-publisher/reply-to-reviews) | — | — | — | 没试 |

### 2.2 两个抓取方式的原理

**App Store RSS**：这是苹果公开提供的老 feed，地址里不需要任何凭证，和 RSS 阅读器订阅的是同一个东西。

- 苹果新版的 RSS 生成器（marketing tools）只有榜单，**没有评论 feed**。评论 RSS 是没进新文档的遗留接口，随时可能停 `[推断]`。依据是生成器页面里没有这一项，开发者论坛也有「老 RSS 地址失效、改到 v2」的帖子 `[二手]` [Apple Developer Forums](https://developer.apple.com/forums/thread/705196)。
- JSON 和 XML 两种格式拿到的内容一样。JSON 版第一页第一条有时是 app 元数据，sift 的代码里专门跳过带 `im:name` 的条目。

**google-play-scraper**：不需要登录，没有破解签名，也没有绕过验证码。

- 原理是模拟 Play 网页前端：向 `https://play.google.com/_/PlayStoreUi/data/batchexecute?hl=..&gl=..` POST 一个固定格式的 `f.req`（RPC id `oCPfdb`），再按数组下标从返回里取字段（`constants/element.py`）。
- 这是**未公开的内部接口**。Google 一改返回格式，库就会失效。
- 仓库最近一次推送是 2024-08-05（截至 2026-10-20）。今天实测仍然能用。
- 库的四个问题 `[一手]`，源码在 `utils/request.py` 和 `features/reviews.py`：
  1. **导入即全局关闭 TLS 证书校验**：模块顶层写着 `ssl._create_default_https_context = ssl._create_unverified_context`。同一进程里所有用 `urllib` 的请求都会跟着不校验证书，包括我们自己的 HTTP 层。这一条足以排除「直接作为依赖导入」。
  2. **出错时静默返回空列表**：`reviews()` 里是 `except Exception: token = None; break`。实测包名写错（`so.notion.id`，正确的是 `notion.id`）时不报错，只返回 0 条。
  3. 限流时（返回里出现 `PlayGatewayError`）按 5 秒、10 秒退避重试 3 次。
  4. `at` 用 `datetime.fromtimestamp` 转成了本机时区的无时区时间。存游标时要注意。

### 2.3 协议

| 项目 | LICENSE 原文 | 星数 / 最近推送（截至 2026-10-20） | 对本项目（MIT） |
|---|---|---|---|
| [JoMingyu/google-play-scraper](https://github.com/JoMingyu/google-play-scraper) | MIT（`Copyright (c) 2019 PlanB`）`[一手]` | 1,020★ / 2024-08-05 | 兼容。可以作为依赖，也可以抄下其中约 150 行，保留版权声明 |
| [facundoolano/google-play-scraper](https://github.com/facundoolano/google-play-scraper)（Node） | MIT（`Copyright (c) 2015 Facundo Olano`）`[一手]` | 2,985★ / 2026-10-17 | 兼容，但要 Node |
| [facundoolano/app-store-scraper](https://github.com/facundoolano/app-store-scraper)（Node） | MIT（GitHub API 显示，没看原文） | 1,434★ / 2025-07-27 | 不需要。App Store RSS 用标准库就能解析 |

**注意**：代码的 MIT 协议只管代码本身。评论内容的权利归苹果、Google 和写评论的用户，代码协议不授予任何内容使用权。

## 三、实测记录

环境：Homebrew python3.12，scratchpad 里的临时 venv。UA 是 `demand-radar/1.0 (research probe)`。

- App Store 请求间隔 3.5–10 秒。
- Play 请求间隔 4 秒。
- 原始返回、脚本和标注都放在 scratchpad，没有进仓库。

**请求次数**：

| 对象 | 次数 |
|---|---|
| App Store 评论 RSS 和 Search / Lookup / 榜单 | 33（含 1 次 HEAD 看缓存头） |
| play.google.com 评论接口 | 11 |
| 三个 robots.txt | 3 |
| **合计** | **47** |

条款页面和 GitHub 元数据另算，不是对数据源的请求。

**复现**：脚本在 scratchpad，没有进仓库，最小步骤如下。

```bash
# App Store：标准库即可
curl -s "https://itunes.apple.com/us/rss/customerreviews/page=1/id=1232780281/sortby=mostrecent/xml" | grep -c "<entry>"   # 50 或 0，见 3.1

# Google Play：临时 venv
/opt/homebrew/bin/python3.12 -m venv /tmp/gp && /tmp/gp/bin/pip install google-play-scraper==1.2.7
/tmp/gp/bin/python -c "from google_play_scraper import reviews, Sort; r,_=reviews('notion.id', lang='en', country='us', sort=Sort.NEWEST, count=100, filter_score_with=1); print(len(r), r[0].keys())"
```

4.2 的抽取实测：脚本把 demand-radar 目录加进 `sys.path`，构造 `SourceItem`，再调 `filters.check()` 和 `extract.extract()`。provider 是 `local/mlx-community/Qwen3-4B-Instruct-2507-4bit`，地址 `http://127.0.0.1:8765/v1`。

### 3.1 App Store 评论 RSS：能用，但不稳

| 轮次 | 间隔 | 评论 feed 请求 | 有数据 | 空 feed | 400 |
|---|---|---|---|---|---|
| 1 | 3.5 秒 | 9 | 2（YNAB 美区 JSON 第 2 页；Notion 美区 XML 第 1 页） | 7 | 0 |
| 2 | 3.5 秒 | 12 | 0 | 11 | 1（第 11 页） |
| 3 | 10 秒 | 6 | 3（Notion 美区 XML 第 1 页再次成功；随手记中国区不带 sortby 的 JSON；鲨鱼记账中国区 JSON 第 2 页） | 3 | 0 |

- 空 feed 是 HTTP 200，只有 feed 头，没有 entry。
- HEAD 请求看到 `x-cache: TCP_HIT from ...akamaitechnologies.com`，`apple-timing-app: 3 ms`。也就是说，空结果是 CDN 缓存命中的，不是我们被限流 `[一手]`。
- 规律 `[推断]`：同一个 URL 空了就会持续空一段时间（鲨鱼记账第 1 页、Notion JSON 第 1 页都两次为空）。换一个等价的 URL（JSON 换 XML、带不带 `sortby`）有时能拿到。
- 加随机参数打穿缓存在技术上可行，但等于绕开对方的 CDN 去压源站。**没有试，也不建议。**
- 能拿到的数据在时间上很新：Notion 美区第 1 页 50 条里最新一条是 2026-10-16。YNAB 第 2 页覆盖 2026-08-14 到 09-10。

**对接入的影响**：日常增量可以接受「这次空、下次再试」，游标不动即可。但不能把空结果当成「没有新评论」，要和真正的空区分开。办法是看 feed 的 `link rel="last"` 是否为空字符串：拿到数据时 `last` 指向第 10 页，空 feed 时 `last` 是空字符串。

### 3.2 Google Play：稳定

| 请求 | 结果 |
|---|---|
| Notion 美区，`NEWEST`，100 条 + 续页 100 条 | 两页时间连续：10-09 到 10-19，09-29 到 10-09 |
| Notion 美区，只要 1 星，100 条 | 覆盖 08-14 到 10-18 |
| YNAB 美区，`NEWEST`，**一次 `count=300`** | 300 条，一次请求，覆盖 01-22 到 10-18。其中 174 条有开发者回复 |
| Evernote 美区，只要 2 星，100 条 | 100 条 |
| Notion 台湾区 `zh-TW`，100 条 | 85 条含中文 |
| 台湾区搜「記帳」 | 5 个记账 app；取第一个「簡單記帳」抓了 100 条 |

**「中文区」在两个商店的含义不同**：

- Google Play 在中国大陆没有服务，所以中文评论只能取台湾区或香港区（繁体）。
- 鲨鱼记账、随手记这类大陆 app 主要在国内安卓商店分发。国内商店（华为、小米、应用宝等）本次没有调研。
- 简体中文的 app 评论，实际上只有 App Store 中国区能拿到。

### 3.3 评论长什么样：150 条低分评论人工标注

**取样**：

- 共 10 组，每组随机取 15 条 1–3 星评论，固定随机种子。
- App Store 4 组：美区 Notion、美区 YNAB、中国区随手记、中国区鲨鱼记账。
- Google Play 6 组：美区 Notion（最新）、美区 Notion（1 星）、美区 YNAB、美区 Evernote（2 星）、台湾区 Notion、台湾区簡單記帳。
- 15 条是为了在请求和标注量受限时覆盖更多组。每组的比例误差很大，**只看量级**。

**口径**（先定口径再标）：

- **N1 具体功能缺口**：说清了想要什么。例如「手机端不能一次建多条待办」「想要双周目标」「不同币种分开记账」「离线可用」「把总结余功能加回来」。
- **N2 方向性不满**：单条不构成需求，但聚合后能说明替代品机会。例如开屏广告、强推 AI、涨价、会员墙、改版后信息密度变低。
- **B**：bug、崩溃、性能、银行同步失败。
- **V**：纯情绪、无信息、无关内容。
- **S**：客服、账号、退款。

| 分组 | 条数 | N1 具体缺口 | N2 方向性不满 | B bug | V 情绪 | S 客服 |
|---|---|---|---|---|---|---|
| App Store 美区（Notion、YNAB） | 30 | 6（20%） | 9（30%） | 12（40%） | 3（10%） | 0 |
| App Store 中国区（随手记、鲨鱼记账） | 30 | 2（7%） | **23（77%）** | 1（3%） | 3（10%） | 1（3%） |
| Google Play 美区（Notion ×2、YNAB、Evernote） | 60 | 10（17%） | 19（32%） | 20（33%） | 11（18%） | 0 |
| Google Play 台湾区（Notion、簡單記帳） | 30 | 8（27%） | 5（17%） | 13（43%） | 3（10%） | 1（3%） |
| **合计** | **150** | **26（17%）** | **56（37%）** | **46（31%）** | **20（13%）** | **2（1%）** |

几个观察：

- **N1 几乎全部是「这个 app 的厂商该改」**，例如 YNAB 的双周目标、Notion 的离线页面、簡單記帳的总结余。个人开发者能接的只有两种：做替代品，或者给它做插件、伴侣工具。真正跳出这个 app 的独立需求很少，150 条里只有 1 条勉强算：YNAB 用户想按会计习惯自己记「用信用卡积分还款」。
- **聚合才有信号**：
  - 簡單記帳 15 条里有 5 条在说同一件事：「全部 / 月」切换和总结余功能被删了。
  - Notion 的低分集中在「移动端难用、强推 AI、不能离线」。
  - 两个国内记账 app 的低分几乎全是开屏广告、摇一摇跳转和会员墙。「无广告、买断制、可导出的记账 app」这个机会是从 23 条 N2 里聚出来的，任何单条都看不出来。
- **评论很短**：
  - 正文长度中位数：中国区鲨鱼记账 13 字，随手记 33 字，台湾区簡單記帳 14 字，台湾区 Notion 23 字。
  - 美区 App Store 约 200 字符，美区 Play 48–204 字符。
  - 现有 `empty_body` 规则（去掉模板后少于 20 个字符就丢）会挡掉大批中文短评，见第四节。
- **语言混杂**：台湾区 Notion 里有粤语、简体、英文；美区 Notion 里有日文、印地语、葡萄牙语。
- **点赞数**：Play 有 `thumbsUpCount`，YNAB 300 条里 143 条大于 0，可以当热度。App Store 的 `voteSum` 大多是 0，中国区两组 50 条里各只有 0–1 条非 0，基本不能当热度用。

## 四、适配度

### 4.1 和现有数据源比

| | 现有源（HN、softwarerecs、GitHub、Reddit） | App 评论 |
|---|---|---|
| 内容 | 「有没有能做 X 的工具」「要是有 X 就好了」 | 「这个 app 的 Y 不好」 |
| 单条的价值 | 单条就能是一个需求 | 单条多半是给厂商的 bug 或功能请求，**要按 app 或类别聚合才成机会** |
| 机会类型 | 新工具 | **替代品、插件、伴侣工具**；或者「某类 app 普遍的毛病」 |
| 中文 | 只有少量（V2EX 还没接） | App Store 中国区是目前能拿到的**唯一大量简体中文用户声音** |
| 条款 | 多数有公开 API，条款允许或灰色 | 两个商店都有明确的不利条款（第五节） |

### 4.2 现有提示词和规则原样跑的结果

用项目自己的 `extract()` 和 `filters.check()`，提示词一个字不改。模型是 Qwen3-4B-Instruct-2507-4bit，跑在本机 mlx-lm（`127.0.0.1:8765`）上，温度 0，输出中文，150 条全部跑。

评论的映射：

- `title` =「{app} 的用户评论（N 星）：{评论标题}」
- `text` = 正文
- `kind` = `review`

结果：150 条耗时 322 秒，平均每条约 2.1 秒，没有失败 `[一手]`。

**规则预过滤**：`empty_body` 挡掉 27 条（18%）。

- 其中 App Store 中国区 30 条里挡掉 16 条。
- 被挡的有 13 条 N2、2 条 N1：「为什么不可以多人共享旧帐本」就差点被挡。
- 帖子用的 20 字符门槛对中文短评太高。

**模型判断**：下表忽略过滤，150 条全部交给模型。

| 人工标注 | 条数 | 模型判 `is_request=true` | 其中 `buildable=true` |
|---|---|---|---|
| N1 具体缺口 | 26 | 6 | 6 |
| N2 方向性不满 | 56 | **0** | — |
| B bug | 46 | 1（「希望支持蓝牙键盘」） | 1 |
| V 情绪 | 20 | 0 | — |
| S 客服 | 2 | 0 | — |

**怎么解读**：

- 现有提示词把绝大多数评论判成「不是需求」，150 条只放过 7 条。这对「卡、广告多、太贵、客服差不是需求」这条规则来说是对的，所以 **N2 一条都没进清单**。但 N2 恰恰是评论里最有替代品价值的那部分。
- 放过的 6 条 N1 被判成 `buildable=true`。可它们都是「希望鲨鱼记账支持……」「希望恢复……」这种只有厂商能改的需求，和提示词里「让某个 App 去掉广告 → false」的标准不一致。
- 也就是说，现有提示词对评论**既漏掉了要的，放进来的又判错了**。
- 结论：**提示词和规则都不能直接复用**。评论要有自己的提示词，问的是「这说明同类产品有什么机会」，而不是「这是不是一个可以独立做的工具需求」。还要有自己的过滤阈值。

### 4.3 映射到 SourceItem

| 字段 | App Store | Google Play |
|---|---|---|
| `source` | `appstore/{cc}/{app_id}`，如 `appstore/cn/1079718756` | `googleplay/{country}/{package}`，如 `googleplay/tw/notion.id` |
| `external_id` | 评论 `id`（如 `14533363800`） | `reviewId`（UUID） |
| `title` | 「{app 名}：{评论标题}」。app 名用 Lookup 查一次后缓存 | 「{app 名}」（评论没有标题） |
| `text` | `content` | `content` |
| `url` | 没有单条评论的链接，只能给 app 页面 `apps.apple.com/{cc}/app/id{id}` | app 页面 `play.google.com/store/apps/details?id=..&hl=..&gl=..` |
| `score` | `voteSum`（大多为 0） | `thumbsUpCount` |
| `created_at` | `updated`（ISO，带时区） | `at`（库给的是本机时区的无时区时间，要换算） |
| `kind` | 新值 `review` | 同左 |
| 星级 | `SourceItem` 没有这个字段。建议在源里按 `max_rating = 3` 过滤，星级写进 `title` 让模型看到 | 同左，还能在请求时用 `filter_score_with` 直接只要 1 / 2 / 3 星（要 3 次请求） |

两点要说明：

- 写评论的用户昵称**不入库**。sift 也是入库时匿名化。
- 「原帖链接」只能落到 app 页面，清单里每条的「来源」就不是一条可以点开核对的原文了。原文摘录（`excerpt`）因此更重要。

### 4.4 用户怎么配置要监控的 app

两种方式都能做，建议先只做第一种：

```toml
# 草案，未实现
[appreview]
enabled = false            # 默认关闭：条款见 docs/research/2026-10-20-app-store-reviews.md
max_rating = 3             # 只要 1–3 星

[[appreview.apps]]
name = "鲨鱼记账"
appstore = { id = 1079718756, countries = ["cn"] }

[[appreview.apps]]
name = "Notion"
appstore = { id = 1232780281, countries = ["us"] }
googleplay = { package = "notion.id", locales = ["en-us", "zh-tw"] }

# 第二种（以后再说）：按类别榜单自动挑 app
# [appreview.charts]
# appstore = [{ country = "cn", genre = 6015, top = 10 }]   # 6015 = 财务
```

- **按 app id 列表**：可控，请求量可预估。每个 app、每个国家每天 1–2 次请求。用户需要知道 app id，可以提供一个 `appreview lookup "鲨鱼记账" --country cn` 子命令走 Search API 查。
- **按类别榜单**：老榜单 RSS 能按 `genre` 取前 N 名，适合「看看记账这一类普遍在抱怨什么」。但请求量是 N 倍，而且排名每天在变，游标管理更复杂。

### 4.5 对镜像和依赖的影响

| 路线 | 新依赖 | 镜像体积 | 启动步骤 |
|---|---|---|---|
| App Store RSS | **0**。`urllib` 加 `xml.etree` 或 `json`，复用 `sources/http.py` 的限速和退避 | ≈0 | 不变 |
| Play，直接 `pip install google-play-scraper` | 1 个包，**本身零依赖**，144 KB `[一手]` 实测 | +约 150 KB | 不变。但会**全局关闭 TLS 校验**，不可接受 |
| Play，抄写约 150 行进 `sources/googleplay.py`，走我们自己的 `http.py` | 0（保留 MIT 版权声明） | ≈0 | 不变。代价是 Google 改格式时要自己跟 |
| Play，Node 版 | Node 运行时 | +几十 MB | 不符合「小而精」 |

### 4.6 评测：现有评测集不能直接用

- 现有 116 条（`eval/eval_set.json`）全部是帖子和评论，没有一条 app 评论。标签只有 `is_request`、`buildable`、`harmful`。
- 评论需要的判断不一样：「这是不是一个可以做替代品或伴侣工具的痛点」，以及「这条和同一 app 的哪些评论是一回事」。
- 建议补两样东西：
  1. **单条抽取样本 40–60 条**：本次 150 条的口径（N1 / N2 / B / V / S）可以直接作为起点。中英各半，覆盖两个商店，至少 3 类 app。只标 1–3 星。按本次比例，40 条里大约 7 条 N1、15 条 N2、12 条 B、6 条 V。这样 N1 和 N2 各自至少有 7–15 条，不至于 1 条就让 F1 摆动好几个点。
  2. **按 app 聚合的样本 10–20 组**：同一 app 的评论两两标注「是不是同一个痛点」，用来调 `cluster` 在短文本上的阈值。现有 23 对合并标注是长帖，阈值 0.78 不一定适用短评。
- `eval` 命令要能按 `kind` 分开报数字，不能把评论样本混进帖子的 F1 里，否则会拉低主线指标、掩盖回退。

## 五、条款：需要你判断的点

### 5.1 原文

**Apple Media Services Terms and Conditions**（Last Updated: September 14, 2026）`[一手]` [原文](https://www.apple.com/legal/internet-services/itunes/us/terms.html)，第 F 节「Services and Content Usage Rules」：

> You may not use any software, device, automated process, or any similar or equivalent manual process to scrape, copy, or perform measurement, analysis, or monitoring of, any portion of the Content or Services. Additionally, you may not use the Services, Content, or any outputs from the Services or Content, to train, fine-tune, or improve any artificial intelligence model or other software.

> You may use the Services and Content only for personal, noncommercial purposes (except as set forth in the App Store Content section below or as otherwise specified by Apple).

**itunes.apple.com/robots.txt**（2026-10-20 实取）`[一手]`：

```
User-agent: *
Disallow: /search*
Disallow: /*/rss/*
Disallow: /*/lookup?
```

摘录了相关的几行。`apps.apple.com/robots.txt` 还禁止 `/api/*`、`/v1/*`、`*/search?*`。

**Google 服务条款**（Effective July 30, 2026）`[一手]` [原文](https://policies.google.com/terms?hl=en&gl=US)，「滥用服务」列举的禁止行为之一：

> using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files that disallow crawling, training, or other activities)

另一处列举的停用账号原因包括「scraping content that doesn't belong to you」。

**play.google.com/robots.txt**（2026-10-20 实取）`[一手]`：`User-Agent: *` 下有 `Disallow: /_`，覆盖抓取库用的 `/_/PlayStoreUi/data/batchexecute`；还有 `Disallow: /store/getreviews`。

Google Play 自己的服务条款页是前端渲染，没抓到正文。Play 是 Google 服务，上面的通用条款适用。

### 5.2 和 Reddit / Arctic Shift 那次的区别

| | Reddit（Arctic Shift） | App Store RSS | Google Play 抓取库 |
|---|---|---|---|
| 我们直接访问的是谁 | 第三方归档，它自己没有条款 | 苹果 | Google |
| 条款有没有明文禁止 | Reddit 条款管的是直接访问 reddit.com 的人，对二手数据的下游不明确 | **明文禁止自动化抓取和「分析、监控」** | **明文禁止违反 robots.txt 的自动化访问** |
| robots.txt | 不涉及 | `Disallow: /*/rss/*` | `Disallow: /_` |
| 接口性质 | 公开 API | 苹果自己发布的公开 feed，但没进新文档 | 未公开的内部接口 |

### 5.3 需要你自己判断的点

1. **苹果条款的适用范围**。这份条款是用户使用 App Store 等服务时接受的协议。一个不登录、不用 Apple 账号、只读公开 feed 的程序，算不算「使用 Services」，从而受它约束？反过来，RSS 是苹果主动发布给订阅器用的，程序定时拉 feed 算不算条款里的「automated process to … monitor」？本文不下结论。
2. **robots.txt 的效力**。两边的 robots.txt 都覆盖了我们要访问的路径。Google 的条款把「违反 robots.txt」直接写成违约；苹果没有这样写，但 robots.txt 本身表达了态度。
3. **用途**。苹果条款限定「personal, noncommercial」。开源工具由用户自己跑、自己看，更接近个人用途。但用户拿它做商业调研或公开周报，就是另一回事了。周报里贴评论原文属于「copy … distribute」。
4. **AI 用途**。苹果条款禁止用内容「train, fine-tune, or improve any AI model or other software」。我们是本地推理不是训练，但「improve … other software」措辞很宽：用评论找痛点去改进自己的产品，算不算？不确定。
5. **个人信息**。评论带用户昵称。我们计划不入库，但原文正文仍存在用户自己的 MongoDB 里。
6. **依赖一个随时会坏的接口**。评论 RSS 是遗留接口，Play 是内部接口，都没有可用性承诺。

## 六、同类做法

| 名称 | 类型 | 怎么拿评论 | 定价（截至 2026-10-20） | 出处 |
|---|---|---|---|---|
| [sift](https://github.com/Gitter09/sift)（landscape 已有） | 开源 CLI，MIT | App Store：同一个 `customerreviews` JSON RSS，跳过 `im:name` 条目。Play：抓 `play.google.com/store/apps/details?...&showAllReviews=true` 的 HTML，选 `[data-review-id]`，只能拿到页面上直接渲染的少量评论 | 免费 | `[一手]` `sift/scrapers/simple_sources.py` |
| Appbot | SaaS，给 app 开发者做评论监控和情感分析 | 未公开 | 起价约 $49 / 月，按「源」（一个商店里的一个 app）计价；更高档 $159–449 / 月（第三方数据，时间不明） | `[二手]` [Capterra](https://www.capterra.com/p/172807/Appbot/)、[FitGap](https://us.fitgap.com/products/appbot)、[Appbot 博客](https://appbot.co/blog/appbot-vs-appfigures/) |
| AppFollow | SaaS，评论管理、回复、ASO | 未公开 | 有免费档；$29 / $179 / $299 每月和企业定制（竞品 Sonar 2026-07-29 的快照） | `[二手]` [Sonar 对比页](https://trysonar.app/alternatives/appfollow)、[Capterra](https://capterra.com/p/181039/AppFollow/) |
| Apify「Google Play Review Pain Miner」 | 托管 actor，抓低星评论并按规则归类「机会」 | 未公开 | 按 Apify 用量付费 | `[二手]` [Apify](https://apify.com/creative_maitake/google-play-review-pain-miner) |
| 学术研究 | 「从 app 评论里抽需求」是软件工程里做了很多年的方向，近年有用大模型做的 | 多用公开数据集 | — | `[二手]` 例如 [arXiv 2409.15473](https://arxiv.org/html/2409.15473v1) |

**对我们意味着什么**：

- 商业产品卖的是「监控**自己的** app」给开发者和运营。它们的数据来源一般不公开，可能有授权或走开发者自己的 API，和我们「挖别人家 app 找机会」不是一个用户群。
- 「用评论挖替代品机会」已经有 Apify actor 和 sift 在做，但都很小。这个方向不是空白，但也没有强势的开源玩家。
- 我们能加的仍然是「本地模型、一条命令、可量化」。可量化在这里尤其重要：没看到谁公开评论分类的准确率。

**本次改动的其他文档**：`docs/landscape.md` 改了两处。

- sift 那一行补上了它获取 App Store 和 Play 评论的具体方式。
- 「商业产品」里加了一条 App 评论方向的产品：Appbot、AppFollow、Apify actor。

## 七、本次没做的

- 国内安卓商店（华为、小米、应用宝、豌豆荚）的评论：没调研，各家条款和接口都不同。
- App Store Connect API 和 Play Developer API 没有实际调用：需要开发者账号，而且只能看自己的 app。
- 空 feed 的持续时间：没有隔几小时再测同一个 URL。
- 「替代品机会」的提示词和聚合逻辑：不在本次范围，属于实现阶段。

## 八、建议的路线图改动

由主会话决定，本文没有改 ROADMAP.md。

1. 「待定事项」里的「App Store 低分评论（吐槽类）」改成：「App Store / Google Play 评论：已调研（见 research/2026-10-20-app-store-reviews.md）。技术上可行，但两边条款和 robots.txt 都明确不利，**等你判断条款后再决定**；不进 v1.x 默认源。」
2. 如果你接受条款风险，作为 **v1.x 的实验性小项**，按这个顺序做，每步都能停：
   1. 先补评测。40–60 条评论的单条抽取标注，加 10–20 组同 app 聚合标注。`eval` 按 `kind` 分开报数，不影响主线 F1。
   2. 评论专用提示词：判断「替代品、伴侣工具机会」。广告、涨价、强推 AI 这类方向性不满，要能被归纳成「某类 app 的普遍痛点」，而不是判成非需求。
   3. `appreview` 源只做 App Store RSS，标准库实现，默认 `enabled = false`。空 feed 不推进游标。不存昵称。
   4. 中文短评放宽 `empty_body` 的长度阈值。只对 `kind = review` 生效，不动现有规则。
   5. Google Play 放最后，只考虑「抄写约 150 行走自己的 `http.py`」这条路，不引入 google-play-scraper 依赖。
3. 「已有方案提示」可以反过来用评论：对清单里的某条需求，如果在同类 app 的低分评论里也反复出现，说明「已有方案但做得不好」。这比「没人做过」更有用，可以记为 v0.3.x 那一项的后续想法。

## 参考链接

访问日期都是 2026-10-20。

- App Store 评论 RSS：`https://itunes.apple.com/{cc}/rss/customerreviews/page={n}/id={id}/sortby=mostrecent/{json|xml}`（实测）
- iTunes Search / Lookup：`https://itunes.apple.com/search`、`https://itunes.apple.com/lookup`（实测）
- 榜单 RSS v2：`https://rss.applemarketingtools.com/api/v2/us/apps/top-free/10/apps.json`；老榜单：`https://itunes.apple.com/us/rss/topfreeapplications/limit=10/genre=6015/json`（实测）
- Apple Media Services Terms：https://www.apple.com/legal/internet-services/itunes/us/terms.html
- robots.txt：https://itunes.apple.com/robots.txt 、https://apps.apple.com/robots.txt 、https://play.google.com/robots.txt
- Google 服务条款：https://policies.google.com/terms
- Google Play Developer API 评论：https://developers.google.com/android-publisher/reply-to-reviews
- App Store Connect API customerReviews：https://developer.apple.com/documentation/appstoreconnectapi/get-v1-apps-_id_-customerreviews （前端渲染，没拿到正文）
- Apple 开发者论坛，RSS 迁移：https://developer.apple.com/forums/thread/705196
- google-play-scraper（Python）：https://github.com/JoMingyu/google-play-scraper ，PyPI 1.2.7
- google-play-scraper（Node）：https://github.com/facundoolano/google-play-scraper
- app-store-scraper（Node）：https://github.com/facundoolano/app-store-scraper
- sift：https://github.com/Gitter09/sift
- Appbot：https://www.capterra.com/p/172807/Appbot/ 、https://appbot.co/blog/appbot-vs-appfigures/
- AppFollow：https://trysonar.app/alternatives/appfollow
- Apify Google Play Review Pain Miner：https://apify.com/creative_maitake/google-play-review-pain-miner
- arXiv 2409.15473：https://arxiv.org/html/2409.15473v1
