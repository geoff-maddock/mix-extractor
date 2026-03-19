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
