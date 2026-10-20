# 非中国出品的开源模型能不能用在需求雷达上

调研日期：2026-10-20。只查资料，**没有下载模型，也没有跑 eval**。下面的「预计」都是 `[推断]`，要以实测为准。

## 结论与建议

1. **能用，但中文质量预计比 Qwen 低一档，要靠实测确认。** 主力候选只有两家：
   - **Mistral 的 Ministral 3**（法国，Apache-2.0，官方支持中文，Instruct 版本没有思考模式）。
   - **Google 的 Gemma 4**（美国，HF 上标 Apache-2.0，思考模式要显式开启，默认是关的）。
   其他家族在中文、协议或思考模式上至少有一项不合格。
2. **旧结论要推翻一条。** model-selection.md 第四节写「Gemma 4 在 MLX 社区最小的是 12B，没有 4B 版本」，这条不对。
   - `mlx-community/gemma-4-e2b-it-4bit` 和 `gemma-4-e4b-it-4bit` 在 2026-04-02 就已经发布。
   - mlx-lm 源码里的 `gemma4.py` 加载时会丢掉视觉和音频权重，只跑文本部分。
   - 已在 model-selection.md 里加了更正。
3. **嵌入模型的首选是 `embeddinggemma-2:270m`**（Apache-2.0，100+ 种语言，Ollama 上 304MB）。
   - 但本项目合并评测时向量化的主要是**中文短句**。非中国模型里没有一个公布过 C-MTEB 分数。
   - bge-m3 的 MMTEB 比 qwen3-embedding:0.6b 低 4.8 分，在本项目的 23 对标注上却打成平手。所以榜单对本项目的预测力很弱。
   - **嵌入模型最便宜、最该先测。**
4. **models.toml 建议**：按模型标出 `extract_origin` 和 `embed_origin`，再加一组非中国档位。`recommend --exclude-origin cn` 跳过命中的档位，档位全被过滤掉时直接报错，不要悄悄退回 Qwen。草案见第四节。
5. **建议实测 5 项，合计约 2.5 小时。** 嵌入模型放第一项（约 15 分钟）。

## 一、按设备档位的非中国推荐（全部 `[推断]`）

Qwen 基线（M5 实测，来自主会话）：
- **抽取**：Qwen3-4B-Instruct-2507 MLX，帖子 F1 中文 92.4%、英文 90.9%，每条 2.6 秒；容器 CPU 约 7 秒一条。
- **嵌入**：qwen3-embedding:0.6b，合并 F1 77.8%。

| 档位 | 首选（抽取） | 备选（抽取） | 嵌入 | 和 Qwen 相比，中文质量 / 速度 |
|---|---|---|---|---|
| 纯 CPU 8GB | `ministral-3:3b`（3.0GB） | `granite4.1:3b`（2.1GB） | `embeddinggemma-2:270m` | 中文 F1 预计低 3–8 个点。速度持平或略快（约 6 秒一条） |
| 纯 CPU 16GB | `gemma4:e4b`（6.6GB，含视觉和音频编码器） | `ministral-3:8b`（6.0GB） | 同上 | 质量接近 Qwen3-4B。慢 1.3–2 倍（9–14 秒一条） |
| Apple 16GB | `mlx-community/Ministral-3-3B-Instruct-2512-4bit`（2.75GB） | `mlx-community/gemma-4-e4b-it-4bit`（5.15GB） | 同上（Mac 上原生 Ollama） | 3B：低 3–8 个点，约 2.5 秒。E4B：低 0–5 个点，约 3 秒 |
| Apple 32GB 以上 | `mlx-community/gemma-4-26b-a4b-it-4bit`（15.3GB，MoE，激活 3.8B） | `mlx-community/Ministral-3-14B-Instruct-2512-4bit`（8.4GB） | 同上 | 26B-A4B 可能追平甚至超过 Qwen3-4B，约 3–4 秒。风险是关掉思考后仍会输出空的 thought 块，见第二节 |
| NVIDIA 8GB | `ministral-3:8b`（6.0GB） | `gemma4:e4b`（6.6GB，偏紧） | 同上 | 接近 Qwen3-4B，GPU 上 2–4 秒 |
| NVIDIA 16GB 以上 | `ministral-3:14b`（9.1GB） | `gemma4:12b`（8.0GB）；24GB 可上 `gemma4:26b`（18GB） | 同上 | 预计持平或更好 |

