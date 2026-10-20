# Demand Radar

English | [中文](README.md)

Find needs worth building from what people complain about and wish for online. Demand Radar pulls public posts from Hacker News, Reddit, StackExchange and GitHub, uses a **local LLM** to turn them into structured needs, scores and ranks them, and writes a weekly digest.

- **Fully local**: the model runs on your own machine (Ollama + Qwen). No API keys, and your data never leaves it.
- **One-command setup**: `docker compose up`
- **Measurable quality**: a hand-labeled evaluation set and an `eval` command with reproducible metrics

## What you get

Every need comes with a **verbatim quote** from the original post, so you can check at a glance whether the model got it right:

```markdown
### A menu bar app that monitors one specific app's resource usage

- Domain: developer tools
- Pain point: Activity Monitor is hard to read and can't focus on a single app
- Quote: “Is there an app that monitors a specific app from the menu bar?”
- clarity 4 · intensity 3 · feasibility 5 · rank 13.5
- Source: [reddit/macapps](https://www.reddit.com/r/macapps/...) Is there an app that monitors...
```

## Quick start (Docker)

All you need is [Docker](https://docs.docker.com/get-docker/). No Python, MongoDB or Ollama to install:

```bash
cd demand-radar
DR_OUTPUT_LANGUAGE=en docker compose up   # first run downloads the model (~2.5 GB)
```

This starts three containers: **app** (fetch + extract), **mongo** (storage) and **ollama** (local model). The app exits after one round and writes **`./out/demands.md`**.

To get English output by default, set `DR_OUTPUT_LANGUAGE: "en"` in `docker-compose.yml`. Other everyday commands:

```bash
docker compose run --rm app --lang en                  # fetch another round (already-seen posts are skipped)
docker compose run --rm app digest --lang en           # last 7 days → out/digest-<date>.md
docker compose run --rm app digest --days 14 --lang en
docker compose run --rm app eval --lang en             # evaluate the current model on the labeled set
docker compose run --rm app cluster --lang en          # merge the same need seen in several places into one entry (pulls the embedding model on first use, ~640 MB)
docker compose --profile web up -d web                 # web dashboard → http://localhost:8501/?lang=en
```

**Hardware**: the default model, `qwen3:4b-instruct-2507-q4_K_M` (Apache-2.0), is about 2.5 GB and runs on a machine with 8 GB of RAM, at roughly 7 s per post on the CPU inside Docker; on the 61-item labeled set it scores F1 95.2% (Chinese) / 96.8% (English). On weaker machines, switch to `qwen2.5:1.5b` in `docker-compose.yml` (faster, clearly less accurate). If you already run Ollama, comment out the ollama service and point `OLLAMA_API` / `OLLAMA_BASE_URL` at it.

## How it works

Architecture diagrams (deployment, pipeline, modules, storage) are in [`docs/architecture.md`](docs/architecture.md) (labels in Chinese).

```
Public sources (configured in sources.toml)
  → Rule-based pre-filter: empty posts, launch posts (Show HN, "I built…"), bot output never reach the model
  → Local LLM extraction: is it a need, can one person build it, three 1–5 scores
  → Harm flag: needs that mention specific other people (covert tracking, finding someone's address, mass reporting,
    posing as a bank…) stay out of the list and go to a "Held for review" section at the end, with the matching phrase
  → Quote: the program picks the sentence from the post that reads most like a request (small models asked to copy one often translate or paraphrase it)
  → MongoDB: every post gets a status, so nothing is processed twice; failures are retried up to 3 times
  → Ranking: weighted scores + post popularity (log-scaled)
  → Full list out/demands.md / weekly digest out/digest-<date>.md
```

## Sources

All public endpoints, no sign-up needed. Configure them in [`sources.toml`](sources.toml).

| Source | What | Popularity | Limits |
|---|---|---|---|
| `softwarerecs` | StackExchange Software Recommendations: "is there software that does X" | votes | 300 req/day; 10k with `SE_API_KEY` |
| `hn_algolia` | Hacker News posts and comments matching phrases like "I wish there was" | points | generous |
| `github` | Feature-request issues with many 👍 | 👍 | 10 req/min; 30 with `GITHUB_TOKEN` |
| `arctic_shift` | Reddit posts via the third-party [Arctic Shift](docs/arctic-shift.md) archive API (doc in Chinese): newest posts per subreddit, keywords filtered locally, can backfill history | score (accurate ~36 h after posting) | dynamic, a few req/s; free service with no uptime guarantee |
| `rss` | Any RSS / Atom feed; defaults include r/SomebodyMakeThis and a few subreddit searches (same subreddits as `arctic_shift`, kept as its fallback) | — | per site |

If one source fails (network, rate limit), it is skipped and the rest keep going.

**Polite fetching**: every request from every source goes through one rate limiter that keeps a minimum gap between requests to the same host (GitHub 6.5 s, or 2.5 s with `GITHUB_TOKEN`; Reddit 3 s; 1 s otherwise). On 429 the whole host cools down for the server's `Retry-After`, so later requests wait too. The User-Agent carries the project URL. Raise the gaps per host under `[http]` in `sources.toml`.

## Merging similar needs

The same need shows up in a Hacker News comment, a softwarerecs question and a Reddit post. `cluster` merges them: each need's one-line summary is embedded with a local embedding model (default `qwen3-embedding:0.6b`, pulled automatically in Docker), and items whose cosine similarity is at or above 0.78 join the same cluster. The list and the digest then show one entry per cluster, with "from N sources, M posts" and links to the other posts. `run --cluster` (or `DR_CLUSTER=1`) does this after every round.

The embedding model must handle Chinese if you use Chinese output: on 23 labeled pairs `nomic-embed-text` scored 43.5%, while `qwen3-embedding:0.6b` and `bge-m3` both reach 87% at their best threshold. Check your own data with `cluster --eval`. With the Mac GPU override, mlx-lm has no embeddings endpoint, so point embeddings at a separate Ollama: `DR_EMBED_BASE_URL=http://host.docker.internal:11434/v1` for the container, `--embed-base-url http://localhost:11434/v1` for a local run.

## Web dashboard

`docker compose --profile web up -d web` starts a read-only dashboard at http://localhost:8501/ (`?lang=en` for English). It reads MongoDB directly and needs no model: filter by domain, source, time and keyword, sort by rank / heat / time, and browse the held-for-review items and the run history. Set `DR_WEB_PORT` if 8501 is taken. Without Docker: `python -m demand_radar serve --port 8501`.

## Output language

Needs are written in Chinese by default; use `--lang en` or `DR_OUTPUT_LANGUAGE=en` for English. **Quotes always stay in the original language.**

## Evaluation

After changing the prompt, the rules or the model, measure instead of guessing. [`eval/eval_set.json`](eval/eval_set.json) holds 61 items: 46 hand-labeled real posts plus 15 constructed ones covering harmful requests (tracking or exposing specific people) and benign look-alikes.

```bash
python -m demand_radar eval -v --lang en
```

It reports accuracy on "is this a need" and "can one person build it", end-to-end precision / recall / F1 for "should this be listed", how many posts the rules filtered (and whether any good ones were wrongly dropped), and whether any harmful request made it into the list (must be 0) and how many normal needs were held for review. `-v` prints the quote for every listed need so you can check it by hand. Latest results are in [`ROADMAP.md`](ROADMAP.md) (in Chinese).

## Running without Docker

Requires **Python 3.11 or newer** (it uses `tomllib`, added in 3.11). The Python that ships with macOS is 3.9 and fails with `No module named 'tomllib'`; install a newer one with `brew install python@3.12`.

```bash
cd demand-radar
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[web]"     # installs the demand-radar command; drop [web] if you do not need the dashboard

docker run -d -p 127.0.0.1:27017:27017 --name mongo mongo:7
ollama pull qwen3:4b-instruct-2507-q4_K_M

demand-radar run --limit 10 --lang en
demand-radar digest --lang en
```

`demand-radar` is the same as `python -m demand_radar`. Outside the repo, it falls back to the bundled `sources.toml` and eval sets when there is no local copy; copy `sources.toml` into the working directory to customise it, or pass `--sources-file`.

Development and tests run offline, with no network, MongoDB or model:

```bash
pip install -e ".[dev]"
ruff check demand_radar tests docker
python -m unittest discover -s tests
```

## Using the GPU on a Mac (optional)

Docker Desktop on macOS cannot pass the GPU to containers, so a model inside a container runs on the CPU. To use the Apple GPU, run the model server natively with mlx-lm and let the containers handle only MongoDB and the app. Measured on an M5: Qwen3-4B takes about 3 s per post, versus about 7 s for the same 4B model on the CPU inside Docker (qwen2.5:3b takes 4–6 s there but scores clearly lower) (see [`docs/model-selection.md`](docs/model-selection.md), in Chinese).

```bash
# 1. Start the model server (Python 3.11+; avoid port 8080, Docker Desktop uses it)
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-4B-Instruct-2507-4bit --port 8765

# 2. Add the Mac GPU override file; everything else stays the same
docker compose -f docker-compose.yml -f docker-compose.mac-gpu.yml up
docker compose -f docker-compose.yml -f docker-compose.mac-gpu.yml run --rm app digest --lang en
```

To change the model or port, prefix the command with `MLX_MODEL=<model> MLX_PORT=<port>`. Without Docker for the app, pass `--provider local/<model> --base-url http://localhost:8765/v1` directly. Any OpenAI-compatible server running on the host works the same way (LM Studio, llama.cpp, native Ollama).

## Cloud models (optional)

Without a suitable machine you can use a hosted model. It needs an API key, costs money, and sends the posts to the provider:

```bash
export OPENAI_API_KEY=sk-...
python -m demand_radar run --provider "openai/gpt-4o-mini" --lang en
```

## Options

| Option | Default | Meaning |
|---|---|---|
| `--limit` | 10 | Max posts per source (per feed for RSS, per query for HN/GitHub) |
| `--source` | `all` | Run only some sources, e.g. `github,rss` |
| `--provider` | `ollama/qwen3:4b-instruct-2507-q4_K_M` | Model; switches between local and hosted. Other local servers: `local/<model>` |
| `--lang` | `zh` | Output language: `zh` / `en` |
| `--min-confidence` | 0.5 | Needs below this confidence are left out |
| `--days` | 7 | `digest`: how many days back |
| `--sources-file` | `sources.toml` | Sources, filter rules and ranking weights |
| `--mongo-uri` / `--db-name` | `mongodb://localhost:27017` / `demand_radar` | Database |
| `--base-url` | `http://localhost:11434/v1` | Local model server (`--ollama-base-url` still works) |
| `--embed-model` | `qwen3-embedding:0.6b` | Embedding model for `cluster`, via `/v1/embeddings`; `bge-m3` scores the same, `nomic-embed-text` does not handle Chinese |
| `--threshold` | 0.78 | Cosine similarity at or above this means "same need"; compare settings with `cluster --eval` |
| `--embed-base-url` | same as `--base-url` | Separate embedding server (needed with the Mac GPU override) |

Every option can also be set through an environment variable (handy with Docker): `DR_SOURCE`, `DR_LIMIT`, `DR_PROVIDER`, `DR_OUTPUT_LANGUAGE`, `DR_MIN_CONFIDENCE`, `DR_SOURCES_FILE`, `MONGO_URI`, `DR_DB_NAME`, `OLLAMA_BASE_URL`, `DR_OUTPUT`, `DR_CLUSTER`, `DR_EMBED_MODEL`, `DR_EMBED_BASE_URL`, `DR_CLUSTER_THRESHOLD`, `DR_HOST`, `DR_PORT` (dashboard; in compose the host port is `DR_WEB_PORT`).

## Compared with similar projects

Projects such as [Reddit_Scrapper](https://github.com/Mohamedsaleh14/Reddit_Scrapper), [pain-miner](https://github.com/AdvancingTitans/pain-miner) and [sift](https://github.com/Gitter09/sift) mine communities for pain points too, and are all at an early stage.

| | Demand Radar | Typical similar project |
|---|---|---|
| Model | Fully local, no API key | Mostly paid hosted APIs |
| Setup | `docker compose up` | Local Python setup, rarely Docker |
| Quality | Labeled eval set, reproducible metrics | No published evaluation |
| Sources | HN, Reddit, StackExchange, GitHub issues | Mostly Reddit only |

## Troubleshooting

**On Linux: "输出目录 ./out 没有写权限" (output directory not writable)**: the container runs as uid 1000. If `./out` belongs to another user, run `sudo chown -R 1000:1000 out` in `demand-radar`, or uncomment `user:` under the app service in `docker-compose.yml` and set your own `id -u`:`id -g`. Docker Desktop on macOS and Windows is not affected.

**The dashboard is not reachable from other machines**: it has no login, so it listens on localhost only. To share it, change the web port mapping to `"${DR_WEB_PORT:-8501}:8501"` and put your own access control in front.

**Model download fails with `redirect target not allowed … resolves to non-public 198.18.x.x`**

This happens when a Clash / mihomo style proxy runs in fake-ip (TUN) mode: inside the container every hostname resolves to a fake 198.18.0.0/16 address. Ollama follows a redirect to Cloudflare storage when downloading a model, checks that the redirect target is a public IP, and refuses. The network itself works; only that check fails. Pick one fix:

1. **Exclude the host from fake-ip in your proxy** (recommended): add `+.cloudflarestorage.com` to `dns.fake-ip-filter` (Clash Verge Rev: Settings → DNS override). Then run `docker compose up` again.
2. **Pin the real IP in the ollama container**: look it up over encrypted DNS, which the proxy cannot intercept:

   ```bash
   curl -s -H "accept: application/dns-json" \
     "https://1.1.1.1/dns-query?name=dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com&type=A"
   ```

   then uncomment the two `extra_hosts` lines under the ollama service in `docker-compose.yml` and fill in that IP. It may change over time; look it up again if the download stops working.

**`No module named 'tomllib'` when running locally**: your Python is older than 3.11, see "Running without Docker" above.

## Compliance

Demand Radar only uses each platform's public API or RSS and respects their rate limits. You run it yourself, so please also follow each source's terms of service. Reports contain only the summarized need, one short quote and a link to the original post.

## Changelog

Current version: v0.3.0. See [`CHANGELOG.md`](CHANGELOG.md) (in Chinese).

## License

[MIT](LICENSE)
