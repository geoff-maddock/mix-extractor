"""Tests for the web mutation endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mix_extractor.config import get_settings
from mix_extractor.web import app as web_app
from mix_extractor.web.app import app as fastapi_app


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path: Path):
    """Patch _settings to point at a temp directory and seed a mix on disk."""
    out_dir = tmp_path / "output"
    in_dir = tmp_path / "input"
    out_dir.mkdir()
    in_dir.mkdir()
    settings = get_settings(output_dir=out_dir, input_dir=in_dir)

    mix_name = "Sample Mix"
    mix_dir = out_dir / mix_name
    mix_dir.mkdir()
    seed = {
        "mix": {
            "source": f"{mix_name}.mp3",
            "duration_seconds": None,
            "transcription_provider": "test",
            "analyzed_at": "2026-01-01T00:00:00+00:00",
        },
        "tracks": [
            {
                "index": 1, "timestamp": "00:01:00", "artist": "Bicep", "title": "Glue",
                "label": "Feel My Bicep", "remix": "", "extra_info": "",
                "confidence": 1.0, "detection_source": "transcript", "links": {},
            },
            {
                "index": 2, "timestamp": "00:05:00", "artist": "Lone", "title": "Be Mine",
                "label": "R&S", "remix": "", "extra_info": "",
                "confidence": 1.0, "detection_source": "fingerprint", "links": {},
            },
            {
                "index": 3, "timestamp": "00:08:00", "artist": "Pessimist", "title": "Ekkomesa",
                "label": "", "remix": "", "extra_info": "",
                "confidence": 1.0, "detection_source": "transcript", "links": {},
            },
        ],
    }
    (mix_dir / "tracks.json").write_text(json.dumps(seed, indent=2))

    with patch.object(web_app, "_settings", return_value=settings):
        client = TestClient(fastapi_app)
        yield {
            "client": client,
            "settings": settings,
            "out_dir": out_dir,
            "in_dir": in_dir,
            "mix_name": mix_name,
            "mix_dir": mix_dir,
            "tracks_file": mix_dir / "tracks.json",
        }


def _read_tracks(env) -> dict:
    return json.loads(env["tracks_file"].read_text(encoding="utf-8"))


def _read_user_data(env) -> dict:
    p = env["mix_dir"] / "user_data.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ── delete_track ──────────────────────────────────────────────────────────────

class TestDeleteTrack:
    def test_removes_track_and_user_data(self, env):
        # seed user_data so we can verify it gets stripped
        (env["mix_dir"] / "user_data.json").write_text(json.dumps({
            "tracks": {
                "2": {"keep": True, "genre": "techno"},
                "3": {"keep": False},
            }
        }))
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/track/delete",
            json={"index": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["remaining"] == 2

        data = _read_tracks(env)
        indices = [t["index"] for t in data["tracks"]]
        assert indices == [1, 3]

        ud = _read_user_data(env)
        assert "2" not in ud["tracks"]
        assert "3" in ud["tracks"]  # untouched

    def test_404_on_missing_mix(self, env):
        r = env["client"].post("/api/mix/Nope/track/delete", json={"index": 1})
        assert r.status_code == 404

    def test_404_on_missing_track(self, env):
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/track/delete",
            json={"index": 999},
        )
        assert r.status_code == 404

    def test_400_when_index_missing(self, env):
        r = env["client"].post(f"/api/mix/{env['mix_name']}/track/delete", json={})
        assert r.status_code == 400


# ── add_blank_track ───────────────────────────────────────────────────────────

class TestAddBlankTrack:
    def test_appends_with_next_index(self, env):
        r = env["client"].post(f"/api/mix/{env['mix_name']}/track/add")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["track"]["index"] == 4
        assert body["track"]["detection_source"] == "manual"
        assert body["track"]["artist"] == ""
        assert body["track"]["title"] == ""

        data = _read_tracks(env)
        assert len(data["tracks"]) == 4
        assert data["tracks"][-1]["index"] == 4

    def test_index_after_delete_does_not_collide(self, env):
        # Delete the highest-index track, then add — new index should still be max+1
        env["client"].post(
            f"/api/mix/{env['mix_name']}/track/delete", json={"index": 3}
        )
        r = env["client"].post(f"/api/mix/{env['mix_name']}/track/add")
        body = r.json()
        # max remaining was 2, so new index = 3
        assert body["track"]["index"] == 3

    def test_404_on_missing_mix(self, env):
        r = env["client"].post("/api/mix/Nope/track/add")
        assert r.status_code == 404


# ── reorder_tracks ────────────────────────────────────────────────────────────

class TestReorderTracks:
    def test_rearranges_in_place(self, env):
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/tracks/reorder",
            json={"order": [3, 1, 2]},
        )
        assert r.status_code == 200
        data = _read_tracks(env)
        assert [t["index"] for t in data["tracks"]] == [3, 1, 2]
        # Track payloads still intact
        assert data["tracks"][0]["title"] == "Ekkomesa"

    def test_400_on_mismatched_order(self, env):
        # Missing one index
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/tracks/reorder",
            json={"order": [1, 2]},
        )
        assert r.status_code == 400
        # Extra index
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/tracks/reorder",
            json={"order": [1, 2, 3, 99]},
        )
        assert r.status_code == 400
        # Duplicate
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/tracks/reorder",
            json={"order": [1, 1, 2]},
        )
        assert r.status_code == 400

    def test_400_when_order_not_list(self, env):
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/tracks/reorder",
            json={"order": "not-a-list"},
        )
        assert r.status_code == 400


# ── set_mix_source_url ────────────────────────────────────────────────────────

class TestSetMixSourceUrl:
    def test_sets_and_clears(self, env):
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/source-url",
            json={"url": "https://soundcloud.com/foo/bar"},
        )
        assert r.status_code == 200
        assert _read_tracks(env)["mix"]["source_url"] == "https://soundcloud.com/foo/bar"

        # Clear
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/source-url", json={"url": ""}
        )
        assert r.status_code == 200
        assert "source_url" not in _read_tracks(env)["mix"]

    def test_rejects_non_http_url(self, env):
        r = env["client"].post(
            f"/api/mix/{env['mix_name']}/source-url",
            json={"url": "ftp://example.com/foo"},
        )
        assert r.status_code == 400

    def test_404_on_missing_mix(self, env):
        r = env["client"].post(
            "/api/mix/Nope/source-url", json={"url": "https://x.com"}
        )
        assert r.status_code == 404


# ── existing track-management endpoints (previously untested) ────────────────

class TestKeepFlag:
    def test_toggle_persists_to_user_data(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/keep",
            json={"index": 1, "keep": True},
        )
        assert r.status_code == 200
        assert r.json()["keep"] is True
        assert _read_user_data(env)["tracks"]["1"]["keep"] is True

        # Untoggle
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/keep",
            json={"index": 1, "keep": False},
        )
        assert r.json()["keep"] is False


class TestSetGenre:
    def test_genre_persists(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/genre",
            json={"index": 2, "genre": "techno"},
        )
        assert r.status_code == 200
        assert _read_user_data(env)["tracks"]["2"]["genre"] == "techno"


class TestEditField:
    def test_artist_override_recorded(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/edit",
            json={"index": 1, "field": "artist", "value": "Bicep (corrected)"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["original"] == "Bicep"
        assert body["value"] == "Bicep (corrected)"
        ud = _read_user_data(env)
        assert ud["tracks"]["1"]["overrides"]["artist"] == "Bicep (corrected)"

    def test_clearing_override_removes_field(self, env):
        # Set an override first
        env["client"].post(
            f"/api/track/{env['mix_name']}/edit",
            json={"index": 1, "field": "title", "value": "Glue (Edited)"},
        )
        # Clear it
        env["client"].post(
            f"/api/track/{env['mix_name']}/edit",
            json={"index": 1, "field": "title", "value": ""},
        )
        ud = _read_user_data(env)
        assert "title" not in ud["tracks"]["1"].get("overrides", {})

    def test_rejects_unknown_field(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/edit",
            json={"index": 1, "field": "year", "value": "2026"},
        )
        assert r.status_code == 400


class TestLinkOverride:
    def test_persists_url(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/link",
            json={
                "index": 1, "service": "bandcamp",
                "url": "https://bicep.bandcamp.com/track/glue",
            },
        )
        assert r.status_code == 200
        ud = _read_user_data(env)
        assert (
            ud["tracks"]["1"]["link_overrides"]["bandcamp"]
            == "https://bicep.bandcamp.com/track/glue"
        )

    def test_rejects_unknown_service(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/link",
            json={"index": 1, "service": "tidal", "url": "https://tidal.com/x"},
        )
        assert r.status_code == 400

    def test_rejects_non_http_url(self, env):
        r = env["client"].post(
            f"/api/track/{env['mix_name']}/link",
            json={"index": 1, "service": "spotify", "url": "spotify:track:abc"},
        )
        assert r.status_code == 400


# ── markdown regen side effect ────────────────────────────────────────────────

class TestMarkdownRegen:
    def test_delete_regenerates_report(self, env):
        # Seed a report.md with a transcript so we can verify it's preserved
        (env["mix_dir"] / "report.md").write_text(
            "# Tracklist Report: old\n\n## Tracklist\n\n| # | Time |\n|---|---|\n| 1 | x |\n\n"
            "---\n\n## Full Transcript\n\n**[00:00]** hello world\n"
        )
        env["client"].post(
            f"/api/mix/{env['mix_name']}/track/delete", json={"index": 1}
        )
        md = (env["mix_dir"] / "report.md").read_text()
        assert "Updated" in md
        assert "Lone" in md  # survivor track surfaced in new tracklist
        assert "hello world" in md  # transcript preserved