推断依据：
- **只有一处是同一张表里和 Qwen 直接对比**：Ministral 3 的模型卡，base 模型 5-shot Multilingual MMLU。
  - 3B 是 0.652，Qwen3-4B Base 是 0.677。
  - 8B 是 0.706，Qwen3-8B Base 是 0.700。
  - 14B 是 0.742，Qwen3-14B Base 是 0.754。
- Gemma 4 的 MMMLU（E4B 76.6、26B-A4B 86.3）是**开思考**测的。技术报告 Table 5 注明「未标注的都是 thinking 模式」，所以关掉思考后的分数会更低。
- **速度**：
  - 稠密模型按「带宽 ÷ 体积」估算。
  - Gemma 4 E 系列用 PLE 查表，26B-A4B 只激活 3.8B，都按有效参数或激活参数估算。
  - 占用内存仍按文件体积算。

## 二、抽取模型逐个看

「中文」一列以模型卡正文为准，不看 HF 的 language 标签。

| 模型 | 出品方 | 参数 | q4 体积（Ollama / MLX） | Ollama tag | mlx-community | 协议 | 中文 | 思考模式 | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| **Ministral 3 3B / 8B / 14B Instruct 2512** | Mistral，法国 | 语言模型 3.4B / 8.4B / 13.5B，另带 0.4B 视觉编码器 | 3.0 / 6.0 / 9.1GB；2.75 / 5.6 / 8.4GB | `ministral-3:3b/8b/14b` | `Ministral-3-{3B,8B,14B}-Instruct-2512-4bit` | Apache-2.0 | 官方列出 zh | Instruct 版没有思考模式，Reasoning 是另一个模型 | **主力候选**。卡片写「native function calling and JSON outputting」，建议温度低于 0.1，和项目的温度 0 一致 |
| **Gemma 4 E2B / E4B** | Google，美国 | 有效 2.3B / 4.5B（含嵌入表 5.1B / 8B） | 4.6 / 6.6GB；3.55 / 5.15GB | `gemma4:e2b`、`gemma4:e4b` | `gemma-4-e2b-it-4bit`、`gemma-4-e4b-it-4bit` | 见下方协议表 | 35+ 种语言，140+ 种预训练，卡片没列明细；MMMLU 67.4 / 76.6（开思考） | 系统提示里加 `<\|think\|>` 才开启；关闭时 E2B/E4B 不输出 thought 块 | **主力候选**。Ollama 的包含视觉和音频编码器，CPU 8GB 档偏大 |
| Gemma 4 12B / 26B-A4B | Google | 12B / 25.2B（激活 3.8B） | 8.0 / 18GB；11.0 / 15.3GB | `gemma4:12b`、`gemma4:26b` | `gemma-4-12B-it-qat-4bit`、`gemma-4-26b-a4b-it-4bit` | 同上 | MMMLU 83.4 / 86.3（开思考） | 关掉后**仍会输出空的 thought 块** | 大内存档候选。先确认空块会不会混进 content、打断 JSON 解析 |
| Granite 4.1 3B / 8B | IBM，美国 | 3B / 8B | 2.1 / 5.3GB；2.13 / 5.24GB | `granite4.1:3b/8b` | `granite-4.1-3b-4bit`、`granite-4.1-8b-4bit` | Apache-2.0 | 官方列出 zh；MMMLU 57.6 / 64.8（instruct，5-shot） | 无 | 备选。协议最干净，中文偏弱 |
| Granite 4.2 3B / 8B | IBM | 3B / 8B | 2.2 / 5.3GB；MLX 无 | `granite4.2:3b/8b` | 无 | Apache-2.0 | 同 4.1 | **默认开启**，卡片要求所有模式都用 temperature=1.0 | 排除。和温度 0 冲突 |
| Phi-4-mini-instruct | Microsoft，美国 | 3.8B | 2.5GB；2.16GB | `phi4-mini` | `Phi-4-mini-instruct-4bit` | MIT | 列出 zh，但卡片说「主要用英文训练」；Multilingual MMLU 49.3 | 无（推理版是另一个模型） | 排除。中文弱 |
| Phi-4 14B | Microsoft | 14B | 9.1GB；8.25GB | `phi4` | `phi-4-4bit` | MIT | 只列英文 | 无 | 排除 |
| Llama 3.2 1B / 3B | Meta，美国 | 1.2B / 3.2B | 1.3 / 2.0GB；1.81GB（3B） | `llama3.2:1b/3b` | `Llama-3.2-3B-Instruct-4bit` | Llama 3.2 Community License | 官方 8 种语言，没有中文 | 无 | 排除。没有中文，协议有附加条款 |
| Llama 4 Scout | Meta | 109B MoE | 67GB | `llama4:scout` | `Llama-4-Scout-17B-16E-Instruct-4bit` | Llama 4 License | — | — | 排除。太大 |
| SmolLM3 3B | Hugging Face，美国 / 法国 | 3B | 无 Ollama；1.73GB | 无（只有 smollm2） | `SmolLM3-3B-4bit` | Apache-2.0 | 原生支持 6 种欧洲语言，**没有中文**（标签里有 zh，正文没有） | **默认开启**，用 `/no_think` 关 | 排除 |
| OLMo 3 7B Instruct | AI2，美国 | 7B | 4.5GB；4.11GB | `olmo-3:7b-instruct` | `Olmo-3-7B-Instruct-4bit` | Apache-2.0；卡片另写「intended for research and educational use」 | 只有英文 | Instruct 版无 | 排除。只有英文 |
| Nemotron 3 Nano 4B | NVIDIA，美国 | 4B | 2.8GB；2.24GB | `nemotron-3-nano:4b` | `NVIDIA-Nemotron-3-Nano-4B-4bit` | NVIDIA Nemotron Open Model License | 只有英文；卡片写「Improved using Qwen」 | **默认开启**，用 `enable_thinking=False` 关 | 排除 |
| Nemotron Labs Dory 1.0 4B | NVIDIA | 4B | GGUF 已出，需要特制的 llama.cpp | 无 | 无 | OpenMDW-1.1 | 只有英文 | vLLM 下一直开着 | 排除。2026-10-16 才发布，生态还不成熟 |
| LFM2.5 1.2B Instruct | Liquid AI，美国 | 1.2B | Ollama 无（只有 thinking 版）；MLX 有 | 无 | `LFM2.5-1.2B-Instruct-4bit` | LFM Open License v1.0 | 列出 zh | 无 | 排除。太小，协议有年收入门槛 |
| LFM2.5 2.6B / 8B-A1B | Liquid AI | 2.6B / 8.3B（激活 1.5B） | —；1.52 / 4.76GB | `lfm2.5:8b` | `LFM2.5-2.6B-4bit`、`LFM2.5-8B-A1B-MLX-4bit` | 同上 | 列出 zh | **一直开着**（卡片：「always thinks before it answers」） | 排除 |
| Command R7B、Aya Expanse 8B、Tiny Aya | Cohere，加拿大 | 7–8B | 5.1GB | `command-r7b`、`aya-expanse:8b` | 有 | **CC-BY-NC-4.0** | 列出 zh | 无 | 排除。不能商用 |
| Gemma 3 / Gemma 3n | Google | 1B–27B | — | `gemma3`、`gemma3n` | 有 | Gemma Terms of Use | 140 种语言 | 无 | 已被 Gemma 4 取代，不再推荐 |
| Mistral Small 3.2 24B（2506） | Mistral | 24B | 15GB；13.3GB | `mistral-small3.2:24b` | `Mistral-Small-3.2-24B-Instruct-2506-4bit` | Apache-2.0 | 列出 zh | 无 | 32GB 档的备选。比 Gemma 4 26B-A4B 慢：稠密 24B，估算 M5 上每条 10 秒以上。新的 Mistral Small 4 是 119B，太大 |
| Granite 4.0-H Tiny（MoE） | IBM | 7B，激活 1B | 4.2GB；3.91GB | `granite4:tiny-h` | `granite-4.0-h-tiny-4bit` | Apache-2.0 | 列出 zh；MMMLU 61.87 | 无 | 可作 CPU 档备选：只激活 1B，CPU 上可能比 3B 稠密模型还快。中文偏弱 |
| Nemotron 3 Nano 30B-A3B / Nano 9B v2 | NVIDIA | 30B（激活约 3B）/ 9B | 18GB / — | `nemotron-3-nano:30b` | 有 | NVIDIA Nemotron Open Model License | 都不支持中文（30B 卡片列 6 种语言，写「Improved using Qwen」） | 推理和非推理合在一个模型里 | 排除 |
| LFM2.5 24B-A2B | Liquid AI | 24B，激活 2.45B | 15GB；有 | `lfm2.5:24b` | `LiquidAI/LFM2.5-24B-A2B-MLX-4bit` | LFM Open License | 列出 zh | **一直开着** | 排除 |
| Phi-mini-MoE / Phi-tiny-MoE、Llama 3.1 8B、Mistral Nemo 12B、OLMoE | 美国 / 法国 | — | — | 部分有 | 部分有 | MIT / Llama / Apache | Phi MoE 和 OLMoE 只有英文；Llama 3.1 的 8 种语言里没有中文；Nemo 是 2024 年的旧模型 | — | 排除 |

