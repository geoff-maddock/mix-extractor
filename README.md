# mix-extractor

Extract tracklists from DJ mix audio files using speech-to-text and LLMs.

## How it works

1. **Download / ingest** — provide a local file from `content/input/` or a URL (YouTube, SoundCloud, Mixcloud, direct links)
2. **Normalize** — convert audio to a consistent format via ffmpeg
3. **Fingerprint** — sample the mix every 90 seconds and query AudD to identify tracks by audio fingerprint (optional)
4. **Transcribe** — send audio to a speech-to-text API (OpenAI Whisper by default)
5. **Parse** — an LLM reads the transcript and extracts structured `{artist, title, timestamp}` data
6. **Merge** — transcript-extracted tracks and fingerprinted tracks are combined and deduplicated
7. **Enrich** — each track is looked up across music services to find purchase/stream links
8. **Report** — results written to `content/output/<mix_name>/tracks.json` and `report.md`

## Requirements

- Python 3.10+
- `ffmpeg` installed and on your `PATH` (`brew install ffmpeg` / `apt install ffmpeg`)

## Setup

```bash
# 1. Clone and install
pip install -e .

# 2. Copy and fill in your API keys
cp .env.example .env
# Edit .env with your keys

# 3. Run
mix-extractor analyze content/input/my_mix.mp3
# or
mix-extractor analyze https://soundcloud.com/some-dj/set-title
```

## Web GUI

mix-extractor ships with a browser-based interface built on FastAPI. It exposes all the
same functionality as the CLI plus interactive track management features.

### Install web dependencies

```bash
pip install -e ".[web]"
```

### Start the server

```bash
mix-extractor serve                        # http://127.0.0.1:8000 (default)
mix-extractor serve --port 9000            # custom port
mix-extractor serve --host 0.0.0.0        # listen on all interfaces (LAN access)
mix-extractor serve --reload               # auto-reload on code changes (development)
```

Open `http://127.0.0.1:8000` in your browser.

### Pages

| Page | URL | Description |
|---|---|---|
| **Dashboard** | `/` | Submit analysis jobs, upload files, view active jobs and recent mixes |
| **Mix detail** | `/mix/<mix_name>` | Full tracklist with timestamps, links, keep flags, and inline editing |
| **Library** | `/library` | Cross-mix track search; filter to kept tracks only |
| **Job status** | `/job/<job_id>` | Live streaming log output for an in-progress analysis |

### Features

**Analyze a mix** — Paste a URL (SoundCloud, YouTube, Mixcloud, Bandcamp) or enter a
local filename from `content/input/`. Expand *Advanced options* to choose a different LLM
provider/model, transcription backend, or enable/disable fingerprinting and enrichment.
The job runs in the background and the browser auto-refreshes the log until it completes.

**Upload audio** — Drag-and-drop or select a file from the Dashboard to upload it
directly to the `content/input/` folder, ready for analysis.

**Track management** (on the Mix detail page):
- **Bookmark** tracks with the heart icon to build a keep list
- **Edit** artist, title, label, or remix fields inline by clicking any cell — edits are
  stored separately in `user_data.json` and the original scraped data is preserved
- **Tag a genre** via the dropdown on each row
- **Preview** — click stream links (Spotify/Bandcamp/SoundCloud embeds) without leaving
  the page

**Library** — search across all mixes simultaneously by artist, title, label, or genre.
Toggle *Kept tracks only* to see your curated selections.

### REST API

