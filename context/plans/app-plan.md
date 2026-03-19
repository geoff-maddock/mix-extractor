# mix-extractor — Application Plan

## Overview

`mix-extractor` is a CLI tool (Python) that ingests a DJ mix audio file (or in the future, video), transcribes any spoken content, extracts track identifiers (artist + title + any other metadata), and then looks up those tracks online to find purchase or download links.

---

## Goals

1. Accept a local audio file from `content/input/` or a URL to download to that folder
2. Transcribe speech (DJ talk-overs, shoutouts, tracklist announcements) using a speech-to-text API
3. Parse the transcript using an LLM to extract structured track data (artist, title, label, key, etc.)
4. Look up each identified track via music APIs and return links for purchase/download
5. Produce a structured output report (JSON + Markdown)

---

## Tech Stack

### Language
- **Python 3.11+** — mature ecosystem for audio, LLMs, and API clients

### Input / Download
- **yt-dlp** — download audio from URLs (YouTube, SoundCloud, Mixcloud, Bandcamp, direct links, etc.)
- **ffmpeg** (via `pydub` or `ffmpeg-python`) — audio format normalization (MP3, FLAC, WAV, M4A, OGG → standard format for transcription)

### Transcription (Speech-to-Text)
Multiple backend options, configurable by the user:

| Provider | Notes |
|---|---|
| **OpenAI Whisper API** | High accuracy, handles music/DJ content well, pay-per-use |
| **Local Whisper** (`openai-whisper`) | Free, runs locally via CPU/GPU, slower but private |
| **AssemblyAI** | High-quality API, excellent for noisy audio, good free tier |
| **Deepgram** | Fast, accurate API with Nova-2 model; strong music/mixed-audio performance |

Recommended default: **OpenAI Whisper API** (best accuracy for DJ speech over music).

### LLM (Transcript Parsing + Track Enrichment)
Configurable provider:

| Provider | Models |
|---|---|
| **OpenAI** | `gpt-4o`, `gpt-4o-mini` |
| **Anthropic Claude** | `claude-3-7-sonnet`, `claude-3-5-haiku` |

The LLM is used for:
- Extracting structured `{artist, title, label, bpm, key, timestamp}` objects from raw transcript text
- Generating search queries for track lookup
- Fuzzy-matching / disambiguating track results from music APIs

### Track Lookup APIs
Multiple sources queried per track, ranked by confidence:

| API | Data | Notes |
|---|---|---|
| **Beatport** | Electronic music, purchase links | Best for DJ/dance tracks |
| **Discogs** | All genres, buy/sell listings | Comprehensive physical + digital |
| **MusicBrainz** | Open metadata database | Free, no API key needed |
| **Spotify** | Streaming links | Broad catalog |
| **YouTube Music** | Stream links, video | Via `ytmusicapi` |

---

## Architecture

```
Input (file or URL)
        │
        ▼
[ Downloader ]         ← yt-dlp; saves to content/input/
        │
        ▼
[ Audio Normalizer ]   ← ffmpeg; converts to WAV/MP3 for transcription
        │
        ▼
[ Transcriber ]        ← Whisper API / AssemblyAI / Deepgram
        │              → produces timestamped transcript
        ▼
[ Parser (LLM) ]       ← sends transcript to GPT-4o or Claude
        │              → extracts [{artist, title, timestamp, ...}]
        ▼
[ Enricher ]           ← queries Beatport, Discogs, MusicBrainz, Spotify
        │              → adds {purchase_url, stream_url, confidence} per track
        ▼
[ Reporter ]           ← writes content/output/<mix_name>/
                          ├── report.md
                          └── tracks.json
```

---

## Project Structure

```
mix-extractor/
├── content/
│   ├── input/                  # Place files here or download target
│   └── output/                 # Results go here, one folder per mix
├── context/
│   ├── inspiration/
│   └── plans/
├── src/
│   └── mix_extractor/
│       ├── __init__.py
│       ├── cli.py              # CLI entry point (argparse or Typer)
│       ├── config.py           # Settings: API keys, provider choices
│       ├── downloader.py       # URL → content/input/ via yt-dlp
│       ├── normalizer.py       # Audio format conversion via ffmpeg
│       ├── transcriber.py      # Speech-to-text (multi-backend)
│       ├── parser.py           # LLM-based transcript → track list
│       ├── enricher.py         # Track lookup across music APIs
│       └── reporter.py         # Output formatting (JSON + Markdown)
├── tests/
│   ├── test_transcriber.py
│   ├── test_parser.py
│   └── test_enricher.py
├── .env.example                # Template for API keys
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Configuration

Users configure the app via a `.env` file or environment variables:

```dotenv
# LLM Provider: "openai" | "anthropic"
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o

