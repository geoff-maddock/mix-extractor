"""Tests for the merger module."""

from __future__ import annotations

from mix_extractor.fingerprinter import FingerprintedTrack
from mix_extractor.merger import merge_tracks, _normalise, _word_overlap, _ts_to_seconds
from mix_extractor.parser import Track


def _make_track(index=1, artist="Artist", title="Title", timestamp="00:01:00", label="") -> Track:
    return Track(index=index, artist=artist, title=title, timestamp=timestamp, label=label)


def _make_fp(timestamp=60.0, artist="Artist", title="Title", label="", score=95) -> FingerprintedTrack:
    return FingerprintedTrack(timestamp=timestamp, artist=artist, title=title, label=label, score=score)


class TestMergeTracksFingerprinting:
    def test_fingerprint_only_returns_fingerprint_source(self):
        fp = _make_fp(timestamp=60.0, artist="Bicep", title="Glue")
        result = merge_tracks([], [fp])
        assert len(result) == 1
        assert result[0]["artist"] == "Bicep"
        assert result[0]["detection_source"] == "fingerprint"

    def test_transcript_only_returns_transcript_source(self):
        t = _make_track(artist="Bicep", title="Glue", timestamp="00:01:00")
        result = merge_tracks([t], [])
        assert len(result) == 1
        assert result[0]["artist"] == "Bicep"
        assert result[0]["detection_source"] == "transcript"

    def test_matching_tracks_merged_as_both(self):
        t = _make_track(artist="Bicep", title="Glue", timestamp="00:01:00")
        fp = _make_fp(timestamp=60.0, artist="Bicep", title="Glue")
        result = merge_tracks([t], [fp])
        assert len(result) == 1
        assert result[0]["detection_source"] == "fingerprint+transcript"

    def test_unmatched_transcript_appended(self):
        t = _make_track(artist="Aphex Twin", title="Windowlicker", timestamp="00:05:00")
        fp = _make_fp(timestamp=60.0, artist="Bicep", title="Glue")
        result = merge_tracks([t], [fp])
        assert len(result) == 2
        sources = {r["detection_source"] for r in result}
        assert "transcript" in sources
        assert "fingerprint" in sources

    def test_transcript_label_merged_into_fingerprint(self):
        t = _make_track(artist="Bicep", title="Glue", timestamp="00:01:00", label="Ninja Tune")
        fp = _make_fp(timestamp=65.0, artist="Bicep", title="Glue", label="")
        result = merge_tracks([t], [fp])
        assert result[0]["label"] == "Ninja Tune"

    def test_fingerprint_label_takes_precedence_if_set(self):
        t = _make_track(artist="Bicep", title="Glue", timestamp="00:01:00", label="TX Label")
        fp = _make_fp(timestamp=65.0, artist="Bicep", title="Glue", label="FP Label")
        result = merge_tracks([t], [fp])
        assert result[0]["label"] == "FP Label"

    def test_result_sorted_by_timestamp(self):
        t1 = _make_track(index=1, artist="Later Artist", title="Later", timestamp="00:05:00")
        t2 = _make_track(index=2, artist="Earlier Artist", title="Earlier", timestamp="00:01:00")
        result = merge_tracks([t1, t2], [])
        times = [r.get("_timestamp_sec", r.get("timestamp")) for r in result]
        # Both are transcript only; earlier timestamp should be index 1
        artists = [r["artist"] for r in result]
        assert artists.index("Earlier Artist") < artists.index("Later Artist")

    def test_result_reindexed(self):
        t1 = _make_track(index=1, artist="A", title="A", timestamp="00:01:00")
        t2 = _make_track(index=2, artist="B", title="B", timestamp="00:02:00")
        result = merge_tracks([t1, t2], [])
        assert result[0]["index"] == 1
        assert result[1]["index"] == 2

    def test_fingerprint_links_preserved(self):
        fp = _make_fp(timestamp=60.0, artist="Bicep", title="Glue")
        fp.links = {"spotify": "https://open.spotify.com/track/abc"}
        result = merge_tracks([], [fp])
        assert result[0]["links"]["spotify"] == "https://open.spotify.com/track/abc"

    def test_timestamp_mismatch_prevents_merge(self):
        """Tracks with the same name but >90s apart should NOT be merged."""
        t = _make_track(artist="Bicep", title="Glue", timestamp="00:40:00")  # 2400s
        fp = _make_fp(timestamp=60.0, artist="Bicep", title="Glue")  # 60s
        result = merge_tracks([t], [fp])
        assert len(result) == 2


class TestHelpers:
    def test_normalise_lowercases_and_strips(self):
        assert _normalise("Aphex Twin") == "aphex twin"
        assert _normalise("Something (Remix)") == "something remix"

    def test_word_overlap_identical(self):
        assert _word_overlap("aphex twin", "aphex twin") == 1.0

    def test_word_overlap_no_overlap(self):
        assert _word_overlap("bicep glue", "autechre gantz") == 0.0

    def test_word_overlap_partial(self):
        score = _word_overlap("bicep glue remix", "bicep glue")
        assert 0.5 < score < 1.0

    def test_word_overlap_empty_strings(self):
        assert _word_overlap("", "") == 1.0

    def test_ts_to_seconds_hhmmss(self):
        assert _ts_to_seconds("01:02:03") == 3723.0

    def test_ts_to_seconds_mmss(self):
        assert _ts_to_seconds("02:30") == 150.0

    def test_ts_to_seconds_invalid(self):
        import math
        assert math.isinf(_ts_to_seconds(""))
