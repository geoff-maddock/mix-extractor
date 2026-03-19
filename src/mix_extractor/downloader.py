"""Downloader — fetch audio from URLs into content/input/ using yt-dlp."""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

console = Console()


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source, re.IGNORECASE))


def download(url: str, dest_dir: Path) -> Path:
    """Download audio from *url* into *dest_dir* and return the resulting file path.

    Uses yt-dlp to handle YouTube, SoundCloud, Mixcloud, Bandcamp, and any
    other site in its supported list, as well as direct audio file URLs.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp") from exc

    dest_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(title)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    console.print(f"[bold blue]Downloading[/bold blue] {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # After post-processing the extension is mp3
        title = ydl.prepare_filename(info)
        output_path = Path(title).with_suffix(".mp3")

    if not output_path.exists():
        # yt-dlp may change the name slightly; fall back to most-recently modified mp3
        candidates = sorted(dest_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise RuntimeError(f"Download succeeded but no MP3 found in {dest_dir}")
        output_path = candidates[-1]

    console.print(f"[green]Saved[/green] → {output_path}")
    return output_path
