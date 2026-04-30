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
The transcript contains [HH:MM:SS] timestamp markers showing when each section was spoken.
Your task is to extract every track the DJ mentions or announces.
Return ONLY a valid JSON array — no markdown, no commentary.
Each element must follow this schema exactly:
{
  "index": <integer, 1-based position in mix>,
  "timestamp": "<HH:MM:SS — use the nearest [HH:MM:SS] marker before the track mention>",
  "artist": "<artist or band name>",
  "title": "<track title>",
  "label": "<record label if mentioned, else empty string>",
  "remix": "<remixer name if mentioned, else empty string>",
  "extra_info": "<any other detail: BPM, key, year, catalogue number, etc. or empty string>"
}

Rules:
- Include a track only if you have at least the artist OR the title.
- ALWAYS populate the timestamp field using the [HH:MM:SS] markers in the transcript.
  Use the timestamp marker that appears just before or nearest to where the track is mentioned.
- If the DJ says something like "this is X by Y" or "Y - X", map correctly to artist/title.
- Clean up transcription artefacts (stutters, filler words) from names.
- Do not invent information not present in the transcript.
- If no tracks can be extracted, return an empty array [].

EXAMPLES

Example 1 — canonical announcement with remixer and label
Transcript:
[00:04:15] Coming up next we have, uh, Bicep with their track "Glue" — the original mix on Feel My Bicep records.
Expected:
[{"index": 1, "timestamp": "00:04:15", "artist": "Bicep", "title": "Glue", "label": "Feel My Bicep", "remix": "", "extra_info": ""}]

Example 2 — "title by artist" word order, plus a remix
Transcript:
[00:18:42] You're listening to "Be Mine" by Lone, the Floating Points remix, fresh out on R&S.
Expected:
[{"index": 1, "timestamp": "00:18:42", "artist": "Lone", "title": "Be Mine", "label": "R&S", "remix": "Floating Points", "extra_info": ""}]

Example 3 — multiple tracks in a row, including filler words and a partial mention
Transcript:
[00:32:00] Right, this one's a heater, Skee Mask "Rio Dub", Ilian Tape.
[00:36:10] And then we go into, um, Pessimist's "Ekkomesa" — 140 BPM banger.
Expected:
[
  {"index": 1, "timestamp": "00:32:00", "artist": "Skee Mask", "title": "Rio Dub", "label": "Ilian Tape", "remix": "", "extra_info": ""},
  {"index": 2, "timestamp": "00:36:10", "artist": "Pessimist", "title": "Ekkomesa", "label": "", "remix": "", "extra_info": "140 BPM"}
]

Example 4 — vague/incomplete mention, skip if both artist and title are unknown
Transcript:
[00:02:00] Big shouts to my man on the next one, you know who you are.
Expected:
[]
"""

_MAX_TRANSCRIPT_CHARS = 28_000  # stay well within 32k context window


def parse_tracks(
    transcript: str,
    segments: "list[TranscriptSegment]",
    settings: "Settings",
) -> list[Track]:
    """Send *transcript* to the configured LLM and return extracted :class:`Track` objects.

    If the transcript contains ``[HH:MM:SS]`` markers the LLM is instructed to
    use them.  Any tracks still missing a timestamp after LLM extraction are
    back-filled by searching the segments for the artist/title mention.
    """
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

    # Back-fill any missing timestamps from the transcript segments
    if segments:
        _estimate_timestamps(tracks, segments)

    console.print(f"[green]Extracted {len(tracks)} track(s)[/green]")
    return tracks


def call_llm(system_prompt: str, user_message: str, settings: "Settings") -> str:
    """Send a system + user message pair to the configured LLM. Returns raw text."""
    if settings.llm_provider == "openai":
        return _call_openai(system_prompt, user_message, settings)
    elif settings.llm_provider == "anthropic":
        return _call_anthropic(system_prompt, user_message, settings)
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")


def _call_llm(transcript: str, settings: "Settings") -> str:
    return call_llm(_SYSTEM_PROMPT, f"Transcript:\n\n{transcript}", settings)


def _call_openai(system_prompt: str, user_message: str, settings: "Settings") -> str:
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=settings.require_key("openai_api_key"))
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content or "[]"


def _call_anthropic(system_prompt: str, user_message: str, settings: "Settings") -> str:
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=settings.require_key("anthropic_api_key"))
    message = client.messages.create(
        model=settings.llm_model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_message},
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


# ── timestamp estimation ──────────────────────────────────────────────────────

def _estimate_timestamps(
    tracks: list[Track],
    segments: "list[TranscriptSegment]",
) -> None:
    """Fill in missing timestamps by scanning segments for artist/title mentions.

    Modifies *tracks* in-place.
    """
    for track in tracks:
        if track.timestamp:
            continue  # already has one

        best_time: float | None = None
        best_score = 0.0

        artist_words = _to_words(track.artist)
        title_words = _to_words(track.title)
        search_words = artist_words | title_words
        if not search_words:
            continue

        for seg in segments:
            seg_words = _to_words(seg.text)
            if not seg_words:
                continue
            overlap = len(search_words & seg_words) / len(search_words)
            if overlap > best_score:
                best_score = overlap
                best_time = seg.start

        if best_time is not None and best_score >= 0.4:
            h = int(best_time // 3600)
            m = int((best_time % 3600) // 60)
            s = int(best_time % 60)
            track.timestamp = f"{h:02d}:{m:02d}:{s:02d}"


def _to_words(text: str) -> set[str]:
    """Lowercase and split into a set of significant words."""
    stop = {"the", "a", "an", "of", "ft", "feat", "featuring", "vs", "and", "is", "by", "from"}
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return {w for w in words if len(w) > 1 and w not in stop}
