"""Tests for the MusicBrainz-based Bandcamp lookup."""

from __future__ import annotations

import html
import json
from unittest.mock import MagicMock, patch

from mix_extractor import bandcamp_lookup as bl
from mix_extractor.enricher import TrackLinks, _lookup_bandcamp


def _album_page(tracks: list[tuple[str, str]]) -> str:
    data = {"trackinfo": [{"title": t, "title_link": link} for t, link in tracks]}
    return f'<div data-tralbum="{html.escape(json.dumps(data))}"></div>'


def _music_page(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<li><a href="{href}"><div class="art"></div>'
        f'<p class="title">\n  {title}\n</p></a></li>'
        for href, title in items
    )


def _mb_mock(*, recording=None, releases=None, artist=None, label=None, search=None):
    mb = MagicMock()
    mb.search_recordings.return_value = search or {"recording-list": []}
    mb.get_recording_by_id.return_value = {"recording": recording or {}}
    mb.get_release_by_id.side_effect = lambda rid, includes: {"release": (releases or {})[rid]}
    mb.get_artist_by_id.return_value = {"artist": artist or {}}
    mb.get_label_by_id.return_value = {"label": label or {}}
    return mb


def _url_rel(url: str) -> dict:
    return {"url-relation-list": [{"type": "bandcamp", "target": url}]}


RECORDING = {
    "id": "rec-1",
    "title": "Arpo Sunk",
    "artist-credit": [{"artist": {"id": "art-1", "name": "Call Super"}}],
    "release-list": [{"id": "rel-1", "title": "Arpo"}],
}


class TestTitleMatching:
    def test_ignores_case_punctuation_and_accents(self):
        assert bl._titles_match("Café del Mar!", "cafe del mar")

    def test_ignores_original_mix_suffix(self):
        assert bl._titles_match("Glue (Original Mix)", "Glue")

    def test_remix_does_not_match_original(self):
        assert not bl._titles_match("Ganzfeld (Djrum Remix)", "Ganzfeld")


class TestFindBandcampUrl:
    def test_release_album_resolved_to_track(self):
        mb = _mb_mock(
            recording=RECORDING,
            releases={"rel-1": _url_rel("https://callsuper.bandcamp.com/album/arpo")},
        )
        page = _album_page([("Arpo", "/track/arpo"), ("Arpo Sunk", "/track/arpo-sunk")])
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", return_value=page):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        assert url == "https://callsuper.bandcamp.com/track/arpo-sunk"
        mb.search_recordings.assert_not_called()  # reused the known MBID

    def test_album_link_kept_when_track_not_on_page(self):
        mb = _mb_mock(
            recording=RECORDING,
            releases={"rel-1": _url_rel("https://callsuper.bandcamp.com/album/arpo")},
        )
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", return_value=None):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        assert url == "https://callsuper.bandcamp.com/album/arpo"
        mb.get_label_by_id.assert_not_called()  # stopped once the album was found

    def test_falls_back_to_artist_music_page(self):
        mb = _mb_mock(recording=RECORDING, releases={"rel-1": {}},
                      artist=_url_rel("https://callsuper.bandcamp.com/"))
        pages = {
            "https://callsuper.bandcamp.com/music": _music_page([("/album/arpo", "Arpo")]),
            "https://callsuper.bandcamp.com/album/arpo": _album_page([("Arpo Sunk", "/track/arpo-sunk")]),
        }
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", side_effect=pages.get):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        assert url == "https://callsuper.bandcamp.com/track/arpo-sunk"

    def test_artist_page_checked_before_releases(self):
        mb = _mb_mock(recording=RECORDING, artist=_url_rel("https://callsuper.bandcamp.com/"))
        pages = {
            "https://callsuper.bandcamp.com/music": _music_page([("/album/arpo", "Arpo")]),
            "https://callsuper.bandcamp.com/album/arpo": _album_page([("Arpo Sunk", "/track/arpo-sunk")]),
        }
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", side_effect=pages.get):
            bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        mb.get_release_by_id.assert_not_called()

    def test_label_page_used_when_artist_has_none(self):
        rec = dict(RECORDING)
        releases = {"rel-1": {"label-info-list": [{"label": {"id": "lab-1"}}]}}
        mb = _mb_mock(recording=rec, releases=releases, artist={},
                      label=_url_rel("https://houndstooth.bandcamp.com/"))
        music = _music_page([
            ("https://callsuper.bandcamp.com/track/arpo-sunk",
             'Arpo Sunk<br><span class="artist-override">Call Super</span>'),
        ])
        with patch.object(bl, "_mb", return_value=mb), \
                patch.object(bl, "_fetch_page", side_effect=lambda u: music if u.endswith("/music") else None):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        assert url == "https://callsuper.bandcamp.com/track/arpo-sunk"

    def test_search_rejects_low_score_and_wrong_artist(self):
        search = {"recording-list": [
            {"id": "x", "ext:score": "70", "title": "Arpo Sunk", "artist-credit-phrase": "Call Super"},
            {"id": "y", "ext:score": "100", "title": "Arpo Sunk", "artist-credit-phrase": "Someone Else"},
        ]}
        mb = _mb_mock(search=search)
        with patch.object(bl, "_mb", return_value=mb):
            assert bl.find_bandcamp_url("Call Super", "Arpo Sunk") is None
        mb.get_recording_by_id.assert_not_called()

    def test_musicbrainz_error_returns_none(self):
        mb = MagicMock()
        mb.search_recordings.side_effect = RuntimeError("503")
        with patch.object(bl, "_mb", return_value=mb):
            assert bl.find_bandcamp_url("Call Super", "Arpo Sunk") is None


