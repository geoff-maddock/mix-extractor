"""Merger — combine transcript-parsed tracks with fingerprinted tracks.

Strategy
--------
1. Start with the fingerprinted tracks as the ground-truth backbone
   (they have confirmed artist/title from the audio itself).
2. For each fingerprinted track, check if a transcript track at a similar
   timestamp (±60s) matches on artist/title (fuzzy). If so, merge any extra
   info (label, remix, extra_info) from the transcript entry.
3. Any transcript tracks that have NO matching fingerprint entry are appended
   as lower-confidence entries (they may be announced but not yet playing, or
   the fingerprinter didn't sample that window).
4. Return the merged list sorted by timestamp.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mix_extractor.fingerprinter import FingerprintedTrack
    from mix_extractor.parser import Track

# Timestamp tolerance for matching fingerprint ↔ transcript (seconds)
_TIMESTAMP_WINDOW = 90


def merge_tracks(
    transcript_tracks: "list[Track]",
    fingerprinted_tracks: "list[FingerprintedTrack]",
) -> list[dict]:
    """Merge transcript and fingerprinted tracks into a single sorted list.

    Returns a list of plain dicts ready for enrichment / reporting.
    Each dict has an additional ``detection_source`` key:
      - ``"fingerprint"``        — confirmed by audio fingerprinting
      - ``"transcript"``         — extracted from speech only
      - ``"fingerprint+transcript"`` — confirmed by both methods
    """
    # Convert transcript tracks to dicts for easier manipulation
    transcript_dicts = [t.model_dump() for t in transcript_tracks]
    _parse_timestamps(transcript_dicts)

    used_transcript_indices: set[int] = set()
    merged: list[dict] = []

    for fp in fingerprinted_tracks:
        entry = _fingerprint_to_dict(fp)
        # Try to find a matching transcript track
        match_idx, match = _find_transcript_match(fp, transcript_dicts)
        if match is not None:
            used_transcript_indices.add(match_idx)
            # Merge supplementary fields from transcript into fingerprint entry
            entry["detection_source"] = "fingerprint+transcript"
            entry["label"] = entry["label"] or match.get("label", "")
            entry["remix"] = entry.get("remix", "") or match.get("remix", "")
            entry["extra_info"] = entry.get("extra_info", "") or match.get("extra_info", "")
        merged.append(entry)

    # Append transcript-only tracks that weren't matched
    for i, t in enumerate(transcript_dicts):
        if i not in used_transcript_indices:
            t["detection_source"] = "transcript"
            t.setdefault("album", "")
            t.setdefault("release_date", "")
            t.setdefault("score", 0)
            merged.append(t)

    # Sort by timestamp (seconds), putting entries with no timestamp last
    merged.sort(key=lambda x: x.get("_timestamp_sec", float("inf")))

    # Re-index and clean up internal keys
    for i, entry in enumerate(merged, start=1):
        entry["index"] = i
        entry.pop("_timestamp_sec", None)

    return merged


# ── helpers ───────────────────────────────────────────────────────────────────

def _fingerprint_to_dict(fp: "FingerprintedTrack") -> dict:
    return {
        "index": 0,  # will be reassigned after merge
        "timestamp": fp.timestamp_str,
        "_timestamp_sec": fp.timestamp,
        "artist": fp.artist,
        "title": fp.title,
        "label": fp.label,
        "remix": "",
        "extra_info": f"album: {fp.album}" if fp.album else "",
        "release_date": fp.release_date,
        "album": fp.album,
        "score": fp.score,
        "confidence": round(fp.score / 100, 2) if fp.score else 1.0,
        "detection_source": fp.detection_source,
        "links": dict(fp.links),
    }


def _parse_timestamps(tracks: list[dict]) -> None:
    """Add ``_timestamp_sec`` float to each transcript track dict."""
    for t in tracks:
        ts = t.get("timestamp", "") or ""
        t["_timestamp_sec"] = _ts_to_seconds(ts) if ts else float("inf")


def _ts_to_seconds(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS string to float seconds."""
    parts = [int(p) for p in re.split(r":", ts) if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return float("inf")


def _find_transcript_match(
    fp: "FingerprintedTrack",
    transcript_dicts: list[dict],
) -> tuple[int, dict | None]:
    """Return (index, dict) of the best matching transcript entry, or (-1, None)."""
    fp_artist = _normalise(fp.artist)
    fp_title = _normalise(fp.title)

    best_idx = -1
    best_score = 0

    for i, t in enumerate(transcript_dicts):
        t_artist = _normalise(t.get("artist", ""))
        t_title = _normalise(t.get("title", ""))

        # Fuzzy match: check overlap of significant words
        artist_score = _word_overlap(fp_artist, t_artist)
        title_score = _word_overlap(fp_title, t_title)
        score = artist_score * 0.4 + title_score * 0.6

        if score < 0.5:
            continue

        # Timestamp proximity bonus (only if transcript has a timestamp)
        t_sec = t.get("_timestamp_sec", float("inf"))
        if t_sec != float("inf"):
            diff = abs(fp.timestamp - t_sec)
            if diff > _TIMESTAMP_WINDOW:
                continue  # too far apart, skip regardless of name match

        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx >= 0:
        return best_idx, transcript_dicts[best_idx]
    return -1, None


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_overlap(a: str, b: str) -> float:
    """Jaccard-like overlap of word sets, ignoring very short words."""
    stop = {"the", "a", "an", "of", "ft", "feat", "featuring", "vs", "and", "&"}
    words_a = {w for w in a.split() if len(w) > 1 and w not in stop}
    words_b = {w for w in b.split() if len(w) > 1 and w not in stop}
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
