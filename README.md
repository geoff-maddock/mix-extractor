# mix-extractor

Extract tracklists from DJ mix audio files using speech-to-text and LLMs.

## How it works

1. **Download / ingest** — provide a local file from `content/input/` or a URL (YouTube, SoundCloud, Mixcloud, direct links)
2. **Normalize** — convert audio to a consistent format via ffmpeg
3. **Transcribe** — send audio to a speech-to-text API (OpenAI Whisper by default)
4. **Parse** — an LLM reads the transcript and extracts structured `{artist, title, timestamp}` data
5. **Enrich** — each track is looked up on MusicBrainz, Spotify, Discogs, and YouTube Music to find links
6. **Report** — results written to `content/output/<mix_name>/tracks.json` and `report.md`

## Requirements

- Python 3.11+
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
mix-extractor list                           # list files in content/input/
mix-extractor analyze <file-or-url>          # full pipeline
mix-extractor analyze <file> --no-enrich     # skip music API lookups
mix-extractor analyze <file> --llm anthropic # override LLM from .env
mix-extractor analyze <file> --transcriber assemblyai
mix-extractor config                         # interactive API key setup
```

## Transcription backends

| Backend | Notes |
|---|---|
| `whisper_api` (default) | OpenAI Whisper API — best accuracy, pay-per-use |
| `whisper_local` | Free local Whisper; install with `pip install openai-whisper` |
| `assemblyai` | High quality API; install with `pip install assemblyai` |
| `deepgram` | Fast API; install with `pip install deepgram-sdk` |

## Output

Results are written to `content/output/<mix_name>/`:

- `tracks.json` — machine-readable structured tracklist with links
- `report.md` — human-readable report with transcript and tracklist table

## Supported input formats

Audio: MP3, FLAC, WAV, M4A, OGG, OPUS, AAC, and any format ffmpeg can decode.  
URLs: any source supported by yt-dlp (YouTube, SoundCloud, Mixcloud, Bandcamp, direct audio URLs, etc.)
