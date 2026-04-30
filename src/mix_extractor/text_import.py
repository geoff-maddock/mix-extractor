"""Text import — parse a plain-text tracklist via LLM and optionally enrich."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings

console = Console()

_TEXT_IMPORT_PROMPT = """\
You are an expert in electronic music. You will receive a plain-text tracklist.
Extract every track into structured data. Return ONLY a valid JSON array — no markdown, no commentary.
Each element must follow this schema exactly:
{
  "index": <integer, 1-based>,
  "timestamp": "<if a timestamp appears in the text, else empty string>",
  "artist": "<artist or band name>",
  "title": "<track title>",
  "label": "<record label if mentioned (often in [brackets]), else empty string>",
  "remix": "<remixer name if mentioned (often in (parentheses) with 'remix'/'mix'/'edit'), else empty string>",
  "extra_info": "<any other detail: BPM, key, year, catalogue number, etc. or empty string>"
}

Rules:
- Lines may use formats like "Artist - Title", "Artist — Title", "Artist – Title",
  numbered lists ("1. Artist - Title"), timestamped lists ("00:03:22 Artist - Title"), etc.
- Parenthetical text like "(DJ X Remix)" or "(Original Mix)" should go in the "remix" field.
- Text in [brackets] is usually the record label.
- URLs in the text should be ignored — they are handled separately.
- Do not invent information not present in the text.
- If no tracks can be extracted, return an empty array [].
"""

# Domain patterns for classifying URLs by music service
_URL_CLASSIFIERS = [
    (re.compile(r"bandcamp\.com"), "bandcamp"),
    (re.compile(r"soundcloud\.com"), "soundcloud"),
    (re.compile(r"open\.spotify\.com"), "spotify"),
    (re.compile(r"music\.youtube\.com|youtube\.com"), "youtube_music"),
    (re.compile(r"discogs\.com"), "discogs"),
    (re.compile(r"musicbrainz\.org"), "musicbrainz"),
    (re.compile(r"deezer\.com"), "deezer"),
]

_URL_RE = re.compile(r"https?://[^\s,<>\"']+")

_HEADER_URL_DOMAINS = ("soundcloud.com", "mixcloud.com", "youtube.com/watch", "youtu.be/")


def _detect_source_url(text: str) -> str | None:
    """Return the first SoundCloud / Mixcloud / YouTube URL near the top of *text*.

    Scans the first few non-blank lines so per-track YouTube / SoundCloud links
    appearing later in the body are not mistaken for the mix-level source.
    """
    line_count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        line_count += 1
        if line_count > 5:
            break
        for url in _URL_RE.findall(stripped):
            for domain in _HEADER_URL_DOMAINS:
                if domain in url:
                    return url
    return None


def _extract_embedded_urls(text: str) -> dict[int, dict[str, str]]:
    """Scan each line for URLs and classify them by music service.

    Returns ``{line_index: {service: url}}`` where *line_index* is 0-based,
    counting only non-blank lines (to align with track indices).
    """
    result: dict[int, dict[str, str]] = {}
    track_line = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        urls = _URL_RE.findall(stripped)
        if urls:
            services: dict[str, str] = {}
            for url in urls:
                for pattern, service in _URL_CLASSIFIERS:
                    if pattern.search(url):
                        services[service] = url
                        break
            if services:
                result[track_line] = services
        track_line += 1
    return result


def _default_mix_name(text: str) -> str:
    """Derive a mix name from the first non-empty line, or use a date-based fallback."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            # Remove URLs before slugifying
            cleaned = _URL_RE.sub("", stripped).strip()
            if not cleaned:
                continue
            # Slugify: lowercase, replace non-alphanum with hyphens, collapse
            slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
            return slug[:60] or f"imported-tracklist-{datetime.now().strftime('%Y-%m-%d')}"
    return f"imported-tracklist-{datetime.now().strftime('%Y-%m-%d')}"


