"""Normalizer — convert audio to a consistent WAV/MP3 format via ffmpeg."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()

# Whisper API accepts: mp3, mp4, mpeg, mpga, m4a, wav, webm (max 25 MB per chunk)
# We target 16 kHz mono WAV for local Whisper; MP3 for API-based transcribers.
_TARGET_SAMPLE_RATE = 16000
_TARGET_CHANNELS = 1


def normalize(input_path: Path, output_dir: Path, *, format: str = "mp3") -> Path:
    """Convert *input_path* to a standardized audio file and return its path.

    Parameters
    ----------
    input_path:
        Source audio file (any ffmpeg-supported format).
    output_dir:
        Directory to write the normalized file into.
    format:
        Output container format: ``"mp3"`` (default, for API backends) or
        ``"wav"`` (for local Whisper).
    """
    try:
        import ffmpeg  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg-python is not installed. Run: pip install ffmpeg-python"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_normalized.{format}"

    if output_path.exists():
        console.print(f"[dim]Normalized file already exists, reusing:[/dim] {output_path}")
        return output_path

    console.print(f"[bold blue]Normalizing[/bold blue] {input_path.name} → {format}")

    stream = ffmpeg.input(str(input_path))

    if format == "wav":
        stream = ffmpeg.output(
            stream,
            str(output_path),
            ar=_TARGET_SAMPLE_RATE,
            ac=_TARGET_CHANNELS,
            acodec="pcm_s16le",
        )
    else:
        stream = ffmpeg.output(
            stream,
            str(output_path),
            ar=_TARGET_SAMPLE_RATE,
            ac=_TARGET_CHANNELS,
            audio_bitrate="128k",
        )

    ffmpeg.run(stream, quiet=True, overwrite_output=True)
    console.print(f"[green]Normalized[/green] → {output_path}")
    return output_path