### JSON 稳定性：取决于运行时，不全看模型

| 运行时 | 机制 | 对选模型的影响 |
|---|---|---|
| Ollama（容器 / NVIDIA） | `/v1` 收到 `response_format: json_object` 时转成 `format: "json"`（`openai/openai.go`，`[一手 源码]`），由 Ollama 做约束解码 | 输出基本都是合法 JSON，换模型时 JSON 风险小。字段对不对还得看模型 |
| mlx_lm.server（Mac） | `server.py` 里**没有处理 `response_format`**（grep 无结果，`[一手 源码]`），没有约束解码 | 全靠模型自己守格式，加上项目的 `max_retries=2`。Gemma 4 12B 以上的空 thought 块就落在这条路径上 |

Instructor 的 `Mode.JSON` 会发 `response_format: json_object`，并把 schema 写进系统提示（`[二手]` Instructor 文档，这一轮没核对源码）。

各模型公开的相关指标：

| 模型 | 公开指标 |
|---|---|
| Gemma 4 | IFEval：E2B 94.6、E4B 96.7、12B 97.2、26B-A4B 98.5（技术报告 Table 5，**开思考**）。没有结构化输出基准 |
| Ministral 3 | 卡片原文「native function calling and JSON outputting」，没给分数 |
| Granite 4.1 | BFCL v3（工具调用）：3B 60.8、8B 68.3 |
| LFM2.5 | 卡片推荐用于 structured outputs（但一直开着思考，已排除） |
| 其他 | 未公布 |

