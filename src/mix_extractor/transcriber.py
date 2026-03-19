"""Transcriber — speech-to-text with pluggable backends.

Supported backends
------------------
whisper_api   : OpenAI Whisper API  (requires OPENAI_API_KEY)
whisper_local : local openai-whisper package  (no key needed, slow)
assemblyai    : AssemblyAI API  (requires ASSEMBLYAI_API_KEY)
deepgram      : Deepgram API  (requires DEEPGRAM_API_KEY)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings

console = Console()

# Whisper API hard limit per request
_WHISPER_API_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


class TranscriptSegment:
    """A timestamped chunk of transcript text."""

    __slots__ = ("start", "end", "text")

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start  # seconds
        self.end = end
        self.text = text.strip()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Segment [{self.start:.0f}s–{self.end:.0f}s] {self.text[:60]!r}>"


def transcribe(audio_path: Path, settings: "Settings") -> list[TranscriptSegment]:
    """Transcribe *audio_path* and return a list of :class:`TranscriptSegment`.

    Routes to the appropriate backend based on ``settings.transcription_provider``.
    For large files, automatically splits and merges chunks.
    """
    provider = settings.transcription_provider
    console.print(f"[bold blue]Transcribing[/bold blue] via [bold]{provider}[/bold] …")

    if provider == "whisper_api":
        return _transcribe_whisper_api(audio_path, settings)
    elif provider == "whisper_local":
        return _transcribe_whisper_local(audio_path)
    elif provider == "assemblyai":
        return _transcribe_assemblyai(audio_path, settings)
    elif provider == "deepgram":
        return _transcribe_deepgram(audio_path, settings)
    else:
        raise ValueError(f"Unknown transcription provider: {provider!r}")


# ── OpenAI Whisper API ────────────────────────────────────────────────────────

def _transcribe_whisper_api(audio_path: Path, settings: "Settings") -> list[TranscriptSegment]:
    from openai import OpenAI  # noqa: PLC0415

    api_key = settings.require_key("openai_api_key")
    client = OpenAI(api_key=api_key)
    file_size = audio_path.stat().st_size

    if file_size <= _WHISPER_API_MAX_BYTES:
        return _whisper_api_single(client, audio_path)
    else:
        return _whisper_api_chunked(client, audio_path, file_size)


def _whisper_api_single(client, audio_path: Path) -> list[TranscriptSegment]:
    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=fh,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    return [
        TranscriptSegment(seg.start, seg.end, seg.text)
        for seg in response.segments
    ]


def _whisper_api_chunked(client, audio_path: Path, file_size: int) -> list[TranscriptSegment]:
    """Split audio into ≤24 MB chunks via ffmpeg, transcribe each, merge."""
    try:
        import ffmpeg  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("ffmpeg-python required for chunked transcription") from exc

    probe = ffmpeg.probe(str(audio_path))
    duration = float(probe["format"]["duration"])
    n_chunks = math.ceil(file_size / (_WHISPER_API_MAX_BYTES * 0.9))
    chunk_duration = duration / n_chunks

    segments: list[TranscriptSegment] = []
    tmp_dir = audio_path.parent / "_chunks"
    tmp_dir.mkdir(exist_ok=True)

    for i in range(n_chunks):
        start_time = i * chunk_duration
        chunk_path = tmp_dir / f"{audio_path.stem}_chunk{i:03d}.mp3"
        console.print(f"  chunk {i + 1}/{n_chunks} ({start_time:.0f}s …)")

        if not chunk_path.exists():
            (
                ffmpeg
                .input(str(audio_path), ss=start_time, t=chunk_duration)
                .output(str(chunk_path), acodec="libmp3lame", audio_bitrate="128k")
                .run(quiet=True, overwrite_output=True)
            )

        chunk_segments = _whisper_api_single(client, chunk_path)
        for seg in chunk_segments:
            segments.append(
                TranscriptSegment(
                    seg.start + start_time,
                    seg.end + start_time,
                    seg.text,
                )
            )

    return segments


# ── Local Whisper ─────────────────────────────────────────────────────────────

def _transcribe_whisper_local(audio_path: Path) -> list[TranscriptSegment]:
    try:
        import whisper  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "openai-whisper is not installed. Run: pip install openai-whisper"
        ) from exc

    console.print("  Loading local Whisper model (medium) …")
    model = whisper.load_model("medium")
    result = model.transcribe(str(audio_path), verbose=False)
    return [
        TranscriptSegment(seg["start"], seg["end"], seg["text"])
        for seg in result["segments"]
    ]


# ── AssemblyAI ────────────────────────────────────────────────────────────────

def _transcribe_assemblyai(audio_path: Path, settings: "Settings") -> list[TranscriptSegment]:
    try:
        import assemblyai as aai  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "assemblyai is not installed. Run: pip install assemblyai"
        ) from exc

    aai.settings.api_key = settings.require_key("assemblyai_api_key")
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(audio_path))

    if transcript.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")

    segments: list[TranscriptSegment] = []
    for utt in transcript.utterances or []:
        segments.append(
            TranscriptSegment(utt.start / 1000.0, utt.end / 1000.0, utt.text)
        )
    if not segments and transcript.text:
        # Fallback: no utterances, return single segment
        segments.append(TranscriptSegment(0.0, 0.0, transcript.text))
    return segments


# ── Deepgram ──────────────────────────────────────────────────────────────────

def _transcribe_deepgram(audio_path: Path, settings: "Settings") -> list[TranscriptSegment]:
    try:
        from deepgram import DeepgramClient, PrerecordedOptions  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "deepgram-sdk is not installed. Run: pip install deepgram-sdk"
        ) from exc

    api_key = settings.require_key("deepgram_api_key")
    client = DeepgramClient(api_key)

    with audio_path.open("rb") as fh:
        payload = {"buffer": fh.read()}

    options = PrerecordedOptions(model="nova-2", smart_format=True, utterances=True)
    response = client.listen.prerecorded.v("1").transcribe_file(payload, options)

    segments: list[TranscriptSegment] = []
    results = response.results
    for utt in (results.utterances or []):
        segments.append(TranscriptSegment(utt.start, utt.end, utt.transcript))
    if not segments:
        # Fallback to words
        words = results.channels[0].alternatives[0].words or []
        if words:
            text = " ".join(w.word for w in words)
            segments.append(TranscriptSegment(words[0].start, words[-1].end, text))
    return segments


def segments_to_text(segments: list[TranscriptSegment]) -> str:
    """Concatenate all segment text into a single string."""
    return " ".join(s.text for s in segments if s.text)
