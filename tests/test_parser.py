"""Tests for the transcript parser."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mix_extractor.parser import Track, _parse_response


class TestParseResponse:
    def test_valid_json_array(self):
        raw = '[{"index": 1, "artist": "Bicep", "title": "Glue", "timestamp": "00:03:22"}]'
        tracks = _parse_response(raw)
        assert len(tracks) == 1
        assert tracks[0].artist == "Bicep"
        assert tracks[0].title == "Glue"

    def test_strips_markdown_fences(self):
        raw = '```json\n[{"index": 1, "artist": "Aphex Twin", "title": "Windowlicker"}]\n```'
        tracks = _parse_response(raw)
        assert len(tracks) == 1
        assert tracks[0].artist == "Aphex Twin"

    def test_empty_array(self):
        assert _parse_response("[]") == []

    def test_invalid_json_returns_empty(self):
        tracks = _parse_response("not json at all")
        assert tracks == []

    def test_non_array_returns_empty(self):
        tracks = _parse_response('{"artist": "Someone"}')
        assert tracks == []

    def test_optional_fields_have_defaults(self):
        raw = '[{"index": 1, "artist": "LFO", "title": "LFO"}]'
        tracks = _parse_response(raw)
        assert tracks[0].label == ""
        assert tracks[0].remix == ""
        assert tracks[0].extra_info == ""
        assert tracks[0].confidence == 1.0

    def test_multiple_tracks(self):
        raw = """[
            {"index": 1, "artist": "Boards of Canada", "title": "Music is Math"},
            {"index": 2, "artist": "Autechre", "title": "Gantz Graf", "label": "Warp"}
        ]"""
        tracks = _parse_response(raw)
        assert len(tracks) == 2
        assert tracks[1].label == "Warp"


class TestTrackModel:
    def test_track_fields(self):
        t = Track(index=1, artist="Plastikman", title="Spastik")
        assert t.index == 1
        assert t.artist == "Plastikman"
        assert t.title == "Spastik"
        assert t.timestamp == ""

    def test_model_dump(self):
        t = Track(index=1, artist="Daft Punk", title="Da Funk", label="Virgin")
        d = t.model_dump()
        assert d["artist"] == "Daft Punk"
        assert d["label"] == "Virgin"


class TestCallAnthropic:
    def _run(self, model, content):
        from mix_extractor.parser import _call_anthropic

        settings = MagicMock(llm_model=model)
        settings.require_key.return_value = "key"
        client = MagicMock()
        client.messages.create.return_value = MagicMock(content=content)
        with patch("anthropic.Anthropic", return_value=client):
            out = _call_anthropic("sys", "msg", settings)
        return out, client.messages.create.call_args.kwargs

    def test_skips_thinking_block(self):
        thinking = MagicMock(type="thinking")
        text = MagicMock(type="text", text="[]")
        out, kwargs = self._run("claude-haiku-5-5", [thinking, text])
        assert out == "[]"
        assert kwargs["extra_body"] == {"output_config": {"effort": "low"}}

    def test_no_text_block_returns_empty_array(self):
        out, _ = self._run("claude-haiku-5-5", [MagicMock(type="thinking")])
        assert out == "[]"

    def test_old_model_gets_no_effort(self):
        _, kwargs = self._run("claude-haiku-4-5-20251001", [MagicMock(type="text", text="[]")])
        assert "extra_body" not in kwargs


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-haiku-5-5", True),
        ("claude-sonnet-5-5", True),
        ("claude-opus-4-5", True),
        ("claude-sonnet-4-6", True),
        ("claude-haiku-4-5-20251001", False),
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-20250514", False),
        ("claude-opus-4-1", False),
        ("claude-3-7-sonnet-20250219", False),
    ],
)
def test_anthropic_supports_effort(model, expected):
    from mix_extractor.parser import _anthropic_supports_effort

    assert _anthropic_supports_effort(model) is expected