### 思考模式

项目里现在的做法：
- `extract.py` 不传 `chat_template_kwargs`，也不传 `reasoning_effort`。所以只有「默认关思考」的模型能直接用。上表的主力候选都满足。
- **mlx_lm.server**：支持启动参数 `--chat-template-args '{"enable_thinking":false}'`，单个请求也可以带 `chat_template_kwargs`（`mlx_lm/server.py`，`[一手 源码]`）。
- **Ollama**：`/v1` 接口认 `reasoning_effort: "none"`，会映射成 `think=false`（`openai/openai.go` 的 `ThinkingFromReasoningEffort`，`[一手 源码]`）。
- 将来要用默认开思考的模型，`extract.py` 加一个可选参数就行。这一轮不改代码。

## 三、嵌入模型

本项目向量化的是模型归纳出的 `need` 一句话（`cluster.py` 的 `text_of`）。默认输出是中文，所以**主要看中文短句的相似度**。`cluster.py` 调 `/v1/embeddings` 时**不加任务前缀**。e5、nomic、EmbeddingGemma 都要求加前缀，不加对它们不利，测的时候要注意。

| 模型 | 出品方 | 参数 | Ollama | MLX | 协议 | 中文 / 多语言证据 | 结论 |
|---|---|---|---|---|---|---|---|
| **EmbeddingGemma 2** | Google，美国 | 740M（纯文本部分 270M） | `embeddinggemma-2:270m`（304MB，只含文本，q8_0）。v0.40.0 的 release note 只写了「MLX now has support for an embedding model: embeddinggemma-2」。Linux 容器里固定的 0.40.6 能不能跑，**要在实测第 1 项里先确认** | `mlx-community/embeddinggemma-2-4bit` | Apache-2.0 | 100+ 种语言；MTEB multilingual v2 61.36；没有 C-MTEB | **首选，待测** |
| EmbeddingGemma 300m | Google | 300M | `embeddinggemma`（622MB） | 有 | Gemma Terms of Use（gated） | MTEB multilingual v2 61.15 | 被 v2 取代 |
| Granite Embedding 278m multilingual（R1） | IBM，美国 | 278M | `granite-embedding:278m`（563MB） | — | Apache-2.0 | 官方列出 zh；ML 检索 52.2 | **备选**（Ollama 上能直接用） |
| Granite Embedding 311m multilingual R2 | IBM | 311M | 无（卡片说可以转 GGUF） | — | Apache-2.0 | 52 种语言重点支持，含 zh；ML 检索 65.2 | 质量更好，但要自己转格式，不适合默认 |
| multilingual-e5-large-instruct | Microsoft，美国 | 560M | 无官方 tag | — | MIT | **MMTEB 63.22**，和 qwen3-embedding:0.6b 的 64.33 接近；**C-MTEB 58.08**，qwen 是 66.33 | 非中国模型里唯一有 C-MTEB 分数的。缺官方 Ollama tag |
| nomic-embed-text-v2-moe | Nomic，美国 | 475M（激活 305M） | `nomic-embed-text-v2-moe`（958MB） | — | Apache-2.0 | 约 100 种语言；MIRACL 65.8 | 备选。v1 不懂中文，v2 是多语言版，要测了才知道 |
| snowflake-arctic-embed-l-v2.0 | Snowflake，美国 | 568M | `snowflake-arctic-embed2`（1.2GB） | — | Apache-2.0 | 多语言；底座是 **BAAI 的 bge-m3-retromae** | 备选。出品方是美国公司，但底座来自中国，归哪一类由用户定 |
| Nemotron-3-Embed-1B | NVIDIA | 1.14B | 无 | — | OpenMDW-1.1（宽松） | 列出 zh；底座是 Ministral-3-3B 剪枝；卡片里列了多款 Qwen 模型（用途待核实） | 暂不推荐。没有 Ollama tag |
| paraphrase-multilingual | sentence-transformers，德国 | 278M | `paraphrase-multilingual`（563MB） | — | Apache-2.0 | 老模型（2019–2021） | 不推荐 |

