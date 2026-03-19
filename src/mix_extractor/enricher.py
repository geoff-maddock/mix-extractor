"""Enricher — look up each track across music APIs to find links.

Active sources (no API key required):
  - MusicBrainz  (open metadata)
  - Bandcamp     (search URL, no key needed)
  - SoundCloud   (search URL, no key needed)
  - YouTube Music (ytmusicapi — no key needed)

Optional sources (require keys / packages):
  - Spotify      (SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)
  - Discogs      (DISCOGS_TOKEN + pip install discogs-client)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings
    from mix_extractor.parser import Track

console = Console()

_MB_DELAY = 1.1  # MusicBrainz rate limit: 1 request per second


class TrackLinks(dict):
    """Dict subclass for {source: url} links — keeps things simple."""


def enrich(tracks: "list[Track]", settings: "Settings") -> list[dict]:
    """Enrich each track with purchase/stream links. Returns a list of dicts
    with the original track data plus a ``links`` key."""
    results = []
    for track in tracks:
        console.print(
            f"  [dim][{track.index}][/dim] "
            f"[bold]{track.artist}[/bold] — {track.title}"
        )
        links = TrackLinks()
        _lookup_musicbrainz(track, links)
        _lookup_bandcamp(track, links)
        _lookup_soundcloud(track, links, settings)
        _lookup_youtube_music(track, links)
        if settings.spotify_client_id and settings.spotify_client_secret:
            _lookup_spotify(track, links, settings)
        if settings.discogs_token:
            _lookup_discogs(track, links, settings)

        entry = track.model_dump()
        entry["links"] = dict(links)
        results.append(entry)

    return results


# ── MusicBrainz ───────────────────────────────────────────────────────────────

def _lookup_musicbrainz(track: "Track", links: TrackLinks) -> None:
    try:
        import musicbrainzngs as mb  # noqa: PLC0415
    except ImportError:
        return

    mb.set_useragent("mix-extractor", "0.1.0", "https://github.com/your-org/mix-extractor")

    query = f'artist:"{track.artist}" AND recording:"{track.title}"'
    try:
        result = mb.search_recordings(query=query, limit=1)
        recordings = result.get("recording-list", [])
        if recordings:
            mbid = recordings[0]["id"]
            links["musicbrainz"] = f"https://musicbrainz.org/recording/{mbid}"
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]MusicBrainz error: {exc}[/dim]")
    finally:
        time.sleep(_MB_DELAY)


# ── Bandcamp ─────────────────────────────────────────────────────────────────

def _lookup_bandcamp(track: "Track", links: TrackLinks) -> None:
    """Construct a Bandcamp track search URL (no API key required)."""
    from urllib.parse import quote_plus  # noqa: PLC0415

    query = f"{track.artist} {track.title}"
    links["bandcamp"] = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type=t"


# ── SoundCloud ────────────────────────────────────────────────────────────────

def _lookup_soundcloud(track: "Track", links: TrackLinks, settings: "Settings") -> None:
    """Construct a SoundCloud track search URL (no API call required)."""
    from urllib.parse import quote_plus  # noqa: PLC0415

    query = f"{track.artist} {track.title}"
    links["soundcloud"] = f"https://soundcloud.com/search/tracks?q={quote_plus(query)}"


# ── YouTube Music ─────────────────────────────────────────────────────────────

def _lookup_youtube_music(track: "Track", links: TrackLinks) -> None:
    try:
        from ytmusicapi import YTMusic  # noqa: PLC0415
    except ImportError:
        return

    try:
        ytm = YTMusic()
        query = f"{track.artist} {track.title}"
        results = ytm.search(query, filter="songs", limit=1)
        if results:
            video_id = results[0].get("videoId") or ""
            if video_id:
                links["youtube_music"] = f"https://music.youtube.com/watch?v={video_id}"
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]YouTube Music error: {exc}[/dim]")


# ── Spotify ───────────────────────────────────────────────────────────────────

def _lookup_spotify(track: "Track", links: TrackLinks, settings: "Settings") -> None:
    try:
        import spotipy  # noqa: PLC0415
        from spotipy.oauth2 import SpotifyClientCredentials  # noqa: PLC0415
    except ImportError:
        return

    try:
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
            )
        )
        query = f"artist:{track.artist} track:{track.title}"
        results = sp.search(q=query, type="track", limit=1)
        items = results["tracks"]["items"]
        if items:
            links["spotify"] = items[0]["external_urls"]["spotify"]
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Spotify error: {exc}[/dim]")


# ── Discogs ───────────────────────────────────────────────────────────────────

def _lookup_discogs(track: "Track", links: TrackLinks, settings: "Settings") -> None:
    try:
        import discogs_client as discogs  # noqa: PLC0415
    except ImportError:
        return

    try:
        d = discogs.Client(
            "mix-extractor/0.1.0",
            user_token=settings.discogs_token,
        )
        results = d.search(f"{track.artist} {track.title}", type="release")
        page = results.page(1)
        if page:
            links["discogs"] = page[0].url
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Discogs error: {exc}[/dim]")
