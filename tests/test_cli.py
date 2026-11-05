"""Tests for the analyze pipeline's handling of fingerprint results (issue #12)."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mix_extractor.cli import app
from mix_extractor.config import Settings
from mix_extractor.fingerprinter import FingerprintedTrack
from mix_extractor.transcriber import TranscriptSegment

runner = CliRunner()


@pytest.fixture
def pipeline(tmp_path):
    """Patch every pipeline stage; returns the mocks so tests can set behaviour."""
    audio = tmp_path / "mix.mp3"
    audio.write_bytes(b"x")
    settings = Settings(audd_api_key="key", input_dir=tmp_path, output_dir=tmp_path / "out")
    mocks = {
        "fingerprint": MagicMock(return_value=[FingerprintedTrack(timestamp=0, artist="A", title="One")]),
        "transcribe": MagicMock(return_value=[]),
        "report": MagicMock(),
        "parse": MagicMock(return_value=[]),
    }
    with ExitStack() as stack:
        stack.enter_context(patch("mix_extractor.config.get_settings", side_effect=lambda **o: settings.model_copy(update=o)))
        stack.enter_context(patch("mix_extractor.cli._resolve_input", return_value=(audio, "mix.mp3")))
        stack.enter_context(patch("mix_extractor.normalizer.normalize", return_value=audio))
        stack.enter_context(patch("mix_extractor.fingerprinter.fingerprint_mix", mocks["fingerprint"]))
        stack.enter_context(patch("mix_extractor.transcriber.transcribe", mocks["transcribe"]))
        stack.enter_context(patch("mix_extractor.parser.parse_tracks", mocks["parse"]))
        stack.enter_context(patch("mix_extractor.reporter.write_report", mocks["report"]))
        mocks["audio"] = audio
        yield mocks


def _analyze(*extra):
    return runner.invoke(app, ["analyze", "mix.mp3", "--no-enrich", *extra])


def test_empty_transcript_writes_fingerprint_report(pipeline):
    result = _analyze()
    assert result.exit_code == 0, result.output
    tracks = pipeline["report"].call_args.kwargs["enriched_tracks"]
    assert [t["artist"] for t in tracks] == ["A"]
    pipeline["parse"].assert_not_called()


def test_empty_transcript_and_no_fingerprints_exits_with_error(pipeline):
    pipeline["fingerprint"].return_value = []
    result = _analyze()
    assert result.exit_code == 1
    pipeline["report"].assert_not_called()


def test_transcription_error_mentions_saved_results(pipeline):
    pipeline["transcribe"].side_effect = RuntimeError("401 bad key")
    result = _analyze()
    assert result.exit_code != 0
    assert "will be reused on the next run" in result.output
    assert isinstance(result.exception, RuntimeError)


def test_fingerprint_options_passed_through(pipeline):
    pipeline["transcribe"].return_value = [TranscriptSegment(start=0, end=5, text="hello")]
    result = _analyze("--fingerprint-interval", "60", "--no-second-pass", "--refingerprint")
    assert result.exit_code == 0, result.output
    args, kwargs = pipeline["fingerprint"].call_args
    assert args[0] == pipeline["audio"]  # original audio, not the normalized copy
    assert kwargs["sample_interval"] == 60
    assert kwargs["second_pass"] is False
    assert kwargs["refresh"] is True
    assert kwargs["cache_path"].name == "fingerprints.json"
