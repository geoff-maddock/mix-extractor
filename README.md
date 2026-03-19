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

## CLI commands

```
mix-extractor list                              # list files in content/input/
mix-extractor analyze <file-or-url>             # full pipeline
mix-extractor analyze <file> --no-enrich        # skip music API lookups
mix-extractor analyze <file> --no-fingerprint   # skip AudD audio fingerprinting
mix-extractor analyze <file> --llm anthropic    # override LLM from .env
mix-extractor analyze <file> --transcriber assemblyai
mix-extractor config                            # interactive API key setup
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
