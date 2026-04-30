# buymusic.club Integration Plan

## Overview

[buymusic.club](https://www.buymusic.club/) is a site where DJs share curated lists of
Bandcamp links so fans can find and buy the music. The goal is to add a **publish**
command and web UI action that takes an extracted mix tracklist and posts it as a
buymusic.club list automatically.

---

## Credentials

Stored in `.env`:
```
BUYMUSIC_CLUB_USERNAME=...
BUYMUSIC_CLUB_PASSWORD=...
```

---

## Phase 1 — API Investigation (prerequisite)

Before writing any code, prove out the API surface. Two things to verify:

### 1a. The Bandcamp resolution endpoint

```
GET https://buymusic.club/api/bandcamp?url=<bandcamp_track_url>
```

Known to work for direct track/album URLs (e.g. `https://lobstertheremin.com/track/we-can-have-it-all`).
Need to confirm the response shape — expected to return something like:
```json
{
  "url": "...",
  "artist": "...",
  "title": "...",
  "image": "...",
  "purchase_url": "..."
}
```

Run this manually and log the actual response before writing the integration code.

### 1b. List creation / item addition endpoints

Use browser dev tools (Network tab) while:
1. Logging in to `https://buymusic.club/login`
2. Creating a new list at `https://buymusic.club/new`
3. Adding an item to the list

Capture the exact POST endpoints, request bodies, and authentication mechanism (cookie
session vs. JWT vs. CSRF token). Document findings before implementation.

---

## Phase 2 — Config

Add to `src/mix_extractor/config.py` (`Settings` class):

```python
buymusic_club_username: str = Field(default="")
buymusic_club_password: str = Field(default="")
```

Add the corresponding entries to the `_load_from_env` mapping:
```python
"buymusic_club_username": "BUYMUSIC_CLUB_USERNAME",
"buymusic_club_password": "BUYMUSIC_CLUB_PASSWORD",
```

---

## Phase 3 — `buymusic_club.py` module

Create `src/mix_extractor/buymusic_club.py` with:

### `BuymusicClubClient`

A `requests.Session`-based client that handles:

- **`login(username, password)`** — POST to the login endpoint, persist session cookie.
- **`resolve_bandcamp_url(url) -> dict | None`** — GET `/api/bandcamp?url=<url>`, return
  parsed item or `None` on failure.
- **`create_list(title, description="") -> str`** — POST to create a new list, return
  the new list slug/URL.
- **`add_item(list_id, bandcamp_url) -> bool`** — POST a resolved Bandcamp item to
  the list.

### `publish_mix(mix_name, list_title, settings) -> str`

Top-level function that orchestrates the full flow:

1. Load `tracks.json` for the mix (and `user_data.json` for overrides/keep flags).
2. Filter to tracks where `keep=True` **or** all tracks if no keep data exists.
3. For each kept track, find a usable Bandcamp URL:
   - Prefer `links.bandcamp` if it is a **direct track URL** (not a search URL — i.e.
     does not contain `bandcamp.com/search`).
   - Skip if no direct Bandcamp link is available.
4. Authenticate and call `create_list(list_title)`.
5. For each resolved Bandcamp URL, call `resolve_bandcamp_url()` then `add_item()`.
6. Return the published list URL.
7. Persist the URL in `user_data.json` under `"buymusic_club_url"`.

---

## Phase 4 — CLI command

Add a `publish` subcommand to `src/mix_extractor/cli.py`:

```
mix-extractor publish <mix_name> [--title "List title"] [--all]
```

Options:
- `--title` — set the list name (defaults to the mix name).
- `--all` — include all tracks, not just `keep=True` ones.

Prints the resulting buymusic.club URL on success.

---

## Phase 5 — Web UI

On the mix detail page (`/mix/<mix_name>`):

1. Add a **"Publish to buymusic.club"** button (only shown when credentials are
   configured in `.env`).
2. POST to a new endpoint `/api/mix/{mix_name}/publish` which:
   - Calls `publish_mix()` in a background task (same pattern as `/analyze`).
   - Returns the list URL once done.
3. Show the returned URL as a clickable link, and persist it to `user_data.json`
   so it remains visible on subsequent page loads.

---

## Phase 6 — Enricher integration (optional, later)

Once the Bandcamp resolution endpoint response shape is confirmed, consider using
`/api/bandcamp?url=...` inside the enricher's `_lookup_bandcamp` function to upgrade
search-URL links to direct purchase links when a known Bandcamp URL already exists.

---

## Key decisions / open questions

| Question | Notes |
|---|---|
| Auth mechanism | Likely cookie session — confirm via browser inspection |
| Does the API require a CSRF token? | Check response headers on login |
| Rate limiting on `/api/bandcamp`? | Add a small delay between calls to be safe |
| What if a track has no Bandcamp link? | Skip silently, report count at end |
| Should we update an existing list or always create a new one? | Start with always-create; add update later |

---

## File changes summary

| File | Change |
|---|---|
| `src/mix_extractor/config.py` | Add `buymusic_club_username` / `buymusic_club_password` fields |
| `src/mix_extractor/buymusic_club.py` | New module — client + `publish_mix()` |
| `src/mix_extractor/cli.py` | Add `publish` subcommand |
| `src/mix_extractor/web/app.py` | Add `/api/mix/{mix_name}/publish` endpoint |
| `src/mix_extractor/web/templates/mix.html` | Add "Publish" button + result display |
