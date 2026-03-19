"""Tests for the transcriber module."""

from __future__ import annotations

from mix_extractor.transcriber import TranscriptSegment, segments_to_text


class TestTranscriptSegment:
    def test_basic(self):
        seg = TranscriptSegment(0.0, 5.0, "  hello world  ")
        assert seg.text == "hello world"
        assert seg.start == 0.0
        assert seg.end == 5.0

    def test_repr(self):
        seg = TranscriptSegment(10.0, 20.0, "this is a test")
        assert "10s" in repr(seg)


class TestSegmentsToText:
    def test_concatenates(self):
        segs = [
            TranscriptSegment(0, 1, "Hello"),
            TranscriptSegment(1, 2, "world"),
        ]
        assert segments_to_text(segs) == "Hello world"

    def test_skips_empty(self):
        segs = [
            TranscriptSegment(0, 1, "Hello"),
            TranscriptSegment(1, 2, ""),
            TranscriptSegment(2, 3, "world"),
        ]
        assert segments_to_text(segs) == "Hello world"

    def test_empty_list(self):
        assert segments_to_text([]) == ""
