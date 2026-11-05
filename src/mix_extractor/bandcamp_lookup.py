"""Find a direct Bandcamp link for a track via MusicBrainz.

Bandcamp's own search page (bandcamp.com/search) sits behind a JavaScript
"Client Challenge" and can't be used by automated clients, so instead we:

1. Find the recording on MusicBrainz (or reuse a known recording MBID).
2. Look for Bandcamp URLs attached to the recording, then the artist, then
   the recording's releases, then their label (cheapest/most likely first —
   MusicBrainz allows 1 request per second and is often slower than that).
3. Open the Bandcamp album / artist / label page (these aren't challenged)
   and match the track title to get a direct ``/track/`` link.

Returns the best link found: a track link if possible, otherwise an album link.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from rich.console import Console

console = Console()

_MB_MIN_SCORE = 90  # MusicBrainz search score (0–100) needed to trust a match
_MAX_RELEASES = 3  # releases of the recording to inspect for Bandcamp URLs
_USER_AGENT = "mix-extractor/0.1.0 (track enrichment)"

_MB_RECORDING_URL = re.compile(r"musicbrainz\.org/recording/([0-9a-f-]{36})")
_TRALBUM = re.compile(r'data-tralbum="([^"]+)"')
_CLIENT_ITEMS = re.compile(r'data-client-items="([^"]+)"')
_GRID_ITEM = re.compile(
    r'<a href="([^"]*/(?:album|track)/[^"]+)">.*?<p class="title">(.*?)</p>',
    re.DOTALL,
)
_VERSION_SUFFIX = re.compile(r"\s*[\(\[](original|extended|album)( mix| version)?[\)\]]\s*$", re.I)


# ── helpers ──────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Normalize a title for comparison: case, accents, punctuation, '&'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _VERSION_SUFFIX.sub("", s.lower().replace("&", " and "))
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def _titles_match(a: str, b: str) -> bool:
    return bool(_norm(a)) and _norm(a) == _norm(b)


def _artist_matches(wanted: str, credited: str) -> bool:
    """Whole-word containment either way: 'Call Super' ~ 'Call Super feat. X',
    but 'Lone' doesn't match 'Malone'."""
    w, c = _norm(wanted), _norm(credited)
    if not (w and c):
        return False
    return f" {w} " in f" {c} " or f" {c} " in f" {w} "


def _credited_artist(rec: dict) -> str:
    return rec.get("artist-credit-phrase") or " ".join(
        a["artist"]["name"] for a in rec.get("artist-credit", []) or [] if isinstance(a, dict)
    )


def _lucene_quote(s: str) -> str:
    """Quote a value for a MusicBrainz (Lucene) phrase query."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_bandcamp_host(netloc: str) -> bool:
    host = netloc.lower().split(":")[0]
    return host == "bandcamp.com" or host.endswith(".bandcamp.com")


def _bandcamp_urls(entity: dict) -> list[str]:
    """Bandcamp URLs from a MusicBrainz entity's url-relation-list."""
    urls = []
    for rel in entity.get("url-relation-list", []) or []:
        target = rel.get("target", "")
        is_bc = rel.get("type") == "bandcamp" or _is_bandcamp_host(urlparse(target).netloc)
        if is_bc and target.startswith("http") and target not in urls:
            urls.append(target)
    return urls


def _is_track(url: str) -> bool:
    return "/track/" in url


def _is_album(url: str) -> bool:
    return "/album/" in url


def _fetch_page(url: str) -> str | None:
    """GET a Bandcamp page; None on failure, redirect loop, or challenge page."""
    from mix_extractor.enricher import _http_get_with_retry  # noqa: PLC0415

    resp = _http_get_with_retry(url, headers={"User-Agent": _USER_AGENT}, max_attempts=2)
    if resp is None:
        return None
    text = resp.text
    if "<title>Client Challenge</title>" in text:
        console.print(f"    [dim]Bandcamp blocked automated access to {url}[/dim]")
        return None
    return text


def _track_from_album_page(page: str, base_url: str, title: str) -> str | None:
    """Find the ``/track/`` link for ``title`` in an album page's tracklist."""
    m = _TRALBUM.search(page)
    if not m:
        return None
    try:
        data = json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None
    for t in data.get("trackinfo", []) or []:
        if t.get("title_link") and _titles_match(t.get("title", ""), title):
            return urljoin(base_url, t["title_link"])
    return None


def _releases_on_music_page(page: str, base_url: str) -> list[tuple[str, str]]:
    """(title, absolute url) for each release on an artist/label ``/music`` page."""
    items: list[tuple[str, str]] = []
    for href, raw_title in _GRID_ITEM.findall(page):
        # Label pages add "<br><span class="artist-override">Artist</span>"
        name = re.sub(r"<[^>]+>", "", re.split(r"<br\s*/?>", raw_title)[0])
        items.append((html.unescape(name).strip(), urljoin(base_url, href)))
    # Large catalogs put the overflow in a JSON attribute instead of markup
    m = _CLIENT_ITEMS.search(page)
    if m:
        try:
            for it in json.loads(html.unescape(m.group(1))):
                if it.get("page_url") and it.get("title"):
                    items.append((it["title"], urljoin(base_url, it["page_url"])))
        except ValueError:
            pass
    return items


# ── Bandcamp page resolution ─────────────────────────────────────────────────

def _resolve_release_url(url: str, title: str) -> str:
    """Turn a Bandcamp album URL into the track URL when the title matches."""
    if _is_track(url):
        return url
    page = _fetch_page(url)
    if page:
        track = _track_from_album_page(page, url, title)
        if track:
            return track
    return url


