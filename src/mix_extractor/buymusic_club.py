"""buymusic.club integration — publish a mix tracklist as a BMC list.

API summary (reverse-engineered from site JS):
  POST /api/login              {username, password}            → sets session cookie
  GET  /api/authUser                                           → {id, username, ...}
  GET  /api/bandcamp?url=<url>                                 → item data (artist, title, ...)
  POST /api/lists              {title, description, body, name,
                                url, draft, ListItems:[...]}   → {slug, token, ...}
  PUT  /api/list/{slug}/edit/{token}                           → update existing list
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from rich.console import Console

if TYPE_CHECKING:
    from mix_extractor.config import Settings

console = Console()

_BASE_URL = "https://buymusic.club"
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "mix-extractor/1.0",
}
_RESOLVE_DELAY = 0.5  # seconds between /api/bandcamp calls


class BuymusicClubError(Exception):
    """Raised when the buymusic.club API returns an error."""


class BuymusicClubClient:
    """Thin requests-based client for the buymusic.club API."""

    def __init__(self) -> None:
        import requests  # noqa: PLC0415

        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._username: str = ""

    # ── auth ──────────────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> None:
        """Authenticate and persist the session cookie."""
        resp = self._session.post(
            f"{_BASE_URL}/api/login",
            json={"username": username, "password": password},
            timeout=20,
        )
        if not resp.ok:
            _raise(resp, "Login failed")
        # Fetch the authenticated user to confirm and store username
        me = self._get_auth_user()
        self._username = me.get("username", username)
        console.print(f"  [green]Logged in[/green] as [bold]{self._username}[/bold]")

    def _get_auth_user(self) -> dict:
        resp = self._session.get(f"{_BASE_URL}/api/authUser", timeout=15)
        if not resp.ok:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    # ── Bandcamp resolve ──────────────────────────────────────────────────────

    def resolve_bandcamp_url(self, url: str) -> dict | None:
        """Resolve a direct Bandcamp track/album URL into item metadata.

        Returns a dict compatible with the BMC ListItems schema, or None on failure.
        """
        try:
            resp = self._session.get(
                f"{_BASE_URL}/api/bandcamp",
                params={"url": url},
                timeout=25,
            )
            if not resp.ok:
                return None
            data = resp.json()
            # API returns either an item dict directly, or {error: ...}
            if isinstance(data, dict) and data.get("error"):
                return None
            return data
        except Exception as exc:
            console.print(f"    [dim yellow]Could not resolve {url}: {exc}[/dim yellow]")
            return None

    # ── list CRUD ─────────────────────────────────────────────────────────────

    def create_list(
        self,
        title: str,
        description: str = "",
        items: list[dict] | None = None,
    ) -> dict:
        """Create a new published list on buymusic.club.

        Returns the API response dict containing ``slug`` and ``token``.
        """
        payload = {
            "title": title,
            "description": description,
            "body": "",
            "name": self._username,
            "url": "",
            "draft": False,
            "ListItems": items or [],
        }
        resp = self._session.post(
            f"{_BASE_URL}/api/lists",
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            _raise(resp, "Failed to create list")
        data = resp.json()
        if data.get("error"):
            raise BuymusicClubError(f"API error creating list: {data['error']}")
        return data

    def update_list(self, slug: str, token: str, list_data: dict) -> dict:
        """Update an existing list (e.g. to add items after creation)."""
        payload = {**list_data, "token": token, "draft": False}
        resp = self._session.put(
            f"{_BASE_URL}/api/list/{slug}/edit/{token}",
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            _raise(resp, f"Failed to update list {slug}")
        return resp.json()


# ── top-level publish function ─────────────────────────────────────────────────


def publish_mix(
    mix_name: str,
    settings: "Settings",
    list_title: str | None = None,
    include_all: bool = False,
) -> str:
    """Publish a mix tracklist as a buymusic.club list.

    Args:
        mix_name:    The mix output directory name (e.g. "Fracture - 14 January 2026").
        settings:    App Settings (must have buymusic_club_username/password).
        list_title:  Override the list title (defaults to *mix_name*).
        include_all: If True, include all tracks regardless of keep flag.

    Returns:
        The public URL of the newly created list.
    """
    import json as _json  # noqa: PLC0415

    if not settings.buymusic_club_username or not settings.buymusic_club_password:
        raise BuymusicClubError(
            "Missing credentials. Set BUYMUSIC_CLUB_USERNAME and "
            "BUYMUSIC_CLUB_PASSWORD in your .env file."
        )

    mix_dir = settings.output_dir / mix_name
    tracks_file = mix_dir / "tracks.json"
    user_data_file = mix_dir / "user_data.json"

    if not tracks_file.exists():
        raise BuymusicClubError(f"No tracks.json found for mix: {mix_name}")

    raw = _json.loads(tracks_file.read_text(encoding="utf-8"))
    tracks: list[dict] = raw.get("tracks", [])

    user_data: dict = {}
    if user_data_file.exists():
        try:
            user_data = _json.loads(user_data_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Apply user overrides to get the current state of each track
    tracks = _apply_user_data(tracks, user_data)

    # Filter tracks
    has_keep_data = any(t.get("keep") for t in tracks)
    if not include_all and has_keep_data:
        tracks = [t for t in tracks if t.get("keep")]
        console.print(f"  Publishing [bold]{len(tracks)}[/bold] kept tracks")
    else:
        console.print(f"  Publishing all [bold]{len(tracks)}[/bold] tracks")

    if not tracks:
        raise BuymusicClubError("No tracks to publish. Mark some tracks as 'keep' first, or use --all.")

    # Authenticate
    client = BuymusicClubClient()
    client.login(settings.buymusic_club_username, settings.buymusic_club_password)

    # Resolve Bandcamp URLs
    console.print("  Resolving Bandcamp URLs …")
    items: list[dict] = []
    for i, track in enumerate(tracks):
        bc_url = _direct_bandcamp_url(track)
        if not bc_url:
            console.print(
                f"  [dim]  [{track.get('index', i+1)}] "
                f"{track.get('artist','?')} — {track.get('title','?')} "
                f"(no direct Bandcamp link, skipping)[/dim]"
            )
            continue

        console.print(
            f"    [{i+1}/{len(tracks)}] "
            f"[dim]{track.get('artist','?')} — {track.get('title','?')}[/dim] "
            f"[dim blue]{bc_url}[/dim blue]"
        )
        item = client.resolve_bandcamp_url(bc_url)
        if item:
            item["order"] = len(items)
            items.append(item)
        else:
            console.print(f"    [yellow]  Could not resolve, skipping[/yellow]")

        time.sleep(_RESOLVE_DELAY)

    if not items:
        raise BuymusicClubError(
            "No tracks could be resolved via Bandcamp. "
            "Add direct Bandcamp track URLs using the link editor in the web UI first."
        )

    console.print(f"  Resolved [bold]{len(items)}[/bold] of {len(tracks)} tracks")

    # Create the list
    title = list_title or mix_name
    console.print(f"  Creating list: [bold]{title}[/bold] …")
    result = client.create_list(title=title, items=items)

    slug = result.get("slug", "")
    list_url = f"https://www.buymusic.club/list/{slug}"
    console.print(f"  [green]Published![/green] {list_url}")

    # Persist the URL in user_data.json
    user_data["buymusic_club_url"] = list_url
    user_data_file.write_text(
        _json.dumps(user_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return list_url


# ── helpers ───────────────────────────────────────────────────────────────────


def _direct_bandcamp_url(track: dict) -> str | None:
    """Return a direct Bandcamp track URL from a track dict, or None if only a search URL."""
    url = track.get("links", {}).get("bandcamp", "")
    if not url:
        return None
    # Search URLs contain 'bandcamp.com/search' — skip those
    if "bandcamp.com/search" in url:
        return None
    return url


def _apply_user_data(tracks: list[dict], user_data: dict) -> list[dict]:
    """Apply user overrides (artist/title/link_overrides) to track list."""
    result = []
    for track in tracks:
        t = dict(track)
        tid = str(t.get("index", 0))
        td = user_data.get("tracks", {}).get(tid, {})

        t["keep"] = td.get("keep", t.get("keep", False))
        t["genre"] = td.get("genre", t.get("genre", ""))

        for field, user_val in td.get("overrides", {}).items():
            if user_val:
                t[field] = user_val

        # Apply link overrides
        links = dict(t.get("links", {}))
        for service, url in td.get("link_overrides", {}).items():
            if url:
                links[service] = url
        t["links"] = links

        result.append(t)
    return result


def _raise(resp: "requests.Response", context: str) -> None:  # type: ignore[name-defined]
    try:
        body = resp.json()
        msg = body.get("error", {})
        if isinstance(msg, dict):
            msg = msg.get("message", str(body))
    except Exception:
        msg = resp.text[:200]
    raise BuymusicClubError(f"{context} (HTTP {resp.status_code}): {msg}")
