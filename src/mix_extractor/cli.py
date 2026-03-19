"""CLI entry point for mix-extractor (Typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.prompt import Prompt

app = typer.Typer(
    name="mix-extractor",
    help="Extract tracklists from DJ mix audio files.",
    add_completion=False,
)
console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_input(source: str, input_dir: Path) -> tuple[Path, str]:
    """Return (local_path, display_name).  Downloads if source is a URL."""
    from mix_extractor.downloader import download, is_url  # noqa: PLC0415

    if is_url(source):
        local_path = download(source, input_dir)
    else:
        local_path = Path(source)
        if not local_path.is_absolute():
            # Try relative to input_dir first, then cwd
            candidate = input_dir / local_path
            if candidate.exists():
                local_path = candidate

    if not local_path.exists():
        console.print(f"[red]File not found:[/red] {local_path}")
        raise typer.Exit(1)

    return local_path, local_path.name


# ── analyze ───────────────────────────────────────────────────────────────────

@app.command()
def analyze(
    source: Annotated[str, typer.Argument(help="Path to audio file in content/input/, or a URL to download.")],
    llm: Annotated[Optional[str], typer.Option("--llm", help="LLM provider: openai | anthropic")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="LLM model name override")] = None,
    transcriber: Annotated[Optional[str], typer.Option("--transcriber", help="Transcription backend: whisper_api | whisper_local | assemblyai | deepgram")] = None,
    no_enrich: Annotated[bool, typer.Option("--no-enrich", help="Skip music API lookups")] = False,
    no_fingerprint: Annotated[bool, typer.Option("--no-fingerprint", help="Skip audio fingerprinting via AudD")] = False,
) -> None:
    """Run the full extraction pipeline on a mix file or URL."""
    from mix_extractor.config import get_settings  # noqa: PLC0415
    from mix_extractor.normalizer import normalize  # noqa: PLC0415
    from mix_extractor.transcriber import transcribe, segments_to_text  # noqa: PLC0415
    from mix_extractor.parser import parse_tracks  # noqa: PLC0415
    from mix_extractor.enricher import enrich  # noqa: PLC0415
    from mix_extractor.reporter import write_report  # noqa: PLC0415

    overrides = {}
    if llm:
        overrides["llm_provider"] = llm
    if model:
        overrides["llm_model"] = model
    if transcriber:
        overrides["transcription_provider"] = transcriber

    settings = get_settings(**overrides)
    local_path, display_name = _resolve_input(source, settings.input_dir)

    # 1. Normalize audio
    work_dir = settings.output_dir / local_path.stem / "_work"
    audio_fmt = "wav" if settings.transcription_provider == "whisper_local" else "mp3"
    normalized = normalize(local_path, work_dir, format=audio_fmt)

    # 2. Audio fingerprinting (runs in parallel with transcription conceptually,
    #    but we run it first since it doesn't depend on the transcript)
    fingerprinted = []
    if not no_fingerprint and settings.audd_api_key:
        from mix_extractor.fingerprinter import fingerprint_mix  # noqa: PLC0415
        fingerprinted = fingerprint_mix(normalized, settings)
    elif not no_fingerprint and not settings.audd_api_key:
        console.print("[dim]Fingerprinting skipped (no AUDD_API_KEY set)[/dim]")

    # 3. Transcribe
    segments = transcribe(normalized, settings)
    transcript = segments_to_text(segments)

    if not transcript.strip():
        console.print("[yellow]Transcription produced no text. Is there speech in the mix?[/yellow]")
        raise typer.Exit(1)

    # 4. Parse tracklist via LLM
    tracks = parse_tracks(transcript, segments, settings)

    # 5. Merge transcript tracks with fingerprinted tracks
    if fingerprinted:
        from mix_extractor.merger import merge_tracks  # noqa: PLC0415
        console.print("[bold blue]Merging transcript and fingerprint results[/bold blue] …")
        merged_dicts = merge_tracks(tracks, fingerprinted)
    else:
        merged_dicts = [t.model_dump() | {"detection_source": "transcript", "links": {}} for t in tracks]

    # 6. Enrich with music API links
    if no_enrich or not merged_dicts:
        enriched = merged_dicts
    else:
        console.print("[bold blue]Enriching tracks[/bold blue] …")
        enriched = enrich(merged_dicts, settings)

    # 7. Detect duration (best-effort)
    duration: float | None = None
    if segments:
        duration = segments[-1].end

    # 8. Write reports
    write_report(
        source_name=display_name,
        segments=segments,
        enriched_tracks=enriched,
        settings=settings,
        transcription_provider=settings.transcription_provider,
        duration_seconds=duration,
    )


# ── list ──────────────────────────────────────────────────────────────────────

@app.command(name="list")
def list_files() -> None:
    """List audio files currently in content/input/."""
    from mix_extractor.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    input_dir = settings.input_dir

    if not input_dir.exists():
        console.print(f"[yellow]Input directory does not exist:[/yellow] {input_dir}")
        raise typer.Exit(0)

    audio_exts = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".webm", ".mp4"}
    files = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in audio_exts)

    if not files:
        console.print(f"[yellow]No audio files found in[/yellow] {input_dir}")
        raise typer.Exit(0)

    console.print(f"\n[bold]Files in {input_dir}:[/bold]\n")
    for f in files:
        size_mb = f.stat().st_size / (1024 * 1024)
        console.print(f"  {f.name}  [dim]({size_mb:.1f} MB)[/dim]")
    console.print()


# ── config ────────────────────────────────────────────────────────────────────

@app.command()
def config() -> None:
    """Interactive wizard to set API keys in .env."""
    import shutil  # noqa: PLC0415
    from mix_extractor.config import _PROJECT_ROOT  # noqa: PLC0415

    env_path = _PROJECT_ROOT / ".env"

    if not env_path.exists():
        example = _PROJECT_ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, env_path)
            console.print(f"[green]Created .env from .env.example[/green]")
        else:
            env_path.touch()

    current = env_path.read_text(encoding="utf-8")
    lines = current.splitlines()

    keys_to_configure = [
        ("LLM_PROVIDER", "LLM provider (openai/anthropic)", "openai"),
        ("LLM_MODEL", "LLM model", "gpt-4o"),
        ("TRANSCRIPTION_PROVIDER", "Transcription provider (whisper_api/whisper_local/assemblyai/deepgram)", "whisper_api"),
        ("OPENAI_API_KEY", "OpenAI API key", ""),
        ("ANTHROPIC_API_KEY", "Anthropic API key", ""),
        ("ASSEMBLYAI_API_KEY", "AssemblyAI API key (optional)", ""),
        ("DEEPGRAM_API_KEY", "Deepgram API key (optional)", ""),
        ("SPOTIFY_CLIENT_ID", "Spotify client ID (optional)", ""),
        ("SPOTIFY_CLIENT_SECRET", "Spotify client secret (optional)", ""),
        ("DISCOGS_TOKEN", "Discogs token (optional)", ""),
    ]

    new_values: dict[str, str] = {}
    console.print("\n[bold]Configure mix-extractor API keys[/bold]")
    console.print("[dim]Press Enter to keep current value. Leave blank to skip optional keys.[/dim]\n")

    for env_key, label, default in keys_to_configure:
        # Find current value in file
        current_val = default
        for line in lines:
            if line.startswith(f"{env_key}="):
                current_val = line.split("=", 1)[1].strip()
                break

        prompt_text = f"{label}"
        if current_val and current_val != "sk-..." and current_val != "sk-ant-...":
            prompt_text += f" [{current_val}]"

        value = Prompt.ask(prompt_text, default=current_val, show_default=False)
        if value:
            new_values[env_key] = value

    # Rewrite .env
    new_lines: list[str] = []
    written: set[str] = set()
    for line in lines:
        matched = False
        for key in new_values:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={new_values[key]}")
                written.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for key, val in new_values.items():
        if key not in written:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    console.print(f"\n[green]Saved:[/green] {env_path}")


if __name__ == "__main__":
    app()
