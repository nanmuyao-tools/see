# 「已有方案」提示：候选项目评估与最小自研方案

调研日期：2026-10-20。对应路线图 v0.3 最后一项「『已有方案』提示：优先评估集成现有的 idea-validation 开源项目」。

## 一、结论与建议

1. landscape 里那个项目是 **[mnemox-ai/idea-reality-mcp](https://github.com/mnemox-ai/idea-reality-mcp)**：MIT 协议，截至 2026-10-20 有 827★。它从 2026-08 起进入维护模式，不再加新功能。**不建议集成，只借鉴思路。** 原因有四条：
   - 默认会把需求原文发到作者托管的服务，由 Claude Haiku 扩写，这违背「全本地」。
   - 关键词提取靠英文词典，**中文 need 会原样当成一整个查询**。我们的清单默认是中文。
   - 一次检查要发 2×K 次 GitHub 搜索（K 最多 8）。匿名限额是每分钟 10 次，实测**第一条需求就被 403 限流**。
   - 每个结果都附带 AngelRun 的推广链接。
2. 另一个高星项目 [MaxKmet/idea-validation-agents](https://github.com/MaxKmet/idea-validation-agents)（MIT，487★）只有给 Claude Code / Codex 用的提示词文件，没有代码，没有可集成的东西。
3. **建议自研一个小模块，按需求来源分三条路走，不增加依赖。**
   - softwarerecs 的需求：直接取问题下面的回答。回答本身就是现成方案，100 个问题一次请求。
   - Reddit / HN 上「想要一个工具」的需求：本地 Qwen3-4B 把中文 need 改写成 2–4 个词的英文短查询，查 GitHub 仓库搜索和 HN Algolia，再用已经在用的 `qwen3-embedding:0.6b` 按相似度重排。
   - GitHub issue 类需求（对某个产品提的功能请求）：默认不查，标成「只有平台方能做」。
4. 手工试跑 6 条真实需求的结果：**能找到相关的小项目，但找不到最有名的成熟方案**。例如 ntfy 34.9k★、Keploy 18.7k★、ChatALL 16.5k★ 都没搜到。有 2 条的第一名是需求原帖自己。所以这一项**必须先建标注集再调**，界面上只能写「找到的类似项目」，不能写「没人做过」。
5. 建议把这一项从 v0.3 验收里拆出来，作为 v0.3.x 的独立小项。先做 30 条标注和离线评测，再写功能（具体改动见第八节）。

## 二、候选对比

| 候选 | 协议（看过原文） | 活跃度 | 集成方式 | 依赖 / 体积 | 要 key / 付费 | 要云端大模型 | 结论 |
|---|---|---|---|---|---|---|---|
| [idea-reality-mcp](https://github.com/mnemox-ai/idea-reality-mcp) | MIT `[一手]` LICENSE | 827★；最后推送 2026-09-18；README 写 2026-08 起维护模式；PyPI 最新 0.5.0（2026-03-11），仓库是 0.5.1 `[一手]` | 作为 Python 库调用 `idea_check`；也能去掉 fastmcp，只导入 `sources/` 和 `scoring/` | 完整安装后 venv 103 MB，约 65 个包（fastmcp、uvicorn、starlette、cryptography、rich 等）。只装 httpx 是 16 MB，所以完整安装多出约 87 MB。只用源码时只需要 httpx `[一手]` 实测 | GitHub token 可选；PyPI 搜索要 Libraries.io key；Product Hunt 已在代码里永久关掉 | **默认要**：调 `idea-reality-mcp.onrender.com/api/expand-idea`，由 Claude Haiku 扩写需求，失败才回退到本地词典 `[一手]` `scoring/expansion.py`、`scoring/llm.py` | 不集成，借鉴思路 |
| [idea-validation-agents](https://github.com/MaxKmet/idea-validation-agents) | MIT `[一手]` LICENSE | 487★；最后提交 2026-06-16 | 无：只有 `CLAUDE.md` 和 15 个 skill 提示词 | — | — | 要（依赖 Claude Code / Codex / Cursor） | 不适用；`competitor-mapper` 的提示词可以参考 |
| GitHub 仓库搜索 API | 服务条款，不是开源协议 | — | 直接调用；项目里 `sources/github.py` 已有类似代码 | 0 | 不要；匿名每分钟 10 次，带 token 每分钟 30 次，每次搜索最多 1000 条结果，查询不超过 256 字符 `[一手]` [文档](https://docs.github.com/en/rest/search/search) | 否 | 采用 |
| HN Algolia | 服务条款 | — | 直接调用；`sources/hn_algolia.py` 已在用 | 0 | 不要。官方页面是前端渲染，没抓到限额原文，未核实 | 否 | 采用 |
| StackExchange API（softwarerecs 的回答） | 服务条款 | — | 直接调用；`sources/softwarerecs.py` 已在用 | 0 | 不要；匿名每天 300 次（项目代码注释 `[一手]`） | 否 | 采用，性价比最高 |
| Product Hunt API v2 | 服务条款 | — | GraphQL | 0 | **要 token**；「默认不得用于商业目的」；文档里没有关键词搜索接口 `[一手]` [文档](https://api.producthunt.com/v2/docs) | 否 | 不采用。idea-reality 自己也在 2026-07 关掉了这个源，代码注释说查询是坏的 |
| AlternativeTo | 网站 | — | 只能爬网页 | — | 没找到公开 API `[推断]`；robots.txt 禁止 `q`、`sort`、`page` 等搜索参数，也禁止 API 路径 `[一手]` [robots.txt](https://alternativeto.net/robots.txt) | 否 | 不采用 |

## 三、idea-reality-mcp 的实测与源码要点

环境：Homebrew python3.12 venv，在 scratchpad 里从仓库源码安装（commit `83281b4`）。

### 3.1 中文需求提取不出关键词 `[一手]`

调用 `scoring.engine.extract_keywords`，对比三种输入：抽出来的中文 need、原帖英文标题、手写的英文短查询。

| 需求 | 中文 need 的结果 | 原帖英文标题的结果 |
|---|---|---|
| #13 库版本弃用 / 破坏性变更 | 整句中文原样重复 3 次 | `api deprecations breaking changes tracks`、`api`、`breaking`… |
| #1 禁用 PR | 整句中文原样重复 3 次 | `repository requests disable pull add`… |
| #22 自托管 Web 推送 | 命中它的小中文词典：`notification web`、`push notification`、`alert system` | `push notification server web`、`server`、`web` |
| #18 开车时静音通知 | `notification`、`push notification` | `notifications ask hacker news`（把「Ask Hacker News」当成了关键词） |

英文也有问题：词典会拆出 `api`、`server`、`web`、`versions` 这类单词，单独拿去搜就是噪声。

### 3.2 限流和结果质量 `[一手]`

用 `IDEA_REALITY_API_URL=http://127.0.0.1:9` 让托管扩写失败，强制走本地词典，跑 `idea_check(depth="quick")`：

- 快速模式每个关键词发 2 次 GitHub 搜索（全部结果 + 近 6 个月新建），外加 1 次 HN，并发上限 5，失败重试 3 次。
- 一条需求有 5–6 个关键词，就是 10–12 次 GitHub 搜索，超过匿名每分钟 10 次。实测第一条就出现 403，第二、三条全部 403。
- `reality_signal` 三条都返回 `null`，因为部分源测量失败，这一点它处理得对。
- #13 的「相似项目」前 5 名是 nvm（95k★）、terraform、nvm-windows、pyenv、trpc。这是单词 `versions` 按星数排序的结果，和需求无关。
- 它代码里写的「未设 token 每小时 60 次」是核心 API 的限额，搜索 API 实际是每分钟 10 次。

### 3.3 其他源码事实 `[一手]`

- `tools.py` 每次返回都附 `next_step`，指向 AngelRun 的推广链接（`cta.py`）。
- `sources/producthunt.py` 开头写着「PERMANENTLY DISABLED」，但 README 仍说扫 Product Hunt。
- `TERMS.md`：用它的托管 REST API 时，查询会被记到作者私有的 Discord 频道。本地 stdio 模式不收集。
- **值得借鉴的思路**：「测量失败不等于 0」（被限流时不能当成没人做）；分源证据列表；用「近 6 个月新建占比」看趋势。

## 四、最小自研方案

### 4.1 按需求来源走三条路

| 来源 | 「已有方案」指什么 | 做法 | 每条的请求数 |
|---|---|---|---|
| `stackexchange/softwarerecs` | 回答里推荐的软件 | `/questions/{id;id;…}/answers?filter=withbody`，取回答里的链接，按票数排序 | 一次最多 100 个问题，平均远小于 1 次 |
| reddit / hackernews / arctic_shift 里「想要一个工具」的 | 已经存在的开源项目或产品 | 本地模型生成 3 个英文短查询 → GitHub 仓库搜索 + HN Algolia，命中太少就换下一个查询 → embedding 重排 | GitHub 1–3 次，HN 1–3 次，本地模型 1 次 |
| `github`（对某个产品提的功能请求） | 平台自己有没有实现 | 默认不查，标成「只有平台方能做」。以后可以选做：定期查 issue 是否以 `completed` 关闭 | 0（选做时每条 1 次核心 API） |

按来源分路比让模型判断类型更稳。试跑时模型判断 `tool` / `platform_feature` 并不可靠：#22 被判成 `platform_feature`，第二轮有几条干脆没输出这个字段。

### 4.2 查询生成

- 不直接拿中文 need 去搜。用本地 Qwen3-4B（温度 0）输出 `{"queries": [3 个英文查询，每个 2–4 个词，最具体的放前面]}`。
- **长查询基本搜不到东西。** 第一轮让模型出 3–6 个词的单个查询，6 条里 5 条 GitHub 0 结果，6 条 HN 全部 0 结果，因为两边都是所有词都要命中。改成短查询、命中少于 3 条就换下一个之后，6 条都有结果。
- 实测本地模型改写每条 0.6–1.1 秒（mlx，Mac M5）。

### 4.3 相关性判断

- 用 `qwen3-embedding:0.6b` 算中文 need 和「仓库名: 描述」或「HN 标题」的余弦相似度。不用新模型，跨语言能用。
- 试跑中，明显相关的候选在 0.68–0.81，明显无关的在 0.40–0.60（见 5.2）。阈值先取 0.68，要用标注集定。
- **必须排除需求原帖自己**（按 URL 和 HN item id）。#18 和 #1 的第一名都是原帖。
- 「成熟」的判断：相似度超过阈值，并且 GitHub ≥ 1000★ 或 HN ≥ 100 分。展示分三档：「已有成熟方案」「有类似小项目」「未找到」。**不写「没人做过」**。

### 4.4 调用量、限额和缓存

- 只给进入清单 / 周报的簇代表查，不在抽取主循环里查。每周 30 条左右。
- GitHub 匿名每分钟 10 次，每条 1–3 次，间隔 7 秒，30 条约 10–15 分钟，放进每周的定时任务可以接受。带 `GITHUB_TOKEN` 后快 3 倍。
- **和 GitHub 数据源共用同一个 IP 的额度。** 这次调研中核心 API（每小时 60 次）被耗尽，之后查 issue 状态全部 403。要读 `x-ratelimit-remaining`，额度低了就停，下次续跑，和 softwarerecs 的回填做法一样。
- 结果存 MongoDB 新集合（例如 `prior_art`，按簇 id），记查询时间，30 天内不重查。
- 新依赖：0。查询生成用现有抽取模型，重排用现有 embedding 接口，HTTP 用 `sources/http.py`。镜像体积不变，部署步骤不变。

## 五、实测记录

### 5.1 试跑的 6 条真实需求

需求来自 `out/demands.md`（2026-10-20 生成，48 条），覆盖 softwarerecs、Reddit、HN、GitHub issue 四类来源。

| # | 来源 | 需求 | 实际查询（本地模型生成） | GitHub / HN 命中数 |
|---|---|---|---|---|
| 13 | softwarerecs | 库版本弃用 / 破坏性变更提醒 | `version breaking changes detector` / `library version status tool` | 9 / 10 |
| 20 | softwarerecs | 从真实流量生成 API 回归测试 | `auto generate API regression tests` / `generate API test cases from traffic` | 7 / 4 |
| 22 | softwarerecs | 自托管 Web 推送服务器 | `self-hosted web push server` | 28 / 9 |
| 48 | Reddit | 同一提示发给多个 AI 并对比 | `multi AI prompt browser extension` | 12 / 4 |
| 18 | HN | 开车时静音通知 | `car mode notifications` / `driving notification pause` | 15 / 3 |
| 1 | GitHub issue | GitHub 仓库禁用 PR | `disable pull request` | 26 / 14 |

### 5.2 重排后的前几名，与已知成熟方案对比

| # | 重排前几名（相似度） | 已知的成熟方案 | 判断 |
|---|---|---|---|
| 13 | toolspec-diff（0.75）、bvd（0.72）、Infragistics/BreakingChangesDetector（0.71） | 没有明确的单一成熟工具；Dependabot / Renovate 只解决一部分 `[推断]` | 相关，只有小项目 |
| 20 | API-Testing-Agent（0.79）、APIMemento「从生产流量自动生成回归测试」（0.73）、Launch HN: Codeparrot（0.74，127 分） | **Keploy 18,658★**，用 `keploy in:name` 核实 `[一手]` | 相关，**漏了成熟方案** |
| 22 | New-webpush（0.81）、go-notify-server（0.75）、kukuroo（0.73，131★） | **ntfy 34,926★**、gorush 8,789★ `[一手]` | 相关，**漏了成熟方案**；但 softwarerecs 那条问题唯一的回答就推荐了 ntfy |
| 48 | ExtensionOS（0.79）、Multi-AI-Workspace（0.78）、Show HN: Onit（0.76，174 分） | **ChatALL 16,506★** `[一手]` | 相关，**漏了成熟方案** |
| 18 | 原帖自己（0.74），其余都 < 0.63 且无关 | 手机系统自带的驾驶模式（iOS 驾驶专注模式、Android 驾驶模式）`[推断]` | 漏了；仓库搜索不适合系统功能类需求 |
| 1 | 原 issue 本身（0.63）、「因为 AI 我关了所有外部 PR」（0.60） | 平台功能，不适用 | 走 GitHub issue 那条路，不查 |

**小结**：已知存在成熟方案的 4 条里，仓库搜索加重排一条都没找到成熟方案（0/4），但 4 条都找到了相关的小项目。softwarerecs 的回答这一路 1/1 找到了。

**原因**：GitHub 仓库搜索只匹配名字和描述里的字面词。ntfy 的描述是「Send push notifications to your phone…」，不含 self-hosted / web push。补充实测：改成更宽的 `push notifications` 并按星数排序，第一名就是 ntfy、第二名 apprise。说明「宽查询 + 按星数排序 + 重排」可能补上召回，但这要用标注集验证，不能凭这一条下结论。

### 5.3 否掉的思路：让本地模型直接列出已有工具

让 Qwen3-4B 凭自身知识列出 4 个已有工具：

- 大多是编造的仓库名，例如 `github/detector`、`postman/postman`、`notifyjs/notify`、`google/automotive`。
- 只有 #13 的 Dependabot / Renovate 和 #18 的「开车勿扰」沾边。

4B 模型的记忆不能当证据来源，只能用接口查到的东西。

### 5.4 请求次数（全部匿名、只读）

| 目标 | 次数 | 说明 |
|---|---|---|
| GitHub 搜索 API | 约 70 | 其中约 40 次是 idea-reality 快速模式触发的，包括 403 后的重试；自研原型 15 次；核实和宽查询 9 次 |
| GitHub 核心 API | 4 | 查 issue 状态和仓库信息，全部 403（这个 IP 的每小时额度已经用完） |
| HN Algolia | 约 30 | idea-reality 约 16 次，自研原型 14 次 |
| StackExchange | 1 | 3 个问题的回答一次取回，剩余额度 292 |
| PyPI JSON | 1 | |
| 本地模型 | mlx 18 次，Ollama embeddings 6 次 | 不出本机 |

脚本放在 scratchpad，没有进仓库：`cases.py`（6 条需求）、`kw.py`、`check.py`（idea-reality）、`selfbuild.py`（自研原型）、`embed_rank.py`（在 web 容器里跑重排）、`nominate.py`、`probe_gh.py`、`probe_se.py`。复现时，重排脚本需要先用 `docker cp` 放进 `demand-radar-web-1` 再执行。

## 六、怎么量化

参照 `cluster_pairs.json` 的做法，新建 `eval/prior_art.json`。

**标注集**：约 30 条需求，按来源分层抽：softwarerecs 10 条、Reddit / HN 工具类 12 条、GitHub issue 5 条、系统功能类 3 条。每条标：

- `mature_exists`：是 / 否 / 不确定
- `known_solutions`：1–3 个标注人自己搜到的 URL
- `route`：走哪条路

**离线评测**：第一次运行把各接口的原始响应缓存到 `eval/prior_art_cache/`，之后的评测只读缓存。改查询策略或阈值时，不用重新发请求就能对比。

**指标**：

| 指标 | 含义 | 本次 6 条手工试跑的基线 |
|---|---|---|
| 成熟方案召回率 | `mature_exists=是` 的需求里，提示「已有成熟方案」并给出正确链接的比例 | 仓库搜索这一路 0/4 |
| 「成熟」判断的精确率 | 提示「已有成熟方案」的里面，真的有的比例 | 没有提示过，无法计算 |
| 前 3 条命中率 | 前 3 个链接里至少有一个属于 `known_solutions` 或被人工判为相关 | 约 4/5（#18 不算） |
| 原帖自命中率 | 前 3 名里出现需求原帖的比例，目标为 0 | 2/6 |
| 每条请求数 | 用来估算限额 | 2–6 次 |

标注集的 `known_solutions` 一定不全。所以「前 3 条命中」允许人工补判相关，补判结果写回标注文件，下次直接用。

建议命令形式：`python -m demand_radar prior-art --eval -v`，和 `cluster --eval` 风格一致。

## 七、需要用户判断的点

1. **缓存接口结果再分发**：评测缓存里会存 GitHub 仓库描述、HN 标题、StackExchange 回答里的链接。StackExchange 内容是 CC BY-SA，要署名。是否把缓存提交进公开仓库，需要你看各自条款后决定。只存链接、星数和标题会更稳。
2. **是否鼓励用户配 `GITHUB_TOKEN`**：不配也能用，只是慢 3 倍，并且和 GitHub 数据源抢同一份额度。这关系到「零 key」的口径。
3. **界面措辞**：提示写成「找到的类似项目」，还是在有把握时直接写「已有成熟方案」？后者对用户更有用，但当前召回率很低，误导风险更大。
4. **不建议用 idea-reality 的托管 API**：会把需求文本发给第三方，并记录到作者的 Discord。如果以后有人提议「作为可选云服务接入」，这一条要你拍板。

## 八、建议的路线图改动

由主会话决定，本报告没有改 ROADMAP.md。

- 把 v0.3 的「『已有方案』提示：优先评估集成现有的 idea-validation 开源项目」改为：「『已有方案』提示：已评估 idea-reality-mcp，不集成（托管云模型、不支持中文、限流），自研按来源分三路的小模块，见 docs/research/2026-10-20-existing-solutions.md」。
- 这一项拆成两步，验收也分开：
  1. **先评测**：`eval/prior_art.json` 30 条标注 + 离线缓存 + `prior-art --eval`。
  2. **再功能**：softwarerecs 回答这一路先上，它便宜、准、零风险；工具类的搜索加重排这一路，要等成熟方案召回率达到一个你定的门槛（建议 ≥ 50%）再上看板。
- v0.3 的验收（合并加看板）已经达成，不必等这一项。可以把它挪到 v0.3.x 或 v0.4 的第一项。
- tech-plan.md v0.3 第 3 节「查 Product Hunt、GitHub 等」里的 Product Hunt 应该去掉：要 token、默认禁止商用、没有搜索接口。

## 九、对已有文档的改动

- `docs/landscape.md` 第 16 行「相邻方向」：补上项目名 idea-reality-mcp 和链接，星数更新为 827★（截至 2026-10-20）。原来的「290+★」来自它仓库描述里没更新的文字。另外补充了维护模式、默认调托管云模型、Product Hunt 已停用三点，并加上 idea-validation-agents。
- `docs/landscape.md` 第五节借鉴表：「idea-validation 项目」一行改成 idea-reality-mcp，结论改为「只借鉴思路，不直接集成」，并链接本报告。

## 参考链接（访问日期均为 2026-10-20）

- idea-reality-mcp 仓库、LICENSE、TERMS.md、源码：https://github.com/mnemox-ai/idea-reality-mcp
- idea-reality-mcp 的 PyPI 页面：https://pypi.org/project/idea-reality-mcp/
- idea-validation-agents：https://github.com/MaxKmet/idea-validation-agents
- GitHub 上 `idea-validation` 标签的仓库（按星数排序，共 133 个）：https://github.com/topics/idea-validation
- GitHub 搜索 API 限额：https://docs.github.com/en/rest/search/search
- Product Hunt API v2 文档：https://api.producthunt.com/v2/docs
- AlternativeTo robots.txt：https://alternativeto.net/robots.txt
- 核实用的成熟项目：https://github.com/binwiederhier/ntfy 、https://github.com/keploy/keploy 、https://github.com/ai-shifu/ChatALL 、https://github.com/appleboy/gorush
- softwarerecs 问题 95677（回答推荐了 ntfy）：https://softwarerecs.stackexchange.com/questions/95677/web-push-notifications-server