def _unique_mix_dir(base_dir: Path, mix_name: str) -> tuple[str, Path]:
    """Return a (name, path) pair, appending -2, -3, … if the directory exists."""
    candidate = mix_name
    out = base_dir / candidate
    counter = 2
    while out.exists():
        candidate = f"{mix_name}-{counter}"
        out = base_dir / candidate
        counter += 1
    return candidate, out


def import_tracklist(
    text: str,
    mix_name: str,
    settings: "Settings",
    run_enrich: bool = False,
) -> tuple[list[dict], Path]:
    """Parse a plain-text tracklist, optionally enrich, and write tracks.json.

    Returns ``(tracks, output_dir)`` where *tracks* is a list of enriched dicts
    and *output_dir* is the folder containing ``tracks.json``.
    """
    from mix_extractor.enricher import enrich  # noqa: PLC0415
    from mix_extractor.parser import _parse_response, call_llm  # noqa: PLC0415

    console.print("[bold blue]Importing text tracklist[/bold blue] …")

    # 1. Pre-extract embedded URLs
    url_map = _extract_embedded_urls(text)

    # 2. Parse via LLM
    console.print(f"  Parsing via [bold]{settings.llm_provider}/{settings.llm_model}[/bold] …")
    raw_json = call_llm(_TEXT_IMPORT_PROMPT, text, settings)
    tracks = _parse_response(raw_json)

    if not tracks:
        console.print("[yellow]No tracks could be extracted from the text.[/yellow]")
        return [], Path()

    console.print(f"  [green]Parsed {len(tracks)} track(s)[/green]")

    # 3. Convert to dicts and merge embedded URLs
    track_dicts = []
    for track in tracks:
        d = track.model_dump()
        d["detection_source"] = "text_import"
        d["links"] = {}
        # Merge URLs by track index (0-based in url_map, 1-based in track.index)
        line_urls = url_map.get(track.index - 1, {})
        d["links"].update(line_urls)
        track_dicts.append(d)

    # 4. Optionally enrich
    if run_enrich:
        console.print("[bold blue]Enriching tracks[/bold blue] …")
        track_dicts = enrich(track_dicts, settings)

    # 5. Write tracks.json
    if not mix_name:
        mix_name = _default_mix_name(text)

    mix_name, out_dir = _unique_mix_dir(settings.output_dir, mix_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    mix_meta: dict = {
        "source": mix_name,
        "duration_seconds": None,
        "transcription_provider": "text_import",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    source_url = _detect_source_url(text)
    if source_url:
        mix_meta["source_url"] = source_url

    payload = {"mix": mix_meta, "tracks": track_dicts}
    tracks_path = out_dir / "tracks.json"
    tracks_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    console.print(f"  [dim]→ {tracks_path}[/dim]")

    # 6. Write report.md
    _write_markdown(out_dir, mix_name, track_dicts)

    console.print(f"\n[bold green]Import complete:[/bold green] {out_dir}")
    return track_dicts, out_dir


def _write_markdown(out_dir: Path, mix_name: str, tracks: list[dict]) -> None:
    """Write a simple report.md for the imported tracklist."""
    lines = [
        f"# Tracklist: {mix_name}\n",
        f"*Imported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n",
        "\n## Tracklist\n",
        "| # | Artist | Title | Remix | Label | Links |",
        "|---|--------|-------|-------|-------|-------|",
    ]
    for t in tracks:
        links = t.get("links", {})
        link_parts = [f"[{k.title()}]({v})" for k, v in links.items()]
        lines.append(
            f"| {t['index']} "
            f"| {t.get('artist', '')} "
            f"| {t.get('title', '')} "
            f"| {t.get('remix', '')} "
            f"| {t.get('label', '')} "
            f"| {' · '.join(link_parts) or '—'} |"
        )
    path = out_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"  [dim]→ {path}[/dim]")
