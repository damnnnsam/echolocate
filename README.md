# echolocate

**Send the prompts real buyers ask into ChatGPT, Gemini, Claude, Perplexity and Google AI every day. Listen for your brand bouncing back.**

You give it a URL. Claude reads the site, works out what the company sells, its ICP and its real competitors, then defines prompt topics and writes the prompts buyers would actually type (100 by default). Every day echolocate sends every prompt to every engine and stores the answers. Claude then scores each answer: is the brand mentioned, at what position, with what sentiment, which competitors appear instead, and is your domain cited.

It is a CLI, an MCP server for coding agents, a REST API, an HTML dashboard and an optional daily email. Storage is one SQLite file.

## Status

Early (v0.1). Tested live end to end with the Gemini and Claude engines: onboard, run, score, report, trend, MCP server, dashboard. The ChatGPT, Perplexity and DataForSEO clients follow those vendors' documented APIs but have not been run against live keys yet.

## Engines

Each engine switches on when its key is in `.env`. `echolocate status` shows what is live.

| Engine | How it is asked | Key |
|---|---|---|
| Gemini | Gemini API with Google Search grounding | `GOOGLE_AI_API_KEY` |
| Claude | Anthropic API with the web search tool | `ANTHROPIC_API_KEY` (opt-in per brand: `engines --add claude`) |
| ChatGPT | OpenAI Responses API with `web_search` | `OPENAI_API_KEY` |
| Perplexity | Sonar API | `PERPLEXITY_API_KEY` |
| Google AI Mode / AI Overview | DataForSEO SERP API | `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` |

Gemini decides per prompt whether to run Google Search: on generic "best tool for X" prompts the current flash model often answers from memory (no citations), on "current pricing" / "vs" prompts it searches. `ECHO_GEMINI_MODEL=gemini-3.1-flash-lite` or `gemini-3.5-flash` searched on every prompt we tried. The Claude engine runs 2-5 searches per prompt and costs ~10× Gemini, which is why it is opt-in.

With DataForSEO credentials and no vendor key, ChatGPT and Gemini fall back to DataForSEO's scrapers of the real consumer UIs, and Claude/Perplexity to its LLM Responses product. DataForSEO answers carry the billed cost; vendor-API answers carry a list-price estimate (`cost_source` says which).

## Setup

```bash
uv sync
cp .env.example .env        # ANTHROPIC_API_KEY + the engine keys you have
uv run echolocate status
```

## Quickstart

```bash
uv run echolocate onboard yourcompany.com --prompts 100   # profile + ICP + competitors + topics + prompts
uv run echolocate prompts                                 # review; prompts add "…"; prompts remove 12 13; prompts generate 20
uv run echolocate run --limit 5                           # smoke test
uv run echolocate run                                     # all prompts × all engines; same-day re-runs only retry failures
uv run echolocate report                                  # markdown · -f json · -f html -o report.html
uv run echolocate trend                                   # day by day
uv run echolocate answers --missed -e gemini              # what Gemini said when it didn't mention you
uv run echolocate serve                                   # REST API + dashboard on :8787
```

## What the numbers mean

- **Visibility** — share of answers to *unbranded* prompts that mention you. The headline. Prompts that name you would mention you anyway, so they are left out.
- **Share of voice** — your mentions as a share of all brand mentions in unbranded answers.
- **Citation rate** — share of answers that cite your domain (or a subdomain) as a source.
- **Sentiment (0–100)** — how answers that mention you portray you, averaged.
- **Avg rank** — your position among the brands an answer names (1 = first).
- **Leaderboard** — the same for every competitor and for brands the engines recommend that you don't track yet. Measured on *your* prompt set.
- **Gaps** — unbranded prompts where an engine recommends competitors and not you. Your content to-do list.
- **Gained / lost** — prompt × engine pairs that started or stopped mentioning you since the previous run.

About 15% of prompts are branded (reputation, pricing, "X vs Y"). They mainly feed the sentiment score.

## From Claude Code (MCP)

This folder ships a `.mcp.json`, so Claude Code opened here sees the tools. Elsewhere:

```bash
claude mcp add echolocate -s user -- uv run --project "$(pwd)" echolocate mcp
```

Tools: `echolocate_status`, `echolocate_report`, `echolocate_trend`, `echolocate_answers`, `echolocate_prompts`, `echolocate_add_prompts`, `echolocate_remove_prompts`, `echolocate_generate_prompts`, `echolocate_onboard`, `echolocate_run` + `echolocate_run_status`, `echolocate_engines`.

## REST API

`echolocate serve` listens on localhost. Set `ECHO_API_TOKEN` to expose it; requests then need `Authorization: Bearer <token>` or `?token=`.

| Method | Path | |
|---|---|---|
| GET | `/v1/brands` | brands + latest headline |
| POST | `/v1/brands` | `{url, prompts?, topics?, country?, language?, engines?}` → onboard |
| GET | `/v1/brands/:brand/report?date=&format=json\|md\|html` | daily report |
| GET | `/v1/brands/:brand/trend?days=30` | time series |
| GET/POST/DELETE | `/v1/brands/:brand/prompts` | list / `{prompts:[…], topic?}` / `{ids:[…]}` |
| POST / GET | `/v1/brands/:brand/runs` | start today's run in the background / status |
| GET | `/v1/brands/:brand/answers?engine=&mentioned=false` | raw answers |
| GET | `/dashboard/:brand` | HTML dashboard |

## Daily schedule and email

`echolocate cron --at 07:00` prints a crontab line. Email goes through Resend: set `RESEND_API_KEY`, `ECHO_EMAIL_FROM`, `ECHO_EMAIL_TO`, then `run --email` or `echolocate email`.

## Costs

`echolocate cost` estimates spend per day and month; after the first runs it uses the average each engine actually cost. Scoring runs on `claude-haiku-4-5` (`ECHO_ANALYSIS_MODEL`), onboarding once per brand on `claude-sonnet-5-5` (`ECHO_MODEL`).

## Code map

`onboarding.py` profile, topics, prompts · `engines/` one client per engine · `runner.py` the daily job (resumable) · `analyze.py` scores answers · `metrics.py` the report · `render.py` markdown + HTML · `cli.py`, `mcp_server.py`, `server.py` the interfaces · `db.py` SQLite (`data/echolocate.db`).

MIT, see [LICENSE](LICENSE).