# Transcription Provider: "whisper_api" | "whisper_local" | "assemblyai" | "deepgram"
TRANSCRIPTION_PROVIDER=whisper_api

# API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
ASSEMBLYAI_API_KEY=...
DEEPGRAM_API_KEY=...

# Music Lookup APIs (optional — degrades gracefully)
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
DISCOGS_TOKEN=...
```

---

## CLI Interface

```bash
# Analyze a file already in content/input/
mix-extractor analyze content/input/my_mix.mp3

# Download from a URL and analyze
mix-extractor analyze https://soundcloud.com/some-dj/mix-title

# List files currently in content/input/
mix-extractor list

# Configure API keys interactively
mix-extractor config
```

---

## Output Format

### `tracks.json`
```json
{
  "mix": {
    "source": "my_mix.mp3",
    "duration_seconds": 3600,
    "transcription_provider": "whisper_api",
    "analyzed_at": "2026-03-18T12:00:00Z"
  },
  "tracks": [
    {
      "index": 1,
      "timestamp": "00:03:22",
      "artist": "Bicep",
      "title": "Glue",
      "label": "Feel My Bicep",
      "confidence": 0.92,
      "links": {
        "beatport": "https://www.beatport.com/track/glue/...",
        "discogs": "https://www.discogs.com/release/...",
        "spotify": "https://open.spotify.com/track/...",
        "youtube": "https://music.youtube.com/watch?v=..."
      }
    }
  ]
}
```

### `report.md`
A human-readable document with the full transcript, the extracted tracklist table, and links for each track.

---

## Development Phases

### Phase 1 — Core Pipeline (MVP)
- [ ] Project scaffold: `pyproject.toml`, `requirements.txt`, directory structure
- [ ] `config.py`: load `.env`, validate required keys, select providers
- [ ] `cli.py`: `analyze` and `list` commands via Typer
- [ ] `downloader.py`: `yt-dlp` integration for URL inputs
- [ ] `normalizer.py`: ffmpeg-based audio conversion
- [ ] `transcriber.py`: OpenAI Whisper API backend
- [ ] `parser.py`: LLM prompt to extract track list from transcript
- [ ] `reporter.py`: write `tracks.json` and `report.md` to `content/output/`

### Phase 2 — Multi-Provider Support
- [ ] `transcriber.py`: add local Whisper, AssemblyAI, Deepgram backends
- [ ] `parser.py`: support both OpenAI and Claude as LLM backends
- [ ] `cli.py`: add `config` command for interactive API key setup

### Phase 3 — Track Enrichment
- [ ] `enricher.py`: MusicBrainz lookup (no key required, good baseline)
- [ ] `enricher.py`: Discogs lookup
- [ ] `enricher.py`: Spotify lookup  
- [ ] `enricher.py`: Beatport scrape / search (no official API; may need scraping or SerpAPI)
- [ ] `enricher.py`: YouTube Music via `ytmusicapi`
- [ ] Confidence scoring and result ranking

### Phase 4 — Quality & Robustness
- [ ] Unit tests for parser and enricher
- [ ] Handle long mixes: chunked transcription to stay within API token limits
- [ ] Rate limiting and retry logic for all external APIs
- [ ] Improve transcript parsing prompt with few-shot examples

### Phase 5 — Video Support (Future)
- [ ] Extract audio from video files (MP4, MKV, etc.) before transcription pipeline
- [ ] Download audio from video URLs (yt-dlp already handles most of this)

---

## Key Challenges & Mitigations

| Challenge | Mitigation |
|---|---|
| DJ speech is heavily mixed with music | Whisper handles this better than most STT; consider audio pre-processing to boost speech frequencies |
| Not all DJs announce tracks verbally | Supplement with audio fingerprinting (AcoustID/AudD) as a future Phase 5 option |
| Beatport has no public API | Use SerpAPI or DuckDuckGo search as a proxy; scraping as fallback |
| Ambiguous artist/title pairs | LLM re-ranking with multiple search results; ask user to confirm low-confidence entries |
| Very long mixes (1–4 hrs) | Chunk audio by silence / fixed intervals; merge transcript chunks before parsing |

---

## Dependencies (initial)

```
typer[all]          # CLI framework
python-dotenv       # .env loading
yt-dlp              # URL download
ffmpeg-python       # Audio normalization
openai              # Whisper API + GPT-4o
anthropic           # Claude API
openai-whisper      # Local Whisper (optional)
assemblyai          # AssemblyAI backend (optional)
deepgram-sdk        # Deepgram backend (optional)
musicbrainzngs      # MusicBrainz lookup
discogs-client      # Discogs lookup
spotipy             # Spotify lookup
ytmusicapi          # YouTube Music lookup
httpx               # HTTP client for any direct API calls
rich                # Terminal output formatting
```