## 四、models.toml 设计建议

这个 worktree 基于 2fc3437，没有 models.toml 和 recommend.py。下面的草案按主线 `0caf7e8` 里的版本写。

- **要加地区字段，而且按模型标，不按档位标。** 同一个档位里，抽取模型和嵌入模型可能来自不同地区。
  - `load_tiers()` 只校验 id、runtime、extract、embed 四个字段，新增字段会被忽略，旧文件照样能用。
- **`recommend` 加 `--exclude-origin cn`**（可以写多个，逗号分隔）。
  - 过滤发生在 `choose()` 里：跳过 `extract_origin` 或 `embed_origin` 命中的档位，继续往下匹配。
  - 档位全被过滤掉时**报错退出**，并提示用 `--list` 查看。**不要悄悄退回 Qwen。**
  - 现在兜底的 `minimal` 档用的是 Qwen，所以必须再加一个非中国的兜底档。
- **不加过滤时行为不变**：Qwen 档位放在前面，从上往下取第一个匹配的。
- 建议同时加 `license` 字段，`recommend --list` 时显示出来。

```toml
# origin 用 ISO 国家码，指出品方所在地（不看底座和训练数据来源，见「需要用户判断的点」）
# recommend --exclude-origin cn：跳过 extract_origin 或 embed_origin 命中的档位

[[tier]]
id = "apple-16g"
name = "Apple Silicon，16GB 内存以上"
when = { apple_silicon = true, min_ram_gb = 16 }
runtime = "mlx"
extract = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
extract_origin = "cn"
embed = "qwen3-embedding:0.6b"
embed_origin = "cn"
license = "Apache-2.0"
sec_per_item = 2.6
quality = "帖子 F1 中文 92.4% / 英文 90.9%；评论 F1 中文 80.6% / 英文 83.2%"
measured = true

[[tier]]
id = "apple-32g-intl"
name = "Apple Silicon，32GB 以上（非中国模型）"
when = { apple_silicon = true, min_ram_gb = 32 }
runtime = "mlx"
extract = "mlx-community/gemma-4-26b-a4b-it-4bit"
extract_origin = "us"
embed = "embeddinggemma-2:270m"
embed_origin = "us"
license = "Apache-2.0"
sec_per_item = 3.5
quality = "未实测；MMMLU 86.3（开思考，官方）"
measured = false
note = "关思考后仍输出空 thought 块，需确认 JSON 解析不受影响"

[[tier]]
id = "apple-16g-intl"
name = "Apple Silicon，16GB 以上（非中国模型）"
when = { apple_silicon = true, min_ram_gb = 16 }
runtime = "mlx"
extract = "mlx-community/Ministral-3-3B-Instruct-2512-4bit"
extract_origin = "fr"
embed = "embeddinggemma-2:270m"
embed_origin = "us"
license = "Apache-2.0"
sec_per_item = 2.5
quality = "未实测"
measured = false

[[tier]]
id = "nvidia-8g-intl"
name = "NVIDIA 显存 8GB 以上（非中国模型）"
when = { min_vram_gb = 8 }
runtime = "ollama"
extract = "ministral-3:8b"
extract_origin = "fr"
embed = "embeddinggemma-2:270m"
embed_origin = "us"
license = "Apache-2.0"
measured = false

[[tier]]
id = "cpu-8g-intl"
name = "只用 CPU，8GB 以上（非中国模型）"
when = { min_ram_gb = 8 }
runtime = "ollama"
extract = "ministral-3:3b"
extract_origin = "fr"
embed = "embeddinggemma-2:270m"
embed_origin = "us"
license = "Apache-2.0"
sec_per_item = 6.0
measured = false

[[tier]]
id = "minimal-intl"          # 非中国兜底，过滤后不至于无档可选
name = "内存不到 8GB（非中国模型）"
when = {}
runtime = "ollama"
extract = "granite4.1:3b"
extract_origin = "us"
embed = "embeddinggemma-2:270m"
embed_origin = "us"
license = "Apache-2.0"
measured = false
```

