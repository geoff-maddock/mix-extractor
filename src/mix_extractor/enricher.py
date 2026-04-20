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
_BC_DELAY = 0.4  # Be polite to Bandcamp search


class TrackLinks(dict):
    """Dict subclass for {source: url} links — keeps things simple."""


def enrich(tracks: "list[Track] | list[dict]", settings: "Settings") -> list[dict]:
    """Enrich each track with purchase/stream links. Returns a list of dicts
    with the original track data plus a ``links`` key.

    Accepts either Pydantic ``Track`` objects or plain dicts (e.g. from the
    merger step).
    """
    from types import SimpleNamespace  # noqa: PLC0415

    results = []
    for raw_track in tracks:
        if isinstance(raw_track, dict):
            base = dict(raw_track)
            existing_links = base.pop("links", {})
            track = SimpleNamespace(**base)
        else:
            base = raw_track.model_dump()
            existing_links = base.pop("links", {})
            track = SimpleNamespace(**base)

        console.print(
            f"  [dim][{track.index}][/dim] "
            f"[bold]{track.artist}[/bold] — {track.title}"
        )
        links = TrackLinks(existing_links)
        _lookup_musicbrainz(track, links)
        _lookup_bandcamp(track, links)
        _lookup_soundcloud(track, links, settings)
        _lookup_youtube_music(track, links)
        _lookup_deezer(track, links)
        if settings.spotify_client_id and settings.spotify_client_secret:
            _lookup_spotify(track, links, settings)
        if settings.discogs_token:
            _lookup_discogs(track, links, settings)

        entry = base
        entry["links"] = dict(links)
        # Collect genre hints stored by lookup functions; strip temp keys
        genre_hints = []
        for key in list(entry["links"].keys()):
            if key.startswith("_genre_hints_"):
                genre_hints.extend(entry["links"].pop(key))
        if genre_hints and not entry.get("genre"):
            seen: set = set()
            unique = []
            for g in genre_hints:
                if g.lower() not in seen:
                    seen.add(g.lower())
                    unique.append(g)
            entry["genre_suggestion"] = ", ".join(unique[:3])
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
    """Search Bandcamp and resolve a direct track link when possible.

    Falls back to the search URL if no matching track is found or the
    request fails.
    """
    import re  # noqa: PLC0415
    from urllib.parse import quote_plus  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    query = f"{track.artist} {track.title}"
    search_url = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type=t"

    try:
        resp = httpx.get(
            search_url,
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "mix-extractor/0.1.0 (track enrichment)"},
        )
        resp.raise_for_status()
        # First track result in the search page contains a direct link
        match = re.search(
            r'class="searchresult[^"]*".*?'
            r'href="(https://[^"]*\.bandcamp\.com/track/[^?"]*)',
            resp.text,
            re.DOTALL,
        )
        if match:
            links["bandcamp"] = match.group(1)
            console.print("    [green]✓ Bandcamp direct link[/green]")
        else:
            links["bandcamp"] = search_url
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Bandcamp search error: {exc}[/dim]")
        links["bandcamp"] = search_url
    finally:
        time.sleep(_BC_DELAY)


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


# ── Deezer ────────────────────────────────────────────────────────────────

def _lookup_deezer(track: "Track", links: TrackLinks) -> None:
    """Search Deezer for a direct track link (free API, no key needed)."""
    from urllib.parse import quote_plus  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    query = f"{track.artist} {track.title}"
    try:
        resp = httpx.get(
            f"https://api.deezer.com/search?q={quote_plus(query)}&limit=1",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("data"):
            links["deezer"] = data["data"][0]["link"]
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Deezer error: {exc}[/dim]")


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
            # Fetch artist genres for auto-suggestion
            try:
                artist_id = items[0]["artists"][0]["id"]
                artist_obj = sp.artist(artist_id)
                genres = artist_obj.get("genres", [])
                if genres:
                    links["_genre_hints_spotify"] = genres[:4]
            except Exception:
                pass
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
            # Extract genres/styles for auto-suggestion
            try:
                rel = page[0]
                genres = list(getattr(rel, "genres", None) or [])
                styles = list(getattr(rel, "styles", None) or [])
                hints = genres + styles
                if hints:
                    links["_genre_hints_discogs"] = hints[:4]
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Discogs error: {exc}[/dim]")
