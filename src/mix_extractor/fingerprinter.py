"""Fingerprinter — identify tracks by audio fingerprinting using the AudD API.

Samples the mix at regular intervals, sends each sample to AudD, and returns
a list of identified tracks with metadata and links.

AudD API: https://audd.io
  - Free tier: 500 requests/month
  - Paid: ~$0.001/request
  - Returns: title, artist, album, label, release_date, Spotify/Apple/Deezer links
"""

from __future__ import annotations

import io
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

    @property
    def timestamp_str(self) -> str:
        h = int(self.timestamp // 3600)
        m = int((self.timestamp % 3600) // 60)
        s = int(self.timestamp % 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fingerprint_mix(
    audio_path: Path,
    settings: "Settings",
    *,
    sample_interval: int = DEFAULT_SAMPLE_INTERVAL,
) -> list[FingerprintedTrack]:
    """Sample *audio_path* every *sample_interval* seconds and identify each chunk.

    Returns a deduplicated list of :class:`FingerprintedTrack`, one per
    detected song (consecutive identical results are collapsed).
    """
    if not settings.audd_api_key:
        console.print("[yellow]AUDD_API_KEY not set — skipping fingerprinting.[/yellow]")
        return []

    try:
        import ffmpeg  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("ffmpeg-python required for fingerprinting") from exc

    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("httpx required for fingerprinting") from exc

    # Get total duration
    try:
        probe = ffmpeg.probe(str(audio_path))
        duration = float(probe["format"]["duration"])
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not probe audio duration:[/red] {exc}")
        return []

    sample_points = list(range(0, int(duration), sample_interval))
    console.print(
        f"[bold blue]Fingerprinting[/bold blue] {len(sample_points)} sample points "
        f"(every {sample_interval}s) …"
    )

    results: list[FingerprintedTrack] = []
    last_title_artist: str | None = None

    for i, start in enumerate(sample_points):
        console.print(
            f"  [{i + 1}/{len(sample_points)}] {_fmt_seconds(start)} …",
            end=" ",
        )

        snippet = _extract_snippet(audio_path, start, SAMPLE_DURATION)
        if snippet is None:
            console.print("[dim]skip[/dim]")
            continue

        track = _query_audd(snippet, start, settings.audd_api_key)
        if track is None:
            console.print("[dim]no match[/dim]")
            continue

        key = f"{track.artist.lower()}|{track.title.lower()}"
        if key == last_title_artist:
            console.print(f"[dim](same as previous)[/dim]")
            continue

        last_title_artist = key
        results.append(track)
        console.print(f"[green]{track.artist} — {track.title}[/green]")

        # AudD rate limit: be polite
        time.sleep(0.3)

    console.print(f"[green]Fingerprinting complete:[/green] {len(results)} unique track(s) identified")
    return results


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

    if data.get("status") != "success" or not data.get("result"):
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