另一种做法是把模型单独建一张表（`[[model]]`，带 origin、license、size），档位只引用模型 id。这样更干净，但要改 `load_tiers` 的结构。等非中国档位实测过、确定要长期维护时再改。

## 五、建议的实测清单（这一轮没有跑）

按性价比排序。抽取模型每项都要跑：帖子 116 条中英文各一遍，评论 150 条中英文各一遍。
**每个抽取模型的第一个检查点**：能不能加载；关掉思考后输出是不是干净的 JSON。

| # | 测什么 | 命令 | 预计耗时 |
|---|---|---|---|
| 1 | 嵌入模型 4 个：`embeddinggemma-2:270m`、`granite-embedding:278m`、`nomic-embed-text-v2-moe`、`snowflake-arctic-embed2` | `docker compose exec ollama ollama pull <tag>`<br>`docker compose run --rm --entrypoint python app -m demand_radar cluster --eval -v --embed-model <tag>` | 下载约 3GB，每个评测 1–2 分钟，合计约 15 分钟 |
| 2 | Ministral-3-3B（MLX） | `mlx_lm.server --model mlx-community/Ministral-3-3B-Instruct-2512-4bit --port 8765`<br>`python3.12 -m demand_radar eval --provider local/mlx-community/Ministral-3-3B-Instruct-2512-4bit --base-url http://127.0.0.1:8765/v1 --lang zh -v`（再跑一遍 `--lang en`，评论加 `--eval-set eval/review_set.json`） | 约 25 分钟 |
| 3 | Gemma-4-E4B（MLX） | 同上，换成 `mlx-community/gemma-4-e4b-it-4bit` | 约 30 分钟 |
| 4 | Gemma-4-26B-A4B（MLX，需要 32GB） | 同上，换成 `mlx-community/gemma-4-26b-a4b-it-4bit`。先用 1 条请求看 content 里有没有空 thought 块 | 下载 15GB，跑约 40 分钟 |
| 5 | `ministral-3:3b`（容器 CPU，对应 cpu-8g 档） | `docker compose exec ollama ollama pull ministral-3:3b`<br>`docker compose run --rm --entrypoint python app -m demand_radar eval --provider ollama/ministral-3:3b --lang zh` | 只测帖子，约 30 分钟 |

嵌入模型如果都明显低于 qwen3-embedding:0.6b，再考虑两件事：
- 给 `cluster.py` 加可选的任务前缀，然后重测。
- 补测 multilingual-e5-large-instruct。它没有官方 Ollama tag，需要找社区版或自己转 GGUF。

## 需要用户判断的点

1. **Gemma 4 的协议，三方说法不一致。** 我不替你下结论。
   - 上游 `google/gemma-4-*` 在 HF 上标的是 `license:apache-2.0`。卡片链接的许可页标题是「Apache License 2.0」，但这页已标为 deprecated。
   - 上游仓库根目录**没有 LICENSE 文件**（404）。旁边的「Prohibited use」页面没有读，不知道它对 Gemma 4 是否适用。
   - `mlx-community/gemma-4-e2b/e4b-it-4bit` 却标的是 `license:gemma`。
