"""Tests for the enricher module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mix_extractor.enricher import enrich, TrackLinks
from mix_extractor.parser import Track


def _make_track(artist="Test Artist", title="Test Title") -> Track:
    return Track(index=1, artist=artist, title=title)


def _make_settings(**kwargs):
    s = MagicMock()
    s.spotify_client_id = kwargs.get("spotify_client_id", "")
    s.spotify_client_secret = kwargs.get("spotify_client_secret", "")
    s.discogs_token = kwargs.get("discogs_token", "")
    return s


class TestEnrich:
    @patch("mix_extractor.enricher._lookup_musicbrainz")
    @patch("mix_extractor.enricher._lookup_youtube_music")
    def test_returns_list_of_dicts(self, mock_yt, mock_mb):
        tracks = [_make_track()]
        results = enrich(tracks, _make_settings())
        assert len(results) == 1
        assert "links" in results[0]
        assert results[0]["artist"] == "Test Artist"

    @patch("mix_extractor.enricher._lookup_musicbrainz")
    @patch("mix_extractor.enricher._lookup_youtube_music")
    @patch("mix_extractor.enricher._lookup_spotify")
    def test_calls_spotify_when_keys_present(self, mock_sp, mock_yt, mock_mb):
        tracks = [_make_track()]
        settings = _make_settings(spotify_client_id="cid", spotify_client_secret="csec")
        enrich(tracks, settings)
        mock_sp.assert_called_once()

    @patch("mix_extractor.enricher._lookup_musicbrainz")
    @patch("mix_extractor.enricher._lookup_youtube_music")
    @patch("mix_extractor.enricher._lookup_spotify")
    def test_skips_spotify_without_keys(self, mock_sp, mock_yt, mock_mb):
        tracks = [_make_track()]
        enrich(tracks, _make_settings())
        mock_sp.assert_not_called()
