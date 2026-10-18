# Faceless Türkçe Stüdyo v8

Automated vertical (9:16) series builder that outputs **Turkish** episodes.
Turkish sibling of `faceless-studio` v7, but with the captions removed and a
stronger story engine.

> All code comments, log lines and this README are in English.
> The only Turkish text inside `pipeline.py` is the LLM prompt, because that is
> what makes the generated script Turkish.

## What changed vs. v7

| | faceless-studio v7 | faceless-turkce-studio v8 |
|---|---|---|
| Language | English | **Turkish** (script, narration, title, description, hashtags) |
| Voice | `en-US-ChristopherNeural` | **`tr-TR-AhmetNeural`** (override with the `VOICE` env var) |
| Images | 14 | **21** (7 levels × 3 shots) |
| On-screen text | Captions + "Part N" burned in | **None** — the platform generates subtitles |
| Camera | One zoom-in for everything | Per shot: arrival = push in, **POV = slow drift in**, outcome = pull back |
| Narration | 17–22 words/shot | 15–21 words/shot |
| Story | Same tone every episode | Each episode gets its own **flavor**, flavors rotate in order |
| Time jumps | None | Required at every level transition ("Üç yıl sonra…") |

Runtime is roughly 2.5–3 minutes per episode (21 shots × 15–21 words).

## How it runs

`listen.yml` (hourly) looks for `/start` in Telegram → triggers `produce.yml`:
`write` (Groq/Gemini script) → `produce` (Cloudflare images + edge-tts + ffmpeg) → `notify` (Telegram).

If the daily free Cloudflare quota runs out, the run stops, writes
`memory/autoresume.flag`, and retries automatically about an hour after the UTC
reset. Nothing is lost — every step caches by filename and is idempotent.

## Setup

1. Create the repo (GitHub → **New repository** → **Import a repository** →
   source `https://github.com/landryparadis012-git/faceless-studio`), or create an
   empty repo and copy these files in:
   `pipeline.py`, `listen.py`, `requirements.txt`, `README.md`,
   `.github/workflows/*.yml`, `memory/.gitkeep`.
2. **Do not copy** `memory/series_state.json` or `memory/tg_offset.txt` — the
   Turkish channel must start fresh at episode 1.
3. Actions tab → enable the workflows (imported repos arrive with them disabled).
4. Settings → Secrets and variables → Actions, add:
   `GROQ_API_KEY`, `GEMINI_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HUB_URL`, `HUB_WEBHOOK_SECRET`.
5. Actions → **produce** → *Run workflow* → first Turkish episode.

Series memory lives in `memory/series_state.json`. If that file is absent the
pipeline starts a brand-new series at episode 1. After 8 episodes the series
closes and the next `/start` begins a new one.

## Knobs (top of `pipeline.py`)

`VOICE` (env-overridable), `MAX_EPISODES` (8), `LEVEL_COUNT` (7),
`SHOTS_PER_LEVEL` (3), `FLAVORS` (episode flavors), `DEFAULT_BIBLE`
(character + style). `MAX_EPISODES` format is read by a regex in
`sync-state.yml` — keep the line shape.

## Output per episode (`episodes/EP-…/`)

`script.json` · `images/L1..L21.png` · `audio/L1..L21.mp3` · `segs/` ·
`video.mp4` · `metadata.json` (includes `captions_burned_in: false` and the
`ai_disclosure` note).

## OpsHub

One shared module, `hub.py`, feeds and is triggered by OpsHub in every studio.

**Feed.** `content.episode_ready`, `content.series_state` and
`content.series_finished` are POSTed to `$HUB_URL/ingest` (`schema_version: 1`),
signed with `x-hub-signature-256` using `HUB_WEBHOOK_SECRET`. If the hub only
exposes the older unsigned route, the same event is retried against
`$OPSHUB_URL/emit`, so both hub generations keep working. Emitting is never
fatal: a broken hub cannot kill a render.

**Trigger.** `repository_dispatch` with type `opshub.produce` (or `opshub.run`).
OpsHub sends it with any token that has `contents: write` here. The
`client_payload` supports:

| key | meaning |
|---|---|
| `topic` | free-text brief for the next episode (`new_series` ignores it for a fresh arc) |
| `new_series` | `true` starts a brand-new arc instead of continuing the current one |
| `max_episodes` | override how long the arc runs |
| `reason` | free text, echoed into the run log |

Telegram needs no new bot: the same `TELEGRAM_BOT_TOKEN` works everywhere, only
`TELEGRAM_CHAT_ID` changes per studio.

```bash
curl -X POST https://api.github.com/repos/<owner>/<repo>/dispatches \
  -H "Authorization: Bearer $OPSHUB_GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"opshub.produce","client_payload":{"topic":"","new_series":false,"reason":"opshub nightly"}}'
```
