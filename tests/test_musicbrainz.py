from __future__ import annotations

import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import musicbrainz, storage


class MusicBrainzTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.db_patch = patch.object(storage, "DB_PATH", self.db)
        self.db_patch.start()
        storage.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp.cleanup()

    def test_configuration_has_meaningful_user_agent(self) -> None:
        config = musicbrainz.configuration()
        self.assertEqual(config["authentication"], "public read-only")
        self.assertIn("WhatsNewChecker/", config["user_agent"])
        self.assertIn("gavrevns@gmail.com", config["user_agent"])
        self.assertEqual(config["rate_limit"], "1 request/sec")

    def test_album_details_maps_release_group_and_selected_release(self) -> None:
        group = {
            "id":"rg-1", "title":"Wall of Eyes", "first-release-date":"2024-01-26",
            "primary-type":"Album", "secondary-types":[],
            "artist-credit":[{"name":"The Smile","artist":{"id":"artist-1","name":"The Smile","sort-name":"Smile, The"}}],
            "genres":[{"name":"art rock","count":4}], "tags":[{"name":"experimental","count":2}],
            "rating":{"value":4.2,"votes-count":12},
            "releases":[{"id":"release-1","date":"2024-01-26","status":"Official"}],
        }
        release = {
            "id":"release-1", "title":"Wall of Eyes", "status":"Official", "country":"GB",
            "barcode":"191404138393", "label-info":[{"catalog-number":"XL1383CD","label":{"name":"XL Recordings"}}],
            "media":[{"format":"CD","track-count":8,"tracks":[{"number":"1","title":"Wall of Eyes","length":305000}]}],
        }
        with patch.object(musicbrainz, "_request_json", side_effect=[group, release]) as request, \
             patch.object(musicbrainz, "_cover_art_url", return_value="https://coverartarchive.org/release-group/rg-1/front-250"), \
             patch.object(musicbrainz.listenbrainz, "enrich_albums", side_effect=lambda items: items):
            item = musicbrainz.album_details("rg-1")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(item["artists"], "The Smile")
        self.assertEqual(item["track_count"], 8)
        self.assertEqual(item["label"], "XL Recordings")
        self.assertEqual(item["genres_data"][0]["name"], "art rock")
        self.assertEqual(item["details_json"]["track_list"][0]["title"], "Wall of Eyes")
        self.assertEqual(item["cover_url"], "https://coverartarchive.org/release-group/rg-1/front-250")

    def test_cover_art_returns_empty_for_missing_or_non_front_image(self) -> None:
        missing = urllib.error.HTTPError("url", 404, "Not found", None, None)
        with patch.object(musicbrainz.urllib.request, "urlopen", side_effect=missing):
            self.assertEqual(musicbrainz._cover_art_url("rg-1"), "")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"images":[{"front":false,"thumbnails":{"250":"image"}}]}'

        with patch.object(musicbrainz.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(musicbrainz._cover_art_url("rg-1"), "")

    def test_cover_art_uses_validated_release_group_front_250_url(self) -> None:
        class Response:
            def __init__(self, body=b""):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.body

        metadata = Response(b'{"images":[{"front":true,"thumbnails":{"250":"image"}}]}')
        with patch.object(musicbrainz.urllib.request, "urlopen", side_effect=[metadata, Response()]) as request:
            url = musicbrainz._cover_art_url("rg-1")
        self.assertEqual(url, "https://coverartarchive.org/release-group/rg-1/front-250")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[0].get_method(), "HEAD")

    def test_cover_art_error_is_published_to_running_progress(self) -> None:
        group = {
            "id":"rg-progress", "title":"Album", "first-release-date":"2026-01-01",
            "primary-type":"Album", "secondary-types":[], "artist-credit":[], "releases":[],
        }
        progress_id = "cover-warning-job"
        musicbrainz.recommendation_progress.start(
            progress_id, 1, stage_id="musicbrainz-details",
            label="MusicBrainz · карточки альбомов", unit="альбомов",
        )
        musicbrainz.recommendation_progress.set_stage(
            progress_id, "cover-art", "Cover Art Archive · обложки", 1, "альбомов"
        )
        with patch.object(musicbrainz, "_request_json", return_value=group), \
             patch.object(
                 musicbrainz, "_cover_art_url",
                 side_effect=musicbrainz.MusicBrainzError("Cover Art Archive: HTTP 500"),
             ):
            item = musicbrainz.album_details(
                "rg-progress", fetch_popularity=False, progress_id=progress_id
            )
        progress = musicbrainz.recommendation_progress.get(progress_id)
        self.assertEqual(item["cover_path"], "")
        self.assertEqual(progress["warnings"][0]["provider"], "Cover Art Archive")
        self.assertIn("HTTP 500", progress["warnings"][0]["message"])
        self.assertEqual([stage["processed"] for stage in progress["stages"]], [1, 1])

    def test_search_album_prefers_exact_artist_title_and_year(self) -> None:
        response = {"release-groups":[
            {"id":"wrong","title":"Album","first-release-date":"2024","score":100,"artist-credit":[{"name":"Other","artist":{"id":"a"}}]},
            {"id":"right","title":"Album","first-release-date":"2023","score":95,"primary-type":"Album","artist-credit":[{"name":"Artist","artist":{"id":"b"}}]},
        ]}
        with patch.object(musicbrainz, "_request_json", return_value=response):
            item = musicbrainz.search_album("Album", "Artist", 2023)
        self.assertEqual(item["release_group_mbid"], "right")

    def test_refresh_failure_is_returned_as_provider_warning(self) -> None:
        storage.add_item({
            "content_type":"music", "title_original":"Album", "title_ru":"Album",
            "artists":"Artist", "release_group_mbid":"rg-1",
        })
        with patch.object(musicbrainz, "resolve_album_input", side_effect=musicbrainz.MusicBrainzError("HTTP 503")), \
             patch.object(musicbrainz.listenbrainz, "release_group_popularity", return_value={"rg-1":None}):
            result = musicbrainz.refresh_library()
        self.assertEqual(result["failed"], 1)
        self.assertIn("MusicBrainz", result["provider_warnings"][0]["message"])

    def test_bulk_refresh_fully_updates_incomplete_albums_and_uses_one_listenbrainz_batch(self) -> None:
        cover = "https://coverartarchive.org/release-group/{}/front-500"
        storage.add_item({
            "content_type":"music", "title_original":"Complete", "title_ru":"Complete",
            "artists":"Artist", "release_group_mbid":"rg-complete",
            "first_release_date":"2024-01-01", "track_count":10,
            "musicbrainz_updated_at":"2026-01-01T00:00:00+00:00",
            "cover_url":cover.format("rg-complete"), "cover_path":"albums/rg-complete.jpg",
            "total_listen_count":100,
        })
        missing = storage.add_item({
            "content_type":"music", "title_original":"Missing", "title_ru":"Missing",
            "artists":"Artist", "release_group_mbid":"rg-missing",
            "cover_url":cover.format("rg-missing"), "cover_path":"albums/rg-missing.jpg",
        })
        refreshed = {
            "release_group_mbid":"rg-missing", "title_original":"Missing",
            "title_ru":"Missing", "artists":"Artist", "cover_art_checked":False,
            "cover_cache_checked":False,
        }
        with patch.object(musicbrainz.artwork, "is_cached", return_value=True), \
             patch.object(musicbrainz, "resolve_album_input", return_value=refreshed) as metadata, \
             patch.object(
                 musicbrainz.listenbrainz, "release_group_popularity",
                 return_value={"rg-missing":321},
             ) as popularity:
            result = musicbrainz.refresh_library()
        metadata.assert_called_once()
        self.assertEqual(metadata.call_args.args[0]["release_group_mbid"], "rg-missing")
        self.assertEqual(metadata.call_args.kwargs, {"fetch_popularity":False})
        popularity.assert_called_once()
        self.assertEqual(popularity.call_args.args[0], ["rg-missing"])
        self.assertIn("on_batch", popularity.call_args.kwargs)
        self.assertIn("should_cancel", popularity.call_args.kwargs)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(storage.get_item(missing["id"])["total_listen_count"], 321)

    def test_album_refresh_skips_confirmed_missing_optional_data(self) -> None:
        target = {
            "release_group_mbid":"rg-empty",
            "musicbrainz_updated_at":"checked",
            "cover_url":"", "cover_path":"", "cover_art_updated_at":"checked",
            "total_listen_count":None, "listenbrainz_updated_at":"checked",
        }
        with patch.object(musicbrainz.artwork, "is_cached", return_value=False):
            self.assertFalse(musicbrainz._album_needs_refresh(target))

    def test_album_refresh_repairs_missing_file_for_known_cover(self) -> None:
        target = {
            "release_group_mbid":"rg-cover",
            "musicbrainz_updated_at":"checked",
            "cover_url":"https://cover.test/rg-cover.jpg", "cover_path":"albums/rg-cover.jpg",
            "cover_art_updated_at":"checked", "total_listen_count":None,
            "listenbrainz_updated_at":"checked",
        }
        with patch.object(musicbrainz.artwork, "is_cached", return_value=False):
            self.assertTrue(musicbrainz._album_needs_refresh(target))

    def test_refresh_artist_requests_musicbrainz_and_fanart(self) -> None:
        target = {
            "id":"artist-one", "name_original":"Portishead", "name_ru":"Portishead",
            "mbid":"artist-portishead",
        }
        details = {**target, "profile_url":"https://fanart.test/portishead.jpg"}
        with patch.object(musicbrainz.storage,"list_music_artists",return_value=[target]), \
             patch.object(musicbrainz,"resolve_artist_input",return_value=details) as resolve, \
             patch.object(musicbrainz.storage,"update_music_artist",return_value=details) as save:
            result = musicbrainz.refresh_artist("artist-one")
        resolve.assert_called_once_with(target, include_artwork=True)
        save.assert_called_once_with("artist-one", details)
        self.assertEqual(result["profile_url"], "https://fanart.test/portishead.jpg")

    def test_refresh_album_requests_metadata_artwork_and_popularity(self) -> None:
        target = {
            "id":"album-one", "content_type":"music", "title_original":"Dummy",
            "release_group_mbid":"rg-one", "artists":"Artist", "first_release_date":"",
            "track_count":"", "primary_type":"Album", "musicbrainz_updated_at":"",
            "cover_path":"", "total_listen_count":None,
        }
        details = {
            **target, "first_release_date":"2024-01-01", "track_count":10,
            "cover_url":"https://cover.test/rg-one.jpg", "cover_path":"albums/rg-one.jpg",
        }
        final = {**details, "total_listen_count":123}
        with patch.object(musicbrainz.storage,"get_item",side_effect=[target,final]), \
             patch.object(musicbrainz,"resolve_album_input",return_value=details) as resolve, \
             patch.object(musicbrainz.storage,"update_album_from_provider",return_value=details) as save, \
             patch.object(musicbrainz.listenbrainz,"release_group_popularity",return_value={"rg-one":123}) as popularity, \
             patch.object(musicbrainz.storage,"update_album_popularity") as save_popularity:
            result = musicbrainz.refresh_album("album-one")
        resolve.assert_called_once_with(target, fetch_popularity=False)
        save.assert_called_once_with("album-one", details)
        popularity.assert_called_once_with(["rg-one"])
        save_popularity.assert_called_once_with({"rg-one":123})
        self.assertEqual(result["total_listen_count"], 123)

    def test_bootstrap_browse_keeps_only_primary_studio_albums(self) -> None:
        response = {"release-groups":[
            {"id":"studio","title":"Studio","first-release-date":"2026-01-01","primary-type":"Album","secondary-types":[],"artist-credit":[]},
            {"id":"live","title":"Live","first-release-date":"2026-02-01","primary-type":"Album","secondary-types":["Live"],"artist-credit":[]},
            {"id":"ep","title":"EP","first-release-date":"2026-03-01","primary-type":"EP","secondary-types":[],"artist-credit":[]},
            {"id":"old","title":"Old","first-release-date":"2025-01-01","primary-type":"Album","secondary-types":[],"artist-credit":[]},
        ]}
        with patch.object(musicbrainz, "_request_json", return_value=response):
            items = musicbrainz.browse_artist_albums(
                "artist-1", 2026, 2026, studio_albums_only=True
            )
        self.assertEqual([item["release_group_mbid"] for item in items], ["studio"])

    def test_api_recommendations_exclude_soundtracks_and_allow_disabled_years(self) -> None:
        artist = storage.add_music_artist({"content_type":"music","name":"Artist","mbid":"artist-1"})
        candidates = [
            {"release_group_mbid":"studio","title_original":"Studio","year":2026,"secondary_types":[]},
            {"release_group_mbid":"old","title_original":"Old","year":1899,"secondary_types":[]},
            {"release_group_mbid":"deluxe","title_original":"Studio Deluxe Edition","year":2026,"secondary_types":[]},
            {"release_group_mbid":"soundtrack","title_original":"Score","year":2026,"secondary_types":["Soundtrack"]},
            {"release_group_mbid":"dj-mix","title_original":"DJ mix","year":2026,"secondary_types":["DJ-mix"]},
        ]
        with patch.object(musicbrainz, "browse_artist_albums", return_value=candidates) as browse, \
             patch.object(musicbrainz, "album_details", side_effect=lambda mbid, **_kwargs: next(item for item in candidates if item["release_group_mbid"] == mbid)), \
             patch.object(musicbrainz.listenbrainz, "enrich_albums", side_effect=lambda items, **_kwargs: items) as popularity:
            result = musicbrainz.recommend_albums({
                "artist_ids":[artist["id"]], "excluded_types":["Soundtrack","DJ-mix"], "limit":10,
                "excluded_title_terms":["Deluxe"],
                "progress_id":"recommend-stages",
            })
        self.assertEqual([item["release_group_mbid"] for item in result["items"]], ["studio"])
        self.assertEqual(
            {record["item"]["release_group_mbid"] for record in result["filtered_items"]},
            {"deluxe", "soundtrack", "dj-mix"},
        )
        self.assertTrue(all(record["reason"] for record in result["filtered_items"]))
        self.assertEqual(browse.call_args.args[1:3], (1900, date.today().year + 2))
        self.assertEqual(popularity.call_args.args[0], result["items"])
        self.assertTrue(popularity.call_args.kwargs["continue_on_error"])
        progress = musicbrainz.recommendation_progress.get("recommend-stages")
        self.assertTrue(progress["complete"])
        self.assertEqual(
            [stage["id"] for stage in progress["stages"]],
            ["musicbrainz-artists","musicbrainz-details","cover-art","listenbrainz"],
        )

    def test_existing_album_is_not_returned_as_filtered(self) -> None:
        artist = storage.add_music_artist({"content_type":"music","name":"Artist","mbid":"artist-1"})
        storage.add_item({
            "content_type":"music", "title_original":"Existing", "title_ru":"Existing",
            "artists":"Artist", "year":2026, "release_group_mbid":"existing", "status":"backlog",
        })
        candidates = [{
            "release_group_mbid":"existing", "title_original":"Existing", "year":2026,
            "primary_type":"Album", "secondary_types":[],
        }]
        with patch.object(musicbrainz, "browse_artist_albums", return_value=candidates), \
             patch.object(musicbrainz, "album_details") as details:
            result = musicbrainz.recommend_albums({"artist_ids":[artist["id"]], "limit":10})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["filtered_items"], [])
        details.assert_not_called()


if __name__ == "__main__":
    unittest.main()
