"""Parser — use an LLM to extract a structured tracklist from a transcript.

Supports OpenAI (GPT-4o / GPT-4o-mini) and Anthropic Claude.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings
    from mix_extractor.transcriber import TranscriptSegment

console = Console()


class Track(BaseModel):
    index: int
    timestamp: str = Field(default="")          # e.g. "00:03:22" — best-effort from context
    artist: str
    title: str
    label: str = Field(default="")
    remix: str = Field(default="")              # remixer name if mentioned
    extra_info: str = Field(default="")         # any other detail mentioned (BPM, key, year…)
    confidence: float = Field(default=1.0)      # 0–1, set by enricher later


_SYSTEM_PROMPT = """\
You are an expert in electronic music and DJ culture. You will receive the transcript of a DJ mix.
Your task is to extract every track the DJ mentions or announces. 
Return ONLY a valid JSON array — no markdown, no commentary.
Each element must follow this schema exactly:
{
  "index": <integer, 1-based position in mix>,
  "timestamp": "<HH:MM:SS or empty string if unknown>",
  "artist": "<artist or band name>",
  "title": "<track title>",
  "label": "<record label if mentioned, else empty string>",
  "remix": "<remixer name if mentioned, else empty string>",
  "extra_info": "<any other detail: BPM, key, year, catalogue number, etc. or empty string>"
}

Rules:
- Include a track only if you have at least the artist OR the title.
- If the DJ says something like "this is X by Y" or "Y - X", map correctly to artist/title.
- Clean up transcription artefacts (stutters, filler words) from names.
- Do not invent information not present in the transcript.
- If no tracks can be extracted, return an empty array [].
"""

_MAX_TRANSCRIPT_CHARS = 28_000  # stay well within 32k context window


def parse_tracks(
    transcript: str,
    segments: "list[TranscriptSegment]",
    settings: "Settings",
) -> list[Track]:
    """Send *transcript* to the configured LLM and return extracted :class:`Track` objects."""
    provider = settings.llm_provider
    console.print(f"[bold blue]Parsing tracklist[/bold blue] via [bold]{provider}/{settings.llm_model}[/bold] …")

    # Trim very long transcripts to fit context limits
    trimmed = transcript[:_MAX_TRANSCRIPT_CHARS]
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        console.print(
            f"[yellow]Transcript trimmed to {_MAX_TRANSCRIPT_CHARS} chars for LLM context[/yellow]"
        )

    raw_json = _call_llm(trimmed, settings)
    tracks = _parse_response(raw_json)
    console.print(f"[green]Extracted {len(tracks)} track(s)[/green]")
    return tracks


def _call_llm(transcript: str, settings: "Settings") -> str:
    if settings.llm_provider == "openai":
        return _call_openai(transcript, settings)
    elif settings.llm_provider == "anthropic":
        return _call_anthropic(transcript, settings)
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")


def _call_openai(transcript: str, settings: "Settings") -> str:
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=settings.require_key("openai_api_key"))
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content or "[]"


def _call_anthropic(transcript: str, settings: "Settings") -> str:
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=settings.require_key("anthropic_api_key"))
    message = client.messages.create(
        model=settings.llm_model,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ],
    )
    return message.content[0].text if message.content else "[]"


def _parse_response(raw: str) -> list[Track]:
    """Extract a JSON array from the LLM response and validate each item."""
    # Strip markdown fences if the LLM wrapped them despite instructions
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Failed to parse LLM JSON response:[/red] {exc}")
        console.print(f"[dim]{raw[:500]}[/dim]")
        return []

    if not isinstance(data, list):
        console.print("[red]LLM response was not a JSON array.[/red]")
        return []

    tracks: list[Track] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        # Ensure index is set
        item.setdefault("index", i + 1)
        try:
            tracks.append(Track(**item))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Skipping malformed track entry {i}: {exc}[/yellow]")

    return tracks
