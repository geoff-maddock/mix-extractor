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

        with patch("ffmpeg.probe", return_value=fake_probe), \
             patch("mix_extractor.fingerprinter._extract_snippet", return_value=b"audio"), \
             patch("mix_extractor.fingerprinter._query_audd", return_value=fp_track):
            results = fingerprint_mix(tmp_path / "mix.mp3", settings, sample_interval=90)

        # Two sample points (0s, 90s) but same track → deduplicated to 1
        assert len(results) == 1


def test_fingerprint_interval_setting(monkeypatch):
    import pydantic
    from mix_extractor.config import get_settings

    monkeypatch.setenv("FINGERPRINT_INTERVAL", "45")
    assert get_settings().fingerprint_interval == 45
    assert get_settings(fingerprint_interval=60).fingerprint_interval == 60
    with pytest.raises(pydantic.ValidationError):
        get_settings(fingerprint_interval=5)


# ── Issue #12: second pass, saved results, consolidation ─────────────────────

from mix_extractor.fingerprinter import REPEAT_GAP_SECONDS, consolidate_hits  # noqa: E402


def _hit(t, artist="Bicep", title="Glue", **kw):
    return FingerprintedTrack(timestamp=t, artist=artist, title=title, **kw)


class TestConsolidateHits:
    def test_single_hit_is_unconfirmed(self):
        [play] = consolidate_hits([_hit(0)])
        assert play.hits == 1 and play.confirmed is False

    def test_two_hits_confirm(self):
        [play] = consolidate_hits([_hit(0), _hit(90)])
        assert play.hits == 2 and play.confirmed is True

    def test_non_consecutive_hits_combined(self):
        hits = [_hit(0), _hit(90, artist="Other", title="Song"), _hit(180)]
        plays = consolidate_hits(hits)
        assert [(p.artist, p.hits) for p in plays] == [("Bicep", 2), ("Other", 1)]

    def test_keeps_earliest_timestamp_regardless_of_order(self):
        [play] = consolidate_hits([_hit(135), _hit(45)])
        assert play.timestamp == 45

    def test_ignores_case_and_punctuation(self):
        [play] = consolidate_hits([_hit(0, "Boy Harsher", "Ice Pick (Walk Away)"),
                                   _hit(90, "boy harsher", "Ice Pick - Walk Away")])
        assert play.hits == 2

    def test_far_apart_hits_are_separate_plays(self):
        plays = consolidate_hits([_hit(0), _hit(REPEAT_GAP_SECONDS)])
        assert len(plays) == 2 and all(not p.confirmed for p in plays)

    def test_long_track_chains_hits(self):
        # Hits every 90s for 15 minutes stay one play: the gap is measured from the latest hit.
        plays = consolidate_hits([_hit(t) for t in range(0, 900, 90)])
        assert len(plays) == 1 and plays[0].hits == 10

    def test_merges_links_and_does_not_mutate_input(self):
        a, b = _hit(0, links={"spotify": "s"}), _hit(90, links={"deezer": "d"}, album="LP")
        [play] = consolidate_hits([a, b])
        assert play.links == {"spotify": "s", "deezer": "d"} and play.album == "LP"
        assert a.links == {"spotify": "s"} and a.hits == 1


def _settings():
    s = MagicMock()
    s.audd_api_key = "key"
    return s


def _run(tmp_path, answers, *, duration="300", **kw):
    """Run fingerprint_mix with AudD answers keyed by sample start time; returns (results, queried)."""
    queried = []

    def fake_query(_bytes, start, _key):
        queried.append(start)
        return answers.get(start)

    audio = tmp_path / "mix.mp3"
    audio.write_bytes(b"x" * 10)
    with patch("ffmpeg.probe", return_value={"format": {"duration": duration}}), \
         patch("mix_extractor.fingerprinter._extract_snippet", return_value=b"audio"), \
         patch("mix_extractor.fingerprinter._query_audd", side_effect=fake_query), \
         patch("mix_extractor.fingerprinter.time.sleep"):
        results = fingerprint_mix(audio, _settings(), sample_interval=90, **kw)
    return results, queried


class TestSecondPass:
    def test_retries_misses_at_half_interval(self, tmp_path):
        results, queried = _run(tmp_path, {0: _hit(0), 135: _hit(135, "B", "Two")})
        assert queried == [0, 90, 180, 270, 135, 225]  # 270+45=315 is past the end
        assert [r.timestamp for r in results] == [0, 135]  # merged in time order

    def test_can_be_disabled(self, tmp_path):
        _, queried = _run(tmp_path, {}, second_pass=False)
        assert queried == [0, 90, 180, 270]

    def test_limit_error_keeps_partial_results(self, tmp_path):
        from mix_extractor.fingerprinter import AudDApiLimitError

        def fake_query(_b, start, _k):
            if start == 0:
                return _hit(0)
            raise AudDApiLimitError("limit reached")

        cache = tmp_path / "fp.json"
        (tmp_path / "mix.mp3").write_bytes(b"x")
        with patch("ffmpeg.probe", return_value={"format": {"duration": "300"}}), \
             patch("mix_extractor.fingerprinter._extract_snippet", return_value=b"a"), \
             patch("mix_extractor.fingerprinter._query_audd", side_effect=fake_query), \
             patch("mix_extractor.fingerprinter.time.sleep"):
            results = fingerprint_mix(tmp_path / "mix.mp3", _settings(), cache_path=cache)
        assert len(results) == 1
        assert not cache.exists()  # incomplete runs aren't saved for reuse


class TestSavedResults:
    def test_saved_then_reused_without_requests(self, tmp_path):
        cache = tmp_path / "_work" / "fingerprints.json"
        first, q1 = _run(tmp_path, {0: _hit(0), 90: _hit(90)}, cache_path=cache)
        assert cache.exists() and q1
        second, q2 = _run(tmp_path, {}, cache_path=cache)
        assert q2 == []
        assert [(r.artist, r.hits, r.confirmed) for r in second] == [("Bicep", 2, True)]

    def test_settings_change_triggers_new_run(self, tmp_path):
        cache = tmp_path / "fingerprints.json"
        _run(tmp_path, {}, cache_path=cache)
        _, queried = _run(tmp_path, {}, cache_path=cache, second_pass=False)
        assert queried

    def test_audio_change_triggers_new_run(self, tmp_path):
        cache = tmp_path / "fingerprints.json"
        _run(tmp_path, {}, cache_path=cache)
        (tmp_path / "mix.mp3").write_bytes(b"different length")
        with patch("ffmpeg.probe", return_value={"format": {"duration": "100"}}), \
             patch("mix_extractor.fingerprinter._extract_snippet", return_value=b"a"), \
             patch("mix_extractor.fingerprinter._query_audd", return_value=None) as q, \
             patch("mix_extractor.fingerprinter.time.sleep"):
            fingerprint_mix(tmp_path / "mix.mp3", _settings(), cache_path=cache)
        assert q.called

    def test_refresh_forces_new_run(self, tmp_path):
        cache = tmp_path / "fingerprints.json"
        _run(tmp_path, {}, cache_path=cache)
        _, queried = _run(tmp_path, {}, cache_path=cache, refresh=True)
        assert queried

    def test_corrupt_cache_is_ignored(self, tmp_path):
        cache = tmp_path / "fingerprints.json"
        cache.write_text("not json")
        _, queried = _run(tmp_path, {}, cache_path=cache)
        assert queried