The server also exposes a small JSON API for programmatic access:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/mixes` | List all mixes with metadata and tracks |
| `GET` | `/api/library?q=<query>&keep_only=true` | Search tracks across all mixes |
| `GET` | `/api/job/<job_id>` | Poll job status and log lines |
| `POST` | `/api/track/<mix_name>/keep` | Set/unset a track's keep flag |
| `POST` | `/api/track/<mix_name>/genre` | Set a track's genre tag |
| `POST` | `/api/track/<mix_name>/edit` | Save a field override (artist/title/label/remix) |

---

## CLI commands

```
mix-extractor list                                 # list files in content/input/
mix-extractor analyze <file-or-url>                # full pipeline
mix-extractor analyze <file> --no-enrich           # skip music API lookups
mix-extractor analyze <file> --no-fingerprint      # skip AudD audio fingerprinting
mix-extractor analyze <file> --fingerprint-only    # fingerprint only, no transcription
mix-extractor analyze <file> --llm anthropic       # override LLM from .env
mix-extractor analyze <file> --transcriber assemblyai
mix-extractor config                               # interactive API key setup
```

## Integrations

### LLM providers (track parsing)

At least one LLM key is required to parse the transcript into a structured tracklist.

| Provider | Env vars | How to get a key |
|---|---|---|
| **OpenAI** (default) | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) — pay-per-use, ~$0.01/mix |
| **Anthropic** | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) — pay-per-use |

Set `LLM_PROVIDER=openai` or `LLM_PROVIDER=anthropic` in `.env`.  
Recommended models: `gpt-4o` (OpenAI) or `claude-3-7-sonnet-20250219` (Anthropic).

---

### Transcription backends

| Backend | Env vars | Notes |
|---|---|---|
| `whisper_api` (default) | `OPENAI_API_KEY` | OpenAI Whisper API — best DJ accuracy, ~$0.006/min |
| `whisper_local` | *(none)* | Free, runs on your machine — `pip install openai-whisper` |
| `assemblyai` | `ASSEMBLYAI_API_KEY` | [assemblyai.com](https://www.assemblyai.com) — free tier available |
| `deepgram` | `DEEPGRAM_API_KEY` | [deepgram.com](https://deepgram.com) — free $200 credit on signup |

Set `TRANSCRIPTION_PROVIDER=<backend>` in `.env`.

---

### Audio fingerprinting

Fingerprinting samples the mix every 90 seconds and queries an audio recognition API to
identify tracks directly from the audio signal — independent of any speech in the mix.
Results are merged with transcript-parsed tracks and the `detection_source` field in the
output indicates whether each track came from `fingerprint`, `transcript`, or both.

| Service | Env vars | Notes |
|---|---|---|
| **AudD** | `AUDD_API_KEY` | [audd.io](https://audd.io) — 500 free requests/month; paid plans from $3/month |

Fingerprinting is skipped automatically if `AUDD_API_KEY` is not set, or can be disabled
explicitly with `--no-fingerprint`.

---

### Music lookup & enrichment

These integrations are all optional. Omit the keys to skip that source. Links for Bandcamp
and SoundCloud are always generated as search URLs (no key required).

| Service | Env vars | How to get credentials | Notes |
|---|---|---|---|
| **MusicBrainz** | *(none)* | Open database, no key needed | Always active |
| **Bandcamp** | *(none)* | No key needed | Always active (search URL) |
| **SoundCloud** | *(none)* | No key needed | Always active (search URL) |
| **YouTube Music** | *(none)* | No key needed — uses `ytmusicapi` | Always active |
| **Spotify** | `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` | [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) — free, create an app and use Client Credentials flow | Optional |
| **Discogs** | `DISCOGS_TOKEN` | [discogs.com/settings/developers](https://www.discogs.com/settings/developers) — free personal access token | Optional; requires `pip install discogs-client` |

---

## Output

Results are written to `content/output/<mix_name>/`:

- `tracks.json` — machine-readable structured tracklist with links and `detection_source`
- `report.md` — human-readable report with tracklist table (including Source column) and full transcript

Each track in the output includes a `detection_source` field:

| Value | Meaning |
|---|---|
| `fingerprint` | Identified by AudD audio recognition |
| `transcript` | Extracted from speech by the LLM |
| `fingerprint+transcript` | Confirmed by both methods |

## Supported input formats

Audio: MP3, FLAC, WAV, M4A, OGG, OPUS, AAC, and any format ffmpeg can decode.  
URLs: any source supported by yt-dlp (YouTube, SoundCloud, Mixcloud, Bandcamp, direct audio URLs, etc.)
