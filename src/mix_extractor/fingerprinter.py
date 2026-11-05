"""Fingerprinter — identify tracks by audio fingerprinting using the AudD API.

Samples the mix at regular intervals, sends each sample to AudD, and returns
a list of identified tracks with metadata and links.

AudD API: https://audd.io
  - Free tier: 500 requests/month
  - Paid: ~$0.001/request
  - Returns: title, artist, album, label, release_date, Spotify/Apple/Deezer links
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings

console = Console()

# How often to sample the mix (seconds). 90s balances accuracy vs API cost.
DEFAULT_SAMPLE_INTERVAL = 90
# Duration of each audio snippet sent to AudD (seconds)
SAMPLE_DURATION = 15
# AudD endpoint
_AUDD_URL = "https://api.audd.io/"
# Hits on the same track closer than this are one play; farther apart = played again.
REPEAT_GAP_SECONDS = 600
# Bump when the saved-results format changes so old files are ignored.
_CACHE_VERSION = 1


@dataclass
class FingerprintedTrack:
    """A track identified by audio fingerprinting."""
    timestamp: float          # seconds into the mix where this was detected
    artist: str
    title: str
    album: str = ""
    label: str = ""
    release_date: str = ""
    score: int = 0            # AudD confidence score (0–100)
    links: dict = field(default_factory=dict)
    detection_source: str = "fingerprint"
    hits: int = 1             # how many samples matched this track
    confirmed: bool = False   # True once 2+ samples (or the transcript) agree

    @property
    def timestamp_str(self) -> str:
        h = int(self.timestamp // 3600)
        m = int((self.timestamp % 3600) // 60)
        s = int(self.timestamp % 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class AudDApiLimitError(Exception):
    """Raised when the AudD API limit is reached."""
    pass


def fingerprint_mix(
    audio_path: Path,
    settings: "Settings",
    *,
    sample_interval: int = DEFAULT_SAMPLE_INTERVAL,
    second_pass: bool = True,
    cache_path: Path | None = None,
    refresh: bool = False,
) -> list[FingerprintedTrack]:
    """Sample *audio_path* every *sample_interval* seconds and identify each chunk.

    With *second_pass*, every sample that found no match is retried once,
    offset by half the interval. Raw hits are saved to *cache_path* (when
    given) and reused on later runs with the same audio and settings, unless
    *refresh* is set.

    Returns one :class:`FingerprintedTrack` per play of each detected song,
    sorted by time (see :func:`consolidate_hits`).
    """
    if not settings.audd_api_key:
        console.print("[yellow]AUDD_API_KEY not set — skipping fingerprinting.[/yellow]")
        return []

    cache_key = _cache_key(audio_path, sample_interval, second_pass)
    if cache_path is not None and not refresh:
        cached = _load_cache(cache_path, cache_key)
        if cached is not None:
            console.print(
                f"[dim]Reusing saved fingerprint results ({len(cached)} hit(s)) from[/dim] {cache_path}\n"
                "[dim]Use --refingerprint to query AudD again.[/dim]"
            )
            return consolidate_hits(cached)

    try:
        import ffmpeg  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("ffmpeg-python required for fingerprinting") from exc

    try:
        import httpx  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError("httpx required for fingerprinting") from exc

    try:
        probe = ffmpeg.probe(str(audio_path))
        duration = float(probe["format"]["duration"])
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not probe audio duration:[/red] {exc}")
        return []

    hits: list[FingerprintedTrack] = []
    complete = True
    try:
        points = list(range(0, int(duration), sample_interval))
        console.print(
            f"[bold blue]Fingerprinting[/bold blue] {len(points)} sample points "
            f"(every {sample_interval}s) …"
        )
        misses = _run_pass(audio_path, points, settings.audd_api_key, hits)
        console.print(f"  [dim]Pass 1: {len(points)} request(s), {len(points) - len(misses)} match(es)[/dim]")

        if second_pass and misses:
            offset = sample_interval // 2
            retry = [p + offset for p in misses if p + offset < duration]
            if retry:
                console.print(
                    f"[bold blue]Second pass[/bold blue] over {len(retry)} gap(s) (+{offset}s) …"
                )
                still = _run_pass(audio_path, retry, settings.audd_api_key, hits)
                console.print(f"  [dim]Pass 2: {len(retry)} request(s), {len(retry) - len(still)} match(es)[/dim]")
    except AudDApiLimitError as exc:
        complete = False
        console.print(f"\n[bold yellow]Aborting fingerprinting:[/bold yellow] {exc}")
        console.print("[yellow]Continuing with analysis using partial fingerprint results.[/yellow]")

    # Only complete runs are reused; a run cut short by the AudD limit is retried next time.
    if cache_path is not None and complete:
        _save_cache(cache_path, cache_key, hits)

    results = consolidate_hits(hits)
    confirmed = sum(1 for t in results if t.confirmed)
    console.print(
        f"[green]Fingerprinting complete:[/green] {len(results)} track(s) identified "
        f"({confirmed} confirmed by 2+ samples, {len(results) - confirmed} unconfirmed)"
    )
    return results


def _run_pass(
    audio_path: Path,
    points: list[float],
    api_key: str,
    hits: list[FingerprintedTrack],
) -> list[float]:
    """Query AudD at each point, appending matches to *hits*. Returns the points with no match."""
    misses: list[float] = []
    for i, start in enumerate(points):
        console.print(f"  [{i + 1}/{len(points)}] {_fmt_seconds(start)} …", end=" ")

        snippet = _extract_snippet(audio_path, start, SAMPLE_DURATION)
        if snippet is None:
            console.print("[dim]skip[/dim]")
            misses.append(start)
            continue

        track = _query_audd(snippet, start, api_key)
        time.sleep(0.3)  # be polite to AudD
        if track is None:
            console.print("[dim]no match[/dim]")
            misses.append(start)
            continue

        hits.append(track)
        console.print(f"[green]{track.artist} — {track.title}[/green]")
    return misses


def consolidate_hits(hits: list[FingerprintedTrack]) -> list[FingerprintedTrack]:
    """Combine raw sample hits into one entry per play of each track.

    Hits on the same artist/title (ignoring case and punctuation) are combined
    even when other samples come between them. Hits at least
    :data:`REPEAT_GAP_SECONDS` apart are kept as separate plays. Each entry
    takes the earliest hit's timestamp, counts its hits, and is ``confirmed``
    when 2+ samples matched it.
    """
    plays: list[FingerprintedTrack] = []
    last_seen: dict[str, tuple[FingerprintedTrack, float]] = {}

    for hit in sorted(hits, key=lambda h: h.timestamp):
        key = _track_key(hit.artist, hit.title)
        prev = last_seen.get(key)
        if prev is not None and hit.timestamp - prev[1] < REPEAT_GAP_SECONDS:
            play = prev[0]
            play.hits += 1
            play.confirmed = True
            for name, url in hit.links.items():
                play.links.setdefault(name, url)
            for attr in ("album", "label", "release_date"):
                if not getattr(play, attr):
                    setattr(play, attr, getattr(hit, attr))
            last_seen[key] = (play, hit.timestamp)
            continue

        play = FingerprintedTrack(**{**hit.__dict__, "links": dict(hit.links), "hits": 1, "confirmed": False})
        plays.append(play)
        last_seen[key] = (play, hit.timestamp)

    return plays


def _track_key(artist: str, title: str) -> str:
    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()
    return f"{norm(artist)}|{norm(title)}"


# ── saved results ─────────────────────────────────────────────────────────────

def _cache_key(audio_path: Path, interval: int, second_pass: bool) -> dict:
    try:
        size = audio_path.stat().st_size
    except OSError:
        size = None
    return {
        "version": _CACHE_VERSION,
        "source": audio_path.name,
        "source_size": size,
        "interval": interval,
        "second_pass": second_pass,
        "sample_duration": SAMPLE_DURATION,
    }


def _load_cache(cache_path: Path, key: dict) -> list[FingerprintedTrack] | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("key") != key:
        return None
    try:
        return [FingerprintedTrack(**h) for h in data.get("hits", [])]
    except TypeError:
        return None


def _save_cache(cache_path: Path, key: dict, hits: list[FingerprintedTrack]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "hits": [h.__dict__ for h in hits]}
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[dim]Saved fingerprint results →[/dim] {cache_path}")
    except OSError as exc:
        console.print(f"[yellow]Could not save fingerprint results:[/yellow] {exc}")


def _extract_snippet(audio_path: Path, start: float, duration: float) -> bytes | None:
    """Extract a short audio snippet as MP3 bytes using ffmpeg."""
    import ffmpeg  # noqa: PLC0415

    try:
        out, _ = (
            ffmpeg
            .input(str(audio_path), ss=start, t=duration)
            .output("pipe:1", format="mp3", audio_bitrate="128k", ac=1)
            .run(capture_stdout=True, capture_stderr=True)
        )
        return out if out else None
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]ffmpeg snippet error: {exc}[/dim]")
        return None


def _query_audd(audio_bytes: bytes, timestamp: float, api_key: str) -> FingerprintedTrack | None:
    """Send audio bytes to AudD and parse the response."""
    import httpx  # noqa: PLC0415

    try:
        response = httpx.post(
            _AUDD_URL,
            data={
                "api_token": api_key,
                "return": "spotify,apple_music,deezer",
            },
            files={"file": ("snippet.mp3", audio_bytes, "audio/mpeg")},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]AudD request error: {exc}[/dim]")
        return None

    if data.get("status") != "success":
        error_msg = data.get("error", {}).get("error_message", "Unknown error")
        if "limit" in error_msg.lower():
            raise AudDApiLimitError(error_msg)
        console.print(f"[red]AudD error: {error_msg}[/red]")
        return None

    if not data.get("result"):
        # No match found
        return None

    result = data["result"]
    artist = result.get("artist", "")
    title = result.get("title", "")

    if not artist and not title:
        return None

    links: dict[str, str] = {}

    # Extract Spotify link
    spotify = result.get("spotify") or {}
    if isinstance(spotify, dict):
        ext = spotify.get("external_urls", {})
        if isinstance(ext, dict) and ext.get("spotify"):
            links["spotify"] = ext["spotify"]

    # Extract Apple Music link
    apple = result.get("apple_music") or {}
    if isinstance(apple, dict) and apple.get("url"):
        links["apple_music"] = apple["url"]

    # Extract Deezer link
    deezer = result.get("deezer") or {}
    if isinstance(deezer, dict) and deezer.get("link"):
        links["deezer"] = deezer["link"]

    return FingerprintedTrack(
        timestamp=timestamp,
        artist=artist,
        title=title,
        album=result.get("album", ""),
        label=result.get("label", ""),
        release_date=result.get("release_date", ""),
        score=result.get("score", 0),
        links=links,
    )


def _fmt_seconds(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