2. **「非中国」按什么算。**
   - 按出品方所在地：Snowflake、NVIDIA 都算美国。
   - 按血缘：arctic-embed-l-v2 的底座是 BAAI 的 bge-m3-retromae；Nemotron-3-Nano-4B 卡片写「Improved using Qwen」；Nemotron-3-Embed 卡片里列了 Qwen 模型。按这个口径，它们都算中国。
   - multilingual-e5 由 Microsoft 发布，但作者团队在微软亚洲研究院（北京；依据是 E5 论文的作者署名，`[二手]`，这一轮没有核对原文）。按出品方算是美国公司，按团队所在地另当别论。
   - 草案按出品方算。
3. **其他协议的附加条款**，在本项目里只影响「推荐给用户」，不影响 MIT 代码本身：
   - **Llama 3.2**：分发时要显示「Built with Llama」；要遵守 AUP；月活超过 7 亿要另行申请。
   - **LFM**：年收入 1000 万美元以上的商用不在授权范围内。
   - **Cohere**：CC-BY-NC，不能商用。
   - **OLMo 3**：Apache-2.0，但卡片写「intended for research and educational use」。
4. **要不要为非中国档位维护第二套推荐。** 每加一个档位，就多一份实测和维护成本。可以先只保留 Apple 16GB 和 CPU 8GB 两档。

## 改了哪些已有文档

- `docs/model-selection.md` 第四节「Gemma 4」那一行：加了更正，指向本报告。

## 建议的路线图改动

- 把「非中国模型档位（实测后决定）」放进 ROADMAP 的待用户决定区，前置条件是第五节第 1–2 项实测。
- `extract.py` 可选传 `reasoning_effort` / `chat_template_kwargs`；`cluster.py` 可选加嵌入前缀。两处都是小改动，等实测证明需要再做。

## 参考链接（访问日期都是 2026-10-20）

`[一手]` 模型卡、LICENSE 原文、HF API、Ollama 库页面、源码：
- Gemma 4 卡片：https://huggingface.co/google/gemma-4-E4B-it ；许可页：https://ai.google.dev/gemma/docs/gemma_4_license ；技术报告：https://arxiv.org/html/2607.02770
- Ollama gemma4 标签：https://ollama.com/library/gemma4/tags
- Ministral 3：https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512 ；https://ollama.com/library/ministral-3
- Granite 4.1 / 4.2：https://huggingface.co/ibm-granite/granite-4.1-8b ；https://huggingface.co/ibm-granite/granite-4.2-8b
- Phi-4-mini（MIT LICENSE 原文）：https://huggingface.co/microsoft/Phi-4-mini-instruct
- Llama 3.2 LICENSE：https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE
- SmolLM3：https://huggingface.co/HuggingFaceTB/SmolLM3-3B
- OLMo 3：https://huggingface.co/allenai/Olmo-3-7B-Instruct
- Nemotron 3 Nano 4B：https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 ；协议：https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/
- Dory：https://huggingface.co/nvidia/NVIDIA-Nemotron-Labs-Dory1.0-4B-BF16 ；OpenMDW：https://openmdw.ai/license/1-1/
- LFM2.5（LICENSE 原文）：https://huggingface.co/LiquidAI/LFM2.5-2.6B
- Cohere（HF API 的 license 标签）：https://huggingface.co/CohereLabs/c4ai-command-r7b-12-2024
- EmbeddingGemma 2：https://huggingface.co/google/embeddinggemma-2 ；https://ollama.com/library/embeddinggemma-2
- Granite Embedding R2：https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2
- MMTEB / C-MTEB 对照表（Qwen3-Embedding 卡片引自 MTEB 榜单）：https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- arctic-embed-l-v2：https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0 ；nomic v2：https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe
- mlx-lm 源码（v0.32.2）：`mlx_lm/models/gemma4.py`、`mistral3.py`、`server.py`，https://github.com/ml-explore/mlx-lm
- Ollama 源码：`openai/openai.go`，https://github.com/ollama/ollama ；v0.40.0 release note：https://github.com/ollama/ollama/releases/tag/v0.40.0

`[二手]` 中文基准：
- 搜了 C-Eval、CMMLU、SuperCLUE。没有找到 Gemma 4、Ministral 3、Granite 4.1 的可信中文基准，所以报告里没有引用。
