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
DR_OUTPUT_LANGUAGE=en docker compose up   # first run downloads the model (~2 GB)
```

This starts three containers: **app** (fetch + extract), **mongo** (storage) and **ollama** (local model). The app exits after one round and writes **`./out/demands.md`**.

To get English output by default, set `DR_OUTPUT_LANGUAGE: "en"` in `docker-compose.yml`. Other everyday commands:

```bash
docker compose run --rm app --lang en                  # fetch another round (already-seen posts are skipped)
docker compose run --rm app digest --lang en           # last 7 days → out/digest-<date>.md
docker compose run --rm app digest --days 14 --lang en
docker compose run --rm app eval --lang en             # evaluate the current model on the labeled set
```

**Hardware**: the default model, qwen2.5:3b, is about 2 GB and runs on a machine with 8 GB of RAM. On weaker machines, switch to `qwen2.5:1.5b` in `docker-compose.yml` (faster, somewhat less accurate). If you already run Ollama, comment out the ollama service and point `OLLAMA_API` / `OLLAMA_BASE_URL` at it.

## How it works

```
Public sources (configured in sources.toml)
  → Rule-based pre-filter: empty posts, launch posts (Show HN, "I built…"), bot output never reach the model
  → Local LLM extraction: is it a need, can one person build it, three 1–5 scores, a quote (verified to be in the post)
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
| `rss` | Any RSS / Atom feed; defaults include r/SomebodyMakeThis and a few subreddit searches | — | per site |

If one source fails (network, rate limit), it is skipped and the rest keep going. Rate-limited requests are retried after the server's `Retry-After`.

## Output language

Needs are written in Chinese by default; use `--lang en` or `DR_OUTPUT_LANGUAGE=en` for English. **Quotes always stay in the original language.**

## Evaluation

After changing the prompt, the rules or the model, measure instead of guessing. [`eval/eval_set.json`](eval/eval_set.json) holds 45 hand-labeled real posts.

```bash
python -m demand_radar eval -v --lang en
```

It reports accuracy on "is this a need" and "can one person build it", end-to-end precision / recall / F1 for "should this be listed", how many posts the rules filtered (and whether any good ones were wrongly dropped), and how many quotes really come from the post. Latest results are in [`ROADMAP.md`](ROADMAP.md) (in Chinese).

## Running without Docker

Requires **Python 3.11 or newer** (it uses `tomllib`, added in 3.11). The Python that ships with macOS is 3.9 and fails with `No module named 'tomllib'`; install a newer one with `brew install python@3.12`.

```bash
cd demand-radar
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker run -d -p 27017:27017 --name mongo mongo:7
ollama pull qwen2.5:3b

python -m demand_radar run --limit 10 --lang en
python -m demand_radar digest --lang en
```

Tests run offline, with no network, MongoDB or model:

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests
```

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
| `--provider` | `ollama/qwen2.5:3b` | Model; switches between local and hosted |
| `--lang` | `zh` | Output language: `zh` / `en` |
| `--min-confidence` | 0.5 | Needs below this confidence are left out |
| `--days` | 7 | `digest`: how many days back |
| `--sources-file` | `sources.toml` | Sources, filter rules and ranking weights |
| `--mongo-uri` / `--db-name` | `mongodb://localhost:27017` / `demand_radar` | Database |
| `--ollama-base-url` | `http://localhost:11434/v1` | Local model server |

Every option can also be set through an environment variable (handy with Docker): `DR_SOURCE`, `DR_LIMIT`, `DR_PROVIDER`, `DR_OUTPUT_LANGUAGE`, `DR_MIN_CONFIDENCE`, `DR_SOURCES_FILE`, `MONGO_URI`, `DR_DB_NAME`, `OLLAMA_BASE_URL`, `DR_OUTPUT`.

## Compared with similar projects

Projects such as [Reddit_Scrapper](https://github.com/Mohamedsaleh14/Reddit_Scrapper), [pain-miner](https://github.com/AdvancingTitans/pain-miner) and [sift](https://github.com/Gitter09/sift) mine communities for pain points too, and are all at an early stage.

| | Demand Radar | Typical similar project |
|---|---|---|
| Model | Fully local, no API key | Mostly paid hosted APIs |
| Setup | `docker compose up` | Local Python setup, rarely Docker |
| Quality | Labeled eval set, reproducible metrics | No published evaluation |
| Sources | HN, Reddit, StackExchange, GitHub issues | Mostly Reddit only |

## Troubleshooting

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

## License

[MIT](LICENSE)
