Is there a way to try to retrigger the bandcamp lookup after the tracklist is extracted? If not, add one

Let me check what re-enrichment capabilities exist currently.

Now let me check the mix.html template to see how links are displayed and if there's already an edit-link feature:

There's no dedicated "re-lookup Bandcamp" feature currently. Here's what exists today:

Initial enrichment — _lookup_bandcamp() in enricher.py:115 runs once during analysis, fetching the Bandcamp search page and extracting direct track links.
CLI re-enrich — mix-extractor reprocess --no-transcribe re-runs the full enrichment pipeline (all services) on existing tracks, but it's CLI-only and processes every track across every service.
Manual link editing — The pencil icon next to each Bandcamp button lets you paste a URL manually via editLink() / the /api/track/{mix_name}/link endpoint.
Search URL regeneration — When you edit an artist/title in the web UI, the Bandcamp search URL is regenerated client-side, but it's just the search URL, not a resolved direct link.
What's missing: A targeted way to say "re-run the Bandcamp direct-link lookup for this track (or all tracks)" from the web UI without re-running the entire enrichment pipeline.

To add this, you'd need:

New API endpoint — e.g. POST /api/track/{mix_name}/lookup-bandcamp that accepts a track index, loads the track from tracks.json, calls _lookup_bandcamp(), and writes the resolved URL back to tracks.json (or user_data.json as a link override).

Button in the template — A small "resolve" icon next to the Bandcamp link in mix.html:270-283 that calls the new endpoint and updates the link in-place.

Optionally a bulk action — A "Re-lookup all Bandcamp links" button at the mix level that iterates all tracks.

Want me to switch to edit mode and implement this?