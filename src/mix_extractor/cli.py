"""CLI entry point for mix-extractor (Typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from typing_extensions import Annotated
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
    fingerprint_only: Annotated[bool, typer.Option("--fingerprint-only", help="Run audio fingerprinting only — skip transcription and LLM parsing")] = False,
) -> None:
    """Run the full extraction pipeline on a mix file or URL."""
    from mix_extractor.config import get_settings  # noqa: PLC0415
    from mix_extractor.normalizer import normalize  # noqa: PLC0415
    from mix_extractor.transcriber import transcribe, segments_to_text, segments_to_timestamped_text  # noqa: PLC0415
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

    # 2. Audio fingerprinting
    fingerprinted = []
    if not no_fingerprint and settings.audd_api_key:
        from mix_extractor.fingerprinter import fingerprint_mix  # noqa: PLC0415
        fingerprinted = fingerprint_mix(normalized, settings)
    elif not no_fingerprint and not settings.audd_api_key:
        console.print("[dim]Fingerprinting skipped (no AUDD_API_KEY set)[/dim]")

    # ── fingerprint-only short-circuit ────────────────────────────────────────
    if fingerprint_only:
        if not fingerprinted:
            console.print("[yellow]Fingerprinting returned no results. Is AUDD_API_KEY set?[/yellow]")
            raise typer.Exit(1)
        merged_dicts = [
            {
                "index": i + 1,
                "timestamp": fp.timestamp_str,
                "artist": fp.artist,
                "title": fp.title,
                "label": fp.label,
                "remix": "",
                "extra_info": f"album: {fp.album}" if fp.album else "",
                "album": fp.album,
                "release_date": fp.release_date,
                "score": fp.score,
                "confidence": round(fp.score / 100, 2) if fp.score else 1.0,
                "detection_source": fp.detection_source,
                "links": dict(fp.links),
            }
            for i, fp in enumerate(fingerprinted)
        ]
        if not no_enrich:
            console.print("[bold blue]Enriching tracks[/bold blue] …")
            merged_dicts = enrich(merged_dicts, settings)
        write_report(
            source_name=display_name,
            segments=[],
            enriched_tracks=merged_dicts,
            settings=settings,
            transcription_provider="fingerprint_only",
            duration_seconds=None,
        )
        return
    # ── end fingerprint-only ──────────────────────────────────────────────────

    # 3. Transcribe
    segments = transcribe(normalized, settings)
    transcript = segments_to_text(segments)

    if not transcript.strip():
        console.print("[yellow]Transcription produced no text. Is there speech in the mix?[/yellow]")
        raise typer.Exit(1)

    # 4. Parse tracklist via LLM (use timestamped text so the LLM can assign times)
    timestamped_transcript = segments_to_timestamped_text(segments)
    tracks = parse_tracks(timestamped_transcript, segments, settings)

    # 5. Merge transcript tracks with fingerprinted tracks
    if fingerprinted:
        from mix_extractor.merger import merge_tracks  # noqa: PLC0415
        console.print("[bold blue]Merging transcript and fingerprint results[/bold blue] …")
        merged_dicts = merge_tracks(tracks, fingerprinted)
    else:
        merged_dicts = []
        for t in tracks:
            d = t.model_dump()
            d.update({"detection_source": "transcript", "links": {}})
            merged_dicts.append(d)

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


# ── publish ───────────────────────────────────────────────────────────────────

@app.command()
def publish(
    mix_name: Annotated[str, typer.Argument(help="Name of the mix output directory (e.g. 'Fracture - 14 January 2026').")],
    title: Annotated[Optional[str], typer.Option("--title", help="Title for the buymusic.club list (defaults to mix name).")] = None,
    all_tracks: Annotated[bool, typer.Option("--all", help="Include all tracks, not just bookmarked ones.")] = False,
) -> None:
    """Publish a mix tracklist to buymusic.club."""
    from mix_extractor.config import get_settings  # noqa: PLC0415
    from mix_extractor.buymusic_club import publish_mix, BuymusicClubError  # noqa: PLC0415

    settings = get_settings()

    if not settings.buymusic_club_username or not settings.buymusic_club_password:
        console.print(
            "[red]Missing buymusic.club credentials.[/red]\n"
            "Add [bold]BUYMUSIC_CLUB_USERNAME[/bold] and [bold]BUYMUSIC_CLUB_PASSWORD[/bold] "
            "to your .env file, then run [bold]mix-extractor config[/bold]."
        )
        raise typer.Exit(1)

    console.print(f"\n[bold blue]Publishing[/bold blue] {mix_name} → buymusic.club …\n")
    try:
        url = publish_mix(
            mix_name=mix_name,
            settings=settings,
            list_title=title,
            include_all=all_tracks,
        )
        console.print(f"\n[bold green]Done![/bold green] {url}\n")
    except BuymusicClubError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


# ── reprocess ─────────────────────────────────────────────────────────────────

_AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".webm", ".mp4"}


def _find_source_audio(mix_dir: Path, source_name: str, input_dir: Path) -> Path | None:
    """Locate the original audio for a previously analyzed mix.

    Search order:
      1. Original file in content/input/ (from tracks.json ``mix.source``)
      2. Any audio file in content/input/ whose stem matches the output folder name
      3. The cached normalized file in _work/
    """
    # 1. Exact source name in input dir
    candidate = input_dir / source_name
    if candidate.exists():
        return candidate

    # 2. Stem match (handles renamed extensions)
    mix_stem = mix_dir.name
    for f in input_dir.iterdir():
        if f.suffix.lower() in _AUDIO_EXTS and f.stem == mix_stem:
            return f

    # 3. Fall back to normalized cache in _work/
    work_dir = mix_dir / "_work"
    if work_dir.is_dir():
        for f in sorted(work_dir.iterdir()):
            if f.suffix.lower() in _AUDIO_EXTS and "normalized" in f.name:
                return f

    return None


@app.command()
def reprocess(
    mix_name: Annotated[Optional[str], typer.Argument(help="Name of a previously analyzed mix folder (tab-complete friendly). Omit to choose interactively.")] = None,
    llm: Annotated[Optional[str], typer.Option("--llm", help="LLM provider: openai | anthropic")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="LLM model name override")] = None,
    transcriber: Annotated[Optional[str], typer.Option("--transcriber", help="Transcription backend: whisper_api | whisper_local | assemblyai | deepgram")] = None,
    no_enrich: Annotated[bool, typer.Option("--no-enrich", help="Skip music API lookups")] = False,
    no_fingerprint: Annotated[bool, typer.Option("--no-fingerprint", help="Skip audio fingerprinting via AudD")] = False,
    fingerprint_only: Annotated[bool, typer.Option("--fingerprint-only", help="Run audio fingerprinting only")] = False,
    no_transcribe: Annotated[bool, typer.Option("--no-transcribe", help="Skip transcription — re-enrich using existing tracks.json")] = False,
) -> None:
    """Re-run the extraction pipeline on a previously analyzed mix."""
    import json as _json  # noqa: PLC0415
    from mix_extractor.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    output_dir = settings.output_dir

    # ── discover available mixes ──────────────────────────────────────────────
    if not output_dir.is_dir():
        console.print("[red]No output directory found.[/red] Run [bold]analyze[/bold] first.")
        raise typer.Exit(1)

    available = sorted(
        d.name for d in output_dir.iterdir()
        if d.is_dir() and (d / "tracks.json").exists()
    )
    if not available:
        console.print("[yellow]No previously analyzed mixes found.[/yellow]")
        raise typer.Exit(1)

    # ── interactive picker when no name given ────────────────────────────────
    if mix_name is None:
        console.print("\n[bold]Previously analyzed mixes:[/bold]\n")
        for i, name in enumerate(available, 1):
            console.print(f"  [dim]{i:3d}.[/dim] {name}")
        console.print()
        choice = Prompt.ask(
            "Enter number or name",
            default="1",
        )
        if choice.isdigit():
            idx = int(choice) - 1
            if idx < 0 or idx >= len(available):
                console.print("[red]Invalid selection.[/red]")
                raise typer.Exit(1)
            mix_name = available[idx]
        else:
            mix_name = choice

    mix_dir = output_dir / mix_name
    if not mix_dir.is_dir():
        # Try fuzzy match
        matches = [n for n in available if mix_name.lower() in n.lower()]
        if len(matches) == 1:
            mix_name = matches[0]
            mix_dir = output_dir / mix_name
        elif len(matches) > 1:
            console.print(f"[yellow]Ambiguous name '{mix_name}'. Matches:[/yellow]")
            for m in matches:
                console.print(f"  {m}")
            raise typer.Exit(1)
        else:
            console.print(f"[red]Mix not found:[/red] {mix_name}")
            raise typer.Exit(1)

    console.print(f"\n[bold blue]Re-processing[/bold blue] {mix_name}\n")

    # ── no-transcribe shortcut: just re-enrich existing tracks ────────────────
    if no_transcribe:
        tracks_path = mix_dir / "tracks.json"
        if not tracks_path.exists():
            console.print("[red]No tracks.json found — cannot skip transcription.[/red]")
            raise typer.Exit(1)

        data = _json.loads(tracks_path.read_text(encoding="utf-8"))
        existing_tracks = data.get("tracks", [])
        if not existing_tracks:
            console.print("[yellow]tracks.json has no tracks.[/yellow]")
            raise typer.Exit(1)

        console.print(f"  [dim]Loaded {len(existing_tracks)} existing track(s) from tracks.json[/dim]")

        if not no_enrich:
            from mix_extractor.enricher import enrich  # noqa: PLC0415

            overrides = {}
            if llm:
                overrides["llm_provider"] = llm
            if model:
                overrides["llm_model"] = model
            enrichment_settings = get_settings(**overrides)
            console.print("[bold blue]Enriching tracks[/bold blue] …")
            existing_tracks = enrich(existing_tracks, enrichment_settings)

        from mix_extractor.reporter import write_report  # noqa: PLC0415

        source_name = data.get("mix", {}).get("source", f"{mix_name}.mp3")
        duration = data.get("mix", {}).get("duration_seconds")
        provider = data.get("mix", {}).get("transcription_provider", "unknown")

        write_report(
            source_name=source_name,
            segments=[],
            enriched_tracks=existing_tracks,
            settings=settings,
            transcription_provider=provider,
            duration_seconds=duration,
        )
        return

    # ── full re-run: locate source audio ──────────────────────────────────────
    tracks_path = mix_dir / "tracks.json"
    source_name = ""
    if tracks_path.exists():
        data = _json.loads(tracks_path.read_text(encoding="utf-8"))
        source_name = data.get("mix", {}).get("source", "")

    audio_path = _find_source_audio(mix_dir, source_name, settings.input_dir)
    if audio_path is None:
        console.print(
            f"[red]Cannot find source audio for '{mix_name}'.[/red]\n"
            f"Looked in: {settings.input_dir} and {mix_dir / '_work'}\n"
            f"Use [bold]--no-transcribe[/bold] to re-enrich without re-transcribing."
        )
        raise typer.Exit(1)

    console.print(f"  [dim]Source audio:[/dim] {audio_path}")

    # Delegate to the analyze pipeline with the resolved path
    analyze(
        source=str(audio_path),
        llm=llm,
        model=model,
        transcriber=transcriber,
        no_enrich=no_enrich,
        no_fingerprint=no_fingerprint,
        fingerprint_only=fingerprint_only,
    )


# ── import-text ──────────────────────────────────────────────────────────────

@app.command(name="import-text")
def import_text(
    source: Annotated[str, typer.Argument(help="Path to a text file containing a tracklist, or '-' for stdin.")],
    name: Annotated[Optional[str], typer.Option("--name", help="Mix name for the output folder (auto-generated if omitted).")] = None,
    llm: Annotated[Optional[str], typer.Option("--llm", help="LLM provider: openai | anthropic")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="LLM model name override")] = None,
    no_enrich: Annotated[bool, typer.Option("--no-enrich", help="Skip link enrichment.")] = False,
) -> None:
    """Import a tracklist from a text file (or stdin) and optionally enrich with links."""
    import sys  # noqa: PLC0415

    from mix_extractor.config import get_settings  # noqa: PLC0415
    from mix_extractor.text_import import import_tracklist  # noqa: PLC0415
    from mix_extractor.reporter import _print_summary  # noqa: PLC0415

    overrides: dict = {}
    if llm:
        overrides["llm_provider"] = llm
    if model:
        overrides["llm_model"] = model
    settings = get_settings(**overrides)

    # Read text from stdin or file
    if source == "-":
        text = sys.stdin.read()
    else:
        p = Path(source)
        if not p.exists():
            console.print(f"[red]File not found:[/red] {p}")
            raise typer.Exit(1)
        text = p.read_text(encoding="utf-8")

    if not text.strip():
        console.print("[red]Input text is empty.[/red]")
        raise typer.Exit(1)

    tracks, out_dir = import_tracklist(
        text=text,
        mix_name=name or "",
        settings=settings,
        run_enrich=not no_enrich,
    )

    if not tracks:
        console.print("[yellow]No tracks were extracted. Nothing written.[/yellow]")
        raise typer.Exit(1)

    _print_summary(tracks)


# ── serve ─────────────────────────────────────────────────────────────────────

@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port number")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes (development)")] = False,
) -> None:
    """Start the web GUI server."""
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        console.print(
            "[red]uvicorn is not installed.[/red] "
            "Run: [bold]pip install 'mix-extractor[web]'[/bold]"
        )
        raise typer.Exit(1)

    console.print(f"\n[bold green]mix-extractor web UI[/bold green]")
    console.print(f"  Open [link=http://{host}:{port}]http://{host}:{port}[/link] in your browser\n")
    uvicorn.run(
        "mix_extractor.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
