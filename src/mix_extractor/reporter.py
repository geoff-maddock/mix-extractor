"""Reporter — write tracks.json and report.md to content/output/<mix_name>/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from mix_extractor.config import Settings
    from mix_extractor.transcriber import TranscriptSegment

console = Console()


def write_report(
    *,
    source_name: str,
    segments: "list[TranscriptSegment]",
    enriched_tracks: list[dict],
    settings: "Settings",
    transcription_provider: str,
    duration_seconds: float | None = None,
) -> Path:
    """Write JSON + Markdown report to content/output/<mix_stem>/ and return the folder."""
    mix_stem = Path(source_name).stem
    out_dir = settings.output_dir / mix_stem
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_json(out_dir, source_name, segments, enriched_tracks, transcription_provider, duration_seconds)
    _write_markdown(out_dir, source_name, segments, enriched_tracks)
    _print_summary(enriched_tracks)

    console.print(f"\n[bold green]Reports written to:[/bold green] {out_dir}")
    return out_dir


# ── JSON ──────────────────────────────────────────────────────────────────────

def _write_json(
    out_dir: Path,
    source_name: str,
    segments: "list[TranscriptSegment]",
    enriched_tracks: list[dict],
    transcription_provider: str,
    duration_seconds: float | None,
) -> None:
    payload = {
        "mix": {
            "source": source_name,
            "duration_seconds": duration_seconds,
            "transcription_provider": transcription_provider,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        },
        "tracks": enriched_tracks,
    }
    path = out_dir / "tracks.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"  [dim]→ {path}[/dim]")


# ── Markdown ──────────────────────────────────────────────────────────────────

def _write_markdown(
    out_dir: Path,
    source_name: str,
    segments: "list[TranscriptSegment]",
    enriched_tracks: list[dict],
) -> None:
    lines: list[str] = []

    lines.append(f"# Tracklist Report: {source_name}\n")
    lines.append(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n")

    # Tracklist table
    lines.append("\n## Tracklist\n")
    lines.append("| # | Time | Artist | Title | Remix | Label | Links |")
    lines.append("|---|------|--------|-------|-------|-------|-------|")

    for t in enriched_tracks:
        links_md = _format_links(t.get("links", {}))
        remix = t.get("remix") or ""
        label = t.get("label") or ""
        lines.append(
            f"| {t['index']} "
            f"| {t.get('timestamp', '')} "
            f"| {_md_escape(t.get('artist', ''))} "
            f"| {_md_escape(t.get('title', ''))} "
            f"| {_md_escape(remix)} "
            f"| {_md_escape(label)} "
            f"| {links_md} |"
        )

    # Full transcript
    lines.append("\n---\n")
    lines.append("## Full Transcript\n")
    for seg in segments:
        ts = _fmt_seconds(seg.start)
        lines.append(f"**[{ts}]** {seg.text}\n")

    path = out_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"  [dim]→ {path}[/dim]")


def _format_links(links: dict) -> str:
    parts = []
    labels = {
        "musicbrainz": "MusicBrainz",
        "spotify": "Spotify",
        "youtube_music": "YouTube Music",
        "bandcamp": "Bandcamp",
        "soundcloud": "SoundCloud",
        "discogs": "Discogs",
        "beatport": "Beatport",
    }
    for key, url in links.items():
        label = labels.get(key, key.title())
        parts.append(f"[{label}]({url})")
    return " · ".join(parts) if parts else "—"


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _fmt_seconds(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ── Terminal summary ──────────────────────────────────────────────────────────

def _print_summary(enriched_tracks: list[dict]) -> None:
    if not enriched_tracks:
        console.print("[yellow]No tracks extracted.[/yellow]")
        return

    table = Table(title="Extracted Tracklist", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Time", width=8)
    table.add_column("Artist", style="bold cyan")
    table.add_column("Title", style="bold white")
    table.add_column("Links", style="dim")

    for t in enriched_tracks:
        link_count = len(t.get("links", {}))
        table.add_row(
            str(t["index"]),
            t.get("timestamp", ""),
            t.get("artist", ""),
            t.get("title", ""),
            f"{link_count} source(s)" if link_count else "—",
        )

    console.print(table)
