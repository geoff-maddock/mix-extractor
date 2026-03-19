"""Tests for the fingerprinter module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mix_extractor.fingerprinter import (
    FingerprintedTrack,
    _query_audd,
    fingerprint_mix,
)


class TestFingerprintedTrack:
    def test_timestamp_str_with_hours(self):
        track = FingerprintedTrack(timestamp=3661.0, artist="A", title="T")
        assert track.timestamp_str == "01:01:01"

    def test_timestamp_str_without_hours(self):
        track = FingerprintedTrack(timestamp=90.0, artist="A", title="T")
        assert track.timestamp_str == "01:30"

    def test_default_fields(self):
        track = FingerprintedTrack(timestamp=0.0, artist="A", title="T")
        assert track.album == ""
        assert track.label == ""
        assert track.score == 0
        assert track.links == {}
        assert track.detection_source == "fingerprint"


class TestQueryAudd:
    def _success_response(self, artist="Bicep", title="Glue"):
        return {
            "status": "success",
            "result": {
                "artist": artist,
                "title": title,
                "album": "Bicep",
                "label": "Ninja Tune",
                "release_date": "2017-09-01",
                "score": 95,
                "spotify": {"external_urls": {"spotify": "https://open.spotify.com/track/abc"}},
                "apple_music": {"url": "https://music.apple.com/gb/album/abc"},
            },
        }

    def test_successful_identification(self):
        mock_response = MagicMock()
        mock_response.json.return_value = self._success_response()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert track is not None
        assert track.artist == "Bicep"
        assert track.title == "Glue"
        assert track.timestamp == 60.0
        assert track.label == "Ninja Tune"
        assert track.detection_source == "fingerprint"

    def test_spotify_link_extracted(self):
        mock_response = MagicMock()
        mock_response.json.return_value = self._success_response()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert "spotify" in track.links
        assert "open.spotify.com" in track.links["spotify"]

    def test_no_result_returns_none(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "success", "result": None}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert track is None

    def test_error_status_returns_none(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "error", "error": {"message": "No API key"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert track is None

    def test_network_error_returns_none(self):
        with patch("httpx.post", side_effect=Exception("Connection refused")):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert track is None

    def test_empty_artist_and_title_returns_none(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "result": {"artist": "", "title": ""},
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            track = _query_audd(b"fake_audio", 60.0, "test_key")

        assert track is None


class TestFingerprintMix:
    def test_no_api_key_returns_empty(self):
        settings = MagicMock()
        settings.audd_api_key = ""
        tracks = fingerprint_mix(Path("dummy.mp3"), settings)
        assert tracks == []

    def test_deduplicates_consecutive_identical_results(self, tmp_path):
        """Same track detected at consecutive sample points → only one entry."""
        settings = MagicMock()
        settings.audd_api_key = "real_key"

        fp_track = FingerprintedTrack(timestamp=0, artist="Bicep", title="Glue")

        fake_probe = {"format": {"duration": "200"}}

        with (
            patch("ffmpeg.probe", return_value=fake_probe),
            patch("mix_extractor.fingerprinter._extract_snippet", return_value=b"audio"),
            patch("mix_extractor.fingerprinter._query_audd", return_value=fp_track),
        ):
            results = fingerprint_mix(tmp_path / "mix.mp3", settings, sample_interval=90)

        # Two sample points (0s, 90s) but same track → deduplicated to 1
        assert len(results) == 1
