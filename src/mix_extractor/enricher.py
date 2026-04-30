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

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from rich.console import Console

if TYPE_CHECKING:
    import httpx

    from mix_extractor.config import Settings
    from mix_extractor.parser import Track

console = Console()

_MB_DELAY = 1.1  # MusicBrainz rate limit: 1 request per second

# ── per-host throttling and HTTP retry helper ────────────────────────────────
# Minimum interval between requests to the same host.  Used to be polite
# against rate-limited public endpoints.
_MIN_INTERVAL: dict[str, float] = {
    "bandcamp.com": 0.4,
    "api.deezer.com": 0.2,
    "www.beatport.com": 0.5,
}
_LAST_REQUEST: dict[str, float] = defaultdict(float)


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, 0)
    if interval <= 0:
        return
    elapsed = time.monotonic() - _LAST_REQUEST[host]
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _LAST_REQUEST[host] = time.monotonic()


def _http_get_with_retry(
    url: str,
    *,
    timeout: float = 10.0,
    headers: dict | None = None,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
) -> "httpx.Response | None":
    """GET with exponential backoff on 5xx / 429 / network errors.

    Returns the successful Response or None when every attempt failed.  Honors
    Retry-After on 429.  4xx (other than 429) is treated as non-retriable.
    """
    import httpx  # noqa: PLC0415

    host = urlparse(url).netloc
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        _throttle(host)
        try:
            resp = httpx.get(
                url, timeout=timeout, follow_redirects=True, headers=headers or {}
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.WriteError,
        ) as exc:
            last_error = exc
            time.sleep(backoff_base * (2 ** attempt))
            continue

        if resp.status_code == 429:
            try:
                retry_after = float(resp.headers.get("Retry-After", "1"))
            except ValueError:
                retry_after = 1.0
            time.sleep(min(retry_after, 10))
            continue
        if 500 <= resp.status_code < 600:
            last_error = httpx.HTTPStatusError(
                f"server error {resp.status_code}", request=resp.request, response=resp
            )
            time.sleep(backoff_base * (2 ** attempt))
            continue
        if resp.status_code >= 400:
            return None  # 4xx (other than 429) — caller treats as no result
        return resp

    if last_error is not None:
        console.print(f"    [dim]HTTP retry exhausted for {host}: {last_error}[/dim]")
    return None


class TrackLinks(dict):
    """Dict subclass for {source: url} links — keeps things simple."""


# ── (artist, title) enrichment cache ──────────────────────────────────────────
# Persistent on-disk cache keyed by normalized (artist, title).  Prevents
# repeated API calls when the same track is enriched again — most importantly
# when ``mix-extractor reprocess --no-transcribe`` runs across many mixes.

_CACHE_FILE = "enrich.json"


def _cache_path(settings: "Settings") -> Path:
    d = settings.output_dir / "_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / _CACHE_FILE


def _cache_key(artist: str, title: str) -> str:
    norm = lambda s: re.sub(r"[^\w\s]", "", (s or "").lower()).strip()  # noqa: E731
    return f"{norm(artist)}||{norm(title)}"


def _load_enrich_cache(settings: "Settings") -> dict:
    path = _cache_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_enrich_cache(settings: "Settings", cache: dict) -> None:
    try:
        _cache_path(settings).write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Cache write failed: {exc}[/dim]")


def _has_direct_link(links: dict) -> bool:
    """A cache entry is worth keeping when it has at least one non-search link."""
    for service, url in links.items():
        if service.startswith("_"):
            continue
        if not url:
            continue
        if "/search" in url or "/search?" in url:
            continue
        return True
    return False


def enrich(tracks: "list[Track] | list[dict]", settings: "Settings") -> list[dict]:
    """Enrich each track with purchase/stream links. Returns a list of dicts
    with the original track data plus a ``links`` key.

    Accepts either Pydantic ``Track`` objects or plain dicts (e.g. from the
    merger step).
    """
    from types import SimpleNamespace  # noqa: PLC0415

    cache = _load_enrich_cache(settings)
    cache_dirty = False
    cache_hits = 0

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

        cache_key = _cache_key(track.artist, track.title)
        cached = cache.get(cache_key)

        if cached and _has_direct_link(cached.get("links", {})):
            cache_hits += 1
            console.print("    [dim]↻ cache hit[/dim]")
            links = TrackLinks(cached.get("links", {}))
            entry = base
            entry["links"] = dict(links)
            if cached.get("genre_suggestion") and not entry.get("genre"):
                entry["genre_suggestion"] = cached["genre_suggestion"]
            results.append(entry)
            continue

        links = TrackLinks(existing_links)
        _lookup_musicbrainz(track, links)
        _lookup_bandcamp(track, links)
        _lookup_beatport(track, links)
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

        # Update cache when this lookup actually returned a useful link
        if _has_direct_link(entry["links"]):
            cache[cache_key] = {
                "links": dict(entry["links"]),
                "genre_suggestion": entry.get("genre_suggestion", ""),
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
            cache_dirty = True

        results.append(entry)

    if cache_dirty:
        _save_enrich_cache(settings, cache)
    if cache_hits:
        console.print(f"  [dim]Enrichment cache: {cache_hits} hit(s)[/dim]")

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

    query = f"{track.artist} {track.title}"
    search_url = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type=t"

    resp = _http_get_with_retry(
        search_url,
        headers={"User-Agent": "mix-extractor/0.1.0 (track enrichment)"},
    )
    if resp is None:
        links["bandcamp"] = search_url
        return

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


# ── Beatport ─────────────────────────────────────────────────────────────────

def _lookup_beatport(track: "Track", links: TrackLinks) -> None:
    """Search Beatport and resolve a direct track link when possible.

    Beatport has no public API, so this scrapes the search results page and
    extracts the first ``/track/<slug>/<id>`` link.  Falls back to the search
    URL if no match is found or the request fails.
    """
    from urllib.parse import quote_plus  # noqa: PLC0415

    query = f"{track.artist} {track.title}".strip()
    if not query:
        return
    search_url = f"https://www.beatport.com/search?q={quote_plus(query)}"

    resp = _http_get_with_retry(
        search_url,
        headers={"User-Agent": "mix-extractor/0.1.0 (track enrichment)"},
    )
    if resp is None:
        links["beatport"] = search_url
        return

    match = re.search(r'href="(/track/[^"]+/\d+)"', resp.text)
    if match:
        links["beatport"] = f"https://www.beatport.com{match.group(1)}"
        console.print("    [green]✓ Beatport direct link[/green]")
    else:
        links["beatport"] = search_url


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

    query = f"{track.artist} {track.title}"
    resp = _http_get_with_retry(
        f"https://api.deezer.com/search?q={quote_plus(query)}&limit=1"
    )
    if resp is None:
        return
    try:
        data = resp.json()
    except ValueError:
        return
    if data.get("data"):
        links["deezer"] = data["data"][0]["link"]


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