def _search_music_page(root: str, title: str, release_titles: list[str]) -> str | None:
    """Find the track (or its album) on an artist/label Bandcamp page."""
    base = f"{urlparse(root).scheme or 'https'}://{urlparse(root).netloc}"
    page = _fetch_page(f"{base}/music")
    if not page:
        return None
    releases = _releases_on_music_page(page, base)

    # A standalone track release with the same title
    for name, url in releases:
        if _is_track(url) and _titles_match(name, title):
            return url
    # An album whose title matches one of the recording's MusicBrainz releases
    for name, url in releases:
        if _is_album(url) and any(_titles_match(name, rt) for rt in release_titles):
            return _resolve_release_url(url, title)
    return None


# ── MusicBrainz ──────────────────────────────────────────────────────────────

def _mb():
    import musicbrainzngs as mb  # noqa: PLC0415

    mb.set_useragent("mix-extractor", "0.1.0", "https://github.com/geoff-maddock/mix-extractor")
    return mb


def recording_id_from_links(links: dict) -> str | None:
    """Extract a recording MBID from a ``links['musicbrainz']`` URL."""
    m = _MB_RECORDING_URL.search((links or {}).get("musicbrainz", "") or "")
    return m.group(1) if m else None


def _find_recording_id(mb, artist: str, title: str) -> tuple[str | None, list[str]]:
    """Best matching recording ID, plus the release titles of *every* matching
    candidate.  MusicBrainz often has several equally-scored recordings of the
    same track (single, album, compilation) in no fixed order; pooling their
    release titles lets the artist-page match work whichever one comes first."""
    query = f"artist:{_lucene_quote(artist)} AND recording:{_lucene_quote(title)}"
    result = mb.search_recordings(query=query, limit=10)
    best: str | None = None
    titles: list[str] = []
    for rec in result.get("recording-list", []):
        if int(rec.get("ext:score", 0)) < _MB_MIN_SCORE or not _recording_matches(rec, artist, title):
            continue
        best = best or rec["id"]
        for rel in rec.get("release-list", []) or []:
            if rel.get("title") and rel["title"] not in titles:
                titles.append(rel["title"])
    return best, titles


def _recording_matches(rec: dict, artist: str, title: str) -> bool:
    return _artist_matches(artist, _credited_artist(rec)) and _titles_match(rec.get("title", ""), title)


def find_bandcamp_url(
    artist: str, title: str, recording_id: str | None = None, *, search: bool = True
) -> str | None:
    """Return a direct Bandcamp track (preferred) or album URL, or None.

    ``recording_id`` is a known MusicBrainz recording to start from; it's still
    checked against ``artist``/``title`` because callers may pass an unvetted
    first search hit.  ``search=False`` skips the MusicBrainz search when no
    ``recording_id`` is given (the caller already searched and found nothing).
    """
    if not (artist and title):
        return None
    mb = _mb()
    album_fallback: str | None = None

    def consider(url: str | None) -> str | None:
        """Track URL → done.  Album URL → remember as fallback."""
        nonlocal album_fallback
        if not url:
            return None
        if _is_track(url):
            return url
        album_fallback = album_fallback or url
        return None

    try:
        includes = ["url-rels", "releases", "artists"]
        rec = None
        extra_titles: list[str] = []
        if recording_id:
            rec = mb.get_recording_by_id(recording_id, includes=includes)["recording"]
            if not _recording_matches(rec, artist, title):
                rec, search = None, True  # wrong match — search properly instead
        if rec is None:
            rid, extra_titles = _find_recording_id(mb, artist, title) if search else (None, [])
            if not rid or rid == recording_id:
                return None
            rec = mb.get_recording_by_id(rid, includes=includes)["recording"]

        for url in _bandcamp_urls(rec):
            if found := consider(_resolve_release_url(url, title)):
                return found

        releases = rec.get("release-list", []) or []
        release_titles = [r.get("title", "") for r in releases]
        release_titles += [t for t in extra_titles if t not in release_titles]

        # 1. The primary artist's Bandcamp page — best coverage for one MB call
        for credit in rec.get("artist-credit", []) or []:
            if isinstance(credit, dict) and credit.get("artist", {}).get("id"):
                artist_ent = mb.get_artist_by_id(credit["artist"]["id"], includes=["url-rels"])["artist"]
                for root in _bandcamp_urls(artist_ent):
                    if found := consider(_search_music_page(root, title, release_titles)):
                        return found
                break
        if album_fallback:
            return album_fallback

        # 2. Bandcamp URLs attached to the recording's releases
        label_ids: list[str] = []
        for release in releases[:_MAX_RELEASES]:
            full = mb.get_release_by_id(release["id"], includes=["url-rels", "labels"])["release"]
            for info in full.get("label-info-list", []) or []:
                lid = (info.get("label") or {}).get("id")
                if lid and lid not in label_ids:
                    label_ids.append(lid)
            for url in _bandcamp_urls(full):
                if found := consider(_resolve_release_url(url, title)):
                    return found
            if album_fallback:
                return album_fallback  # its Bandcamp page didn't list the track by name

        # 3. The label's Bandcamp page
        if label_ids:
            label = mb.get_label_by_id(label_ids[0], includes=["url-rels"])["label"]
            for root in _bandcamp_urls(label):
                if found := consider(_search_music_page(root, title, release_titles)):
                    return found
    except Exception as exc:  # noqa: BLE001
        console.print(f"    [dim]Bandcamp/MusicBrainz lookup error: {exc}[/dim]")

    return album_fallback
