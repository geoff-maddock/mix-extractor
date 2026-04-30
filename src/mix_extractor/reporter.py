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
    source_url: str | None = None,
) -> Path:
    """Write JSON + Markdown report to content/output/<mix_stem>/ and return the folder."""
    mix_stem = Path(source_name).stem
    out_dir = settings.output_dir / mix_stem
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_json(out_dir, source_name, segments, enriched_tracks, transcription_provider, duration_seconds, source_url)
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
    source_url: str | None,
) -> None:
    mix_meta: dict = {
        "source": source_name,
        "duration_seconds": duration_seconds,
        "transcription_provider": transcription_provider,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    if source_url:
        mix_meta["source_url"] = source_url

    # Preserve an existing source_url if the caller didn't pass one (e.g. reprocess)
    path = out_dir / "tracks.json"
    if not source_url and path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            prior_url = existing.get("mix", {}).get("source_url")
            if prior_url:
                mix_meta["source_url"] = prior_url
        except Exception:
            pass

    payload = {"mix": mix_meta, "tracks": enriched_tracks}
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
    lines.extend(_render_tracklist_md(enriched_tracks))

    # Full transcript
    lines.append("\n---\n")
    lines.append("## Full Transcript\n")
    for seg in segments:
        ts = _fmt_seconds(seg.start)
        lines.append(f"**[{ts}]** {seg.text}\n")

    path = out_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"  [dim]→ {path}[/dim]")


def _render_tracklist_md(tracks: list[dict]) -> list[str]:
    """Render the tracklist table as markdown lines (header + rows)."""
    lines = [
        "| # | Time | Artist | Title | Remix | Label | Source | Links |",
        "|---|------|--------|-------|-------|-------|--------|-------|",
    ]
    for i, t in enumerate(tracks, start=1):
        links_md = _format_links(t.get("links", {}))
        remix = t.get("remix") or ""
        label = t.get("label") or ""
        source = _format_source(t.get("detection_source", "transcript"))
        lines.append(
            f"| {i} "
            f"| {t.get('timestamp', '')} "
            f"| {_md_escape(t.get('artist', ''))} "
            f"| {_md_escape(t.get('title', ''))} "
            f"| {_md_escape(remix)} "
            f"| {_md_escape(label)} "
            f"| {source} "
            f"| {links_md} |"
        )
    return lines


def regenerate_report_md(
    out_dir: Path,
    source_name: str,
    tracks: list[dict],
) -> None:
    """Rewrite ``report.md`` from the current track list.

    Any existing ``## Full Transcript`` section is preserved verbatim so
    transcripts produced during the original analyze pass don't get lost when
    the user later edits/reorders/deletes tracks via the web UI.
    """
    report_path = out_dir / "report.md"

    transcript_section = ""
    if report_path.exists():
        existing = report_path.read_text(encoding="utf-8")
        idx = existing.find("\n## Full Transcript")
        if idx >= 0:
            transcript_section = existing[idx:]

    lines: list[str] = [
        f"# Tracklist Report: {source_name}\n",
        f"*Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n",
        "\n## Tracklist\n",
    ]
    lines.extend(_render_tracklist_md(tracks))

    if transcript_section:
        lines.append("\n---")
        lines.append(transcript_section)
    output = "\n".join(lines)
    if not output.endswith("\n"):
        output += "\n"
    report_path.write_text(output, encoding="utf-8")


_SOURCE_LABELS = {
    "fingerprint": "🎵 FP",
    "transcript": "📝 TX",
    "fingerprint+transcript": "🎵+📝",
}


def _format_source(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


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
    table.add_column("Source", style="dim", width=8)
    table.add_column("Links", style="dim")

    for t in enriched_tracks:
        link_count = len(t.get("links", {}))
        source = _format_source(t.get("detection_source", "transcript"))
        table.add_row(
            str(t["index"]),
            t.get("timestamp", ""),
            t.get("artist", ""),
            t.get("title", ""),
            source,
            f"{link_count} source(s)" if link_count else "—",
        )

    console.print(table)