class TestLookupBandcamp:
    def test_sets_direct_link(self):
        track = MagicMock(artist="Call Super", title="Arpo Sunk")
        links = TrackLinks(musicbrainz="https://musicbrainz.org/recording/9d9b97fe-e8d3-41e3-a983-9d3ef09f0e6a")
        with patch("mix_extractor.bandcamp_lookup.find_bandcamp_url",
                   return_value="https://callsuper.bandcamp.com/track/arpo-sunk") as find:
            _lookup_bandcamp(track, links)
        assert links["bandcamp"] == "https://callsuper.bandcamp.com/track/arpo-sunk"
        assert find.call_args.args[2] == "9d9b97fe-e8d3-41e3-a983-9d3ef09f0e6a"

    def test_falls_back_to_search_url(self):
        track = MagicMock(artist="Call Super", title="Arpo Sunk")
        links = TrackLinks()
        with patch("mix_extractor.bandcamp_lookup.find_bandcamp_url", return_value=None):
            _lookup_bandcamp(track, links)
        assert links["bandcamp"] == "https://bandcamp.com/search?q=Call+Super+Arpo+Sunk&item_type=t"


class TestReviewFixes:
    def test_artist_match_is_whole_word(self):
        assert bl._artist_matches("Call Super", "Call Super feat. Julia Holter")
        assert not bl._artist_matches("Lone", "Malone")
        assert not bl._artist_matches("Lone", "Lonely Hearts")

    def test_query_escapes_quotes(self):
        assert bl._lucene_quote('The "Real" Thing') == '"The \\"Real\\" Thing"'
        mb = _mb_mock()
        with patch.object(bl, "_mb", return_value=mb):
            bl.find_bandcamp_url('A "B"', "C")
        assert 'artist:"A \\"B\\""' in mb.search_recordings.call_args.kwargs["query"]

    def test_bandcamp_url_filter(self):
        ent = {"url-relation-list": [
            {"type": "bandcamp", "target": "https://music.pearsonsound.com/"},
            {"type": "purchase for download", "target": "https://notbandcamp.com/album/x"},
            {"type": "free streaming", "target": "https://x.bandcamp.com/album/y"},
        ]}
        assert bl._bandcamp_urls(ent) == ["https://music.pearsonsound.com/", "https://x.bandcamp.com/album/y"]

    def test_mismatched_recording_id_falls_back_to_search(self):
        wrong = {"id": "wrong", "title": "Something Else",
                 "artist-credit": [{"artist": {"id": "a", "name": "Other"}}]}
        mb = _mb_mock(search={"recording-list": []})
        mb.get_recording_by_id.return_value = {"recording": wrong}
        with patch.object(bl, "_mb", return_value=mb):
            assert bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="wrong", search=False) is None
        mb.search_recordings.assert_called_once()  # searched despite search=False
        mb.get_artist_by_id.assert_not_called()  # never followed the wrong recording

    def test_search_false_skips_search_without_recording_id(self):
        mb = _mb_mock()
        with patch.object(bl, "_mb", return_value=mb):
            assert bl.find_bandcamp_url("Call Super", "Arpo Sunk", search=False) is None
        mb.search_recordings.assert_not_called()

    def test_recording_album_link_resolved_to_track(self):
        rec = dict(RECORDING, **_url_rel("https://callsuper.bandcamp.com/album/arpo"))
        mb = _mb_mock(recording=rec)
        page = _album_page([("Arpo Sunk", "/track/arpo-sunk")])
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", return_value=page):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk", recording_id="rec-1")
        assert url == "https://callsuper.bandcamp.com/track/arpo-sunk"

    def test_release_titles_pooled_across_matching_candidates(self):
        search = {"recording-list": [
            {"id": "rec-1", "ext:score": "100", "title": "Arpo Sunk", "artist-credit-phrase": "Call Super",
             "release-list": [{"title": "Some Compilation"}]},
            {"id": "rec-2", "ext:score": "100", "title": "Arpo Sunk", "artist-credit-phrase": "Call Super",
             "release-list": [{"title": "Arpo"}]},
        ]}
        rec = dict(RECORDING, **{"release-list": [{"id": "rel-c", "title": "Some Compilation"}]})
        mb = _mb_mock(search=search, recording=rec, releases={"rel-c": {}},
                      artist=_url_rel("https://callsuper.bandcamp.com/"))
        pages = {
            "https://callsuper.bandcamp.com/music": _music_page([("/album/arpo", "Arpo")]),
            "https://callsuper.bandcamp.com/album/arpo": _album_page([("Arpo Sunk", "/track/arpo-sunk")]),
        }
        with patch.object(bl, "_mb", return_value=mb), patch.object(bl, "_fetch_page", side_effect=pages.get):
            url = bl.find_bandcamp_url("Call Super", "Arpo Sunk")
        assert url == "https://callsuper.bandcamp.com/track/arpo-sunk"
