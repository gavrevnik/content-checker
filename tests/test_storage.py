from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import artwork, storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.db_patch = patch.object(storage, "DB_PATH", self.db)
        self.db_patch.start()
        storage.initialize_database()
        with storage.connect() as connection:
            connection.execute("INSERT INTO people(id,name_original,name_ru,tmdb_id,active) VALUES ('nolan','Christopher Nolan','Кристофер Нолан',525,1)")
            connection.execute("INSERT INTO interest_roles(person_id,content_type,role) VALUES ('nolan','movie','director')")

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp.cleanup()

    def test_add_update_and_relations(self) -> None:
        created = storage.add_item({
            "content_type":"movie", "title_original":"Inception", "title_ru":"Начало", "year":"2010",
            "release_date":"2010-07-16", "status":"backlog", "tmdb_id":"27205", "imdb_id":"tt1375666",
            "directors_data":[{"name":"Christopher Nolan","tmdb_id":525,"role":"director"}],
            "genres_data":[{"id":878,"name":"Фантастика"}], "awards_json":[{"source":"test","summary":"4 Oscars"}],
        })
        self.assertEqual(created["directors"], "Кристофер Нолан (Christopher Nolan)")
        self.assertIn("Режиссёры: Кристофер Нолан (Christopher Nolan)", created["key_people"])
        self.assertEqual(created["genres"], "Фантастика")
        self.assertEqual(json.loads(created["awards_json"])[0]["summary"], "4 Oscars")
        self.assertFalse(created["planned_soon"])
        planned = storage.update_item(created["id"], {"planned_soon":True})
        self.assertTrue(planned["planned_soon"])
        updated = storage.update_item(created["id"], {"status":"consumed","reaction":"like"})
        self.assertEqual(updated["reaction"], "like")
        self.assertFalse(updated["planned_soon"])
        self.assertTrue(updated["consumed_at"])
        reset = storage.update_item(created["id"], {"reaction":""})
        self.assertEqual(reset["reaction"], "")
        noted = storage.update_item(created["id"], {"notes":"Пересмотреть режиссёрскую версию"})
        self.assertEqual(noted["notes"], "Пересмотреть режиссёрскую версию")

    def test_return_to_backlog_clears_reaction(self) -> None:
        created = storage.add_item({"content_type":"movie","title_original":"Memento","title_ru":"Помни","status":"consumed","reaction":"dislike"})
        updated = storage.update_item(created["id"], {"status":"backlog"})
        self.assertEqual(updated["reaction"], "")
        self.assertEqual(updated["consumed_at"], "")

    def test_backlog_mood_mapping_is_replaced_and_keeps_only_current_backlog_ids(self) -> None:
        first = storage.add_item({
            "content_type":"movie", "title_original":"The Shining", "title_ru":"Сияние", "status":"backlog",
        })
        second = storage.add_item({
            "content_type":"movie", "title_original":"Alien", "title_ru":"Чужой", "status":"backlog",
        })
        consumed = storage.add_item({
            "content_type":"movie", "title_original":"Psycho", "title_ru":"Психо", "status":"consumed",
        })
        stored = storage.replace_backlog_moods("movie", [{
            "label":"классика ужасов", "item_ids":[first["id"], second["id"], consumed["id"], "unknown"],
        }])
        self.assertEqual(stored[0]["label"], "классика ужасов")
        self.assertEqual(stored[0]["item_ids"], [first["id"], second["id"]])
        storage.replace_backlog_moods("movie", [{"label":"космический ужас", "item_ids":[second["id"]]}])
        self.assertEqual(
            [{key:value for key,value in mood.items() if key != "updated_at"} for mood in storage.list_backlog_moods("movie")],
            [{"label":"космический ужас", "item_ids":[second["id"]]}],
        )
        storage.update_item(second["id"], {"status":"consumed"})
        self.assertEqual(storage.list_backlog_moods("movie"), [])

    def test_duplicate_tmdb_or_title_is_rejected(self) -> None:
        storage.add_item({"content_type":"movie","title_original":"The Shining","title_ru":"Сияние","year":"1980","tmdb_id":694})
        with self.assertRaises(storage.StorageError):
            storage.add_item({"content_type":"movie","title_original":"Shining","title_ru":"Сияние!","year":"1980"})
        with self.assertRaises(storage.StorageError):
            storage.add_item({"content_type":"movie","title_original":"Other","title_ru":"Другое","year":"2020","tmdb_id":694})

    def test_schema_integrity(self) -> None:
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"content_items","movies","albums","music_artists","album_artists","people","interest_roles","movie_people","genres","content_aliases","trash_entries","favorite_movies","backlog_moods","backlog_mood_items"} <= tables)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertIn("raw_json", {row[1] for row in connection.execute("PRAGMA table_info(content_items)")})
            self.assertIn("planned_soon", {row[1] for row in connection.execute("PRAGMA table_info(content_items)")})
            self.assertIn("raw_json", {row[1] for row in connection.execute("PRAGMA table_info(people)")})
            people_columns = {row[1] for row in connection.execute("PRAGMA table_info(people)")}
            self.assertTrue({"profile_path", "profile_url", "profile_local_path"} <= people_columns)
            movie_columns = {row[1] for row in connection.execute("PRAGMA table_info(movies)")}
            self.assertIn("kinopoisk_id", movie_columns)
            self.assertIn("kinopoisk_rating", movie_columns)
            self.assertIn("poster_path", movie_columns)
            self.assertIn("poster_url", movie_columns)
            self.assertIn("poster_local_path", movie_columns)
            self.assertTrue({"tmdb_updated_at", "omdb_updated_at", "kinopoisk_updated_at"} <= movie_columns)
            album_columns = {row[1] for row in connection.execute("PRAGMA table_info(albums)")}
            self.assertIn("cover_url", album_columns)
            self.assertIn("cover_path", album_columns)
            self.assertIn("cover_art_updated_at", album_columns)
            self.assertIn("total_listen_count", album_columns)
            self.assertIn("listenbrainz_updated_at", album_columns)
            self.assertNotIn("rating", album_columns)
            self.assertNotIn("rating_votes", album_columns)
            artist_columns = {row[1] for row in connection.execute("PRAGMA table_info(music_artists)")}
            self.assertTrue({"profile_url", "profile_local_path", "fanart_updated_at"} <= artist_columns)

    def test_album_artist_workflow_and_trash_restore(self) -> None:
        artist = storage.add_music_artist({
            "content_type":"music", "name":"Chelsea Wolfe", "mbid":"artist-mbid",
            "artist_type":"Person", "country":"US",
        })
        created = storage.add_item({
            "content_type":"music", "title_original":"She Reaches Out to She Reaches Out to She",
            "title_ru":"She Reaches Out to She Reaches Out to She", "year":"2024",
            "first_release_date":"2024-02-09", "release_group_mbid":"album-mbid",
            "track_count":10, "genres_data":[{"name":"darkwave","count":3}],
            "cover_url":"https://coverartarchive.org/release-group/album-mbid/front-500",
            "total_listen_count":12345,
            "artists_data":[{"name":"Chelsea Wolfe","mbid":"artist-mbid"}],
        })
        self.assertEqual(created["artists"], "Chelsea Wolfe")
        self.assertEqual(created["track_count"], 10)
        self.assertEqual(created["genres"], "darkwave")
        self.assertEqual(created["musicbrainz_link"], "https://musicbrainz.org/release-group/album-mbid")
        self.assertEqual(created["cover_url"], "https://coverartarchive.org/release-group/album-mbid/front-500")
        self.assertEqual(created["total_listen_count"], 12345)
        self.assertTrue(storage.update_item(created["id"], {"planned_soon":True})["planned_soon"])
        liked = storage.update_item(created["id"], {"status":"consumed", "reaction":"like"})
        self.assertEqual(liked["reaction"], "like")
        self.assertFalse(liked["planned_soon"])
        trashed = storage.trash_entity({"entity_type":"album", "entity_id":created["id"]})
        self.assertEqual(storage.list_library(content_type="music"), [])
        storage.restore_trash(trashed["id"])
        self.assertEqual(storage.list_library(content_type="music")[0]["id"], created["id"])
        artist_trash = storage.trash_entity({"entity_type":"music_artist", "entity_id":artist["id"]})
        self.assertEqual(storage.list_music_artists(), [])
        storage.restore_trash(artist_trash["id"])
        self.assertEqual(storage.list_music_artists()[0]["mbid"], "artist-mbid")

    def test_album_provider_refresh_preserves_workflow(self) -> None:
        created = storage.add_item({
            "content_type":"music", "title_original":"Album", "title_ru":"Album",
            "status":"consumed", "reaction":"dislike", "release_group_mbid":"rg-1",
            "artists":"Artist",
        })
        updated = storage.update_album_from_provider(created["id"], {
            "title_original":"Album", "title_ru":"Album", "release_group_mbid":"rg-1",
            "first_release_date":"2024-01-01", "track_count":8, "artists":"Artist",
            "details_json":{"track_list":[{"number":"1","title":"Opening"}]},
        })
        self.assertEqual(updated["status"], "consumed")
        self.assertEqual(updated["reaction"], "dislike")
        self.assertEqual(updated["track_count"], 8)
        self.assertEqual(updated["track_list"][0]["title"], "Opening")

    def test_album_refresh_preserves_cover_after_transient_cover_art_error(self) -> None:
        cover_url = "https://coverartarchive.org/release-group/rg-cover/front-500"
        created = storage.add_item({
            "content_type":"music", "title_original":"Test Album", "title_ru":"Test Album",
            "artists":"Test Artist", "release_group_mbid":"rg-cover", "year":2026,
            "cover_url":cover_url, "cover_art_checked":True,
        })
        updated = storage.update_album_from_provider(created["id"], {
            "release_group_mbid":"rg-cover", "title_original":"Test Album",
            "cover_url":"", "cover_art_checked":False,
        })
        self.assertEqual(updated["cover_url"], cover_url)

    def test_favorite_is_independent_from_status_and_reaction(self) -> None:
        created = storage.add_item({
            "content_type":"movie", "title_original":"Favorite", "title_ru":"Избранный",
            "status":"consumed", "reaction":"like",
        })
        favorite = storage.set_favorite(created["id"], True)
        self.assertTrue(favorite["favorite"])
        self.assertEqual(favorite["status"], "consumed")
        self.assertEqual(favorite["reaction"], "like")
        moved = storage.update_item(created["id"], {"status":"backlog"})
        self.assertTrue(moved["favorite"])
        removed = storage.set_favorite(created["id"], False)
        self.assertFalse(removed["favorite"])
        self.assertEqual(removed["status"], "backlog")

    def test_album_can_be_added_to_favorites(self) -> None:
        created = storage.add_item({
            "content_type":"music", "title_original":"Favorite Album", "title_ru":"Favorite Album",
            "artists":"Test Artist", "year":2026, "release_group_mbid":"favorite-album",
            "status":"consumed", "reaction":"like",
        })
        favorite = storage.set_favorite(created["id"], True)
        self.assertTrue(favorite["favorite"])
        self.assertEqual(favorite["content_type"], "music")
        listed = storage.list_library(content_type="music")
        self.assertTrue(next(item for item in listed if item["id"] == created["id"])["favorite"])
        self.assertFalse(storage.set_favorite(created["id"], False)["favorite"])

    def test_artist_refresh_targets_exclude_complete_artists(self) -> None:
        storage.add_music_artist({
            "name":"Known Artist", "mbid":"known-artist",
            "profile_local_path":"people/music-known-artist.jpg",
            "musicbrainz_updated_at":"2026-01-01T00:00:00+00:00",
        })
        missing = storage.add_music_artist({"name":"Unknown Artist"})
        with patch.object(artwork, "is_cached", side_effect=lambda path: bool(path)):
            targets = storage.artist_refresh_targets()
        self.assertEqual([target["id"] for target in targets], [missing["id"]])

    def test_artist_refresh_targets_include_missing_fanart(self) -> None:
        artist = storage.add_music_artist({"name":"Known Artist", "mbid":"known-artist"})
        targets = storage.artist_refresh_targets()
        self.assertEqual([target["id"] for target in targets], [artist["id"]])

    def test_artist_refresh_targets_cache_negative_fanart_result_but_repair_known_image(self) -> None:
        absent = storage.add_music_artist({
            "name":"No Image", "mbid":"no-image", "musicbrainz_checked":True,
            "fanart_checked":True,
        })
        repair = storage.add_music_artist({
            "name":"Lost Image", "mbid":"lost-image", "musicbrainz_checked":True,
            "fanart_checked":True, "profile_url":"https://fanart.test/lost.jpg",
        })
        with patch.object(artwork, "is_cached", return_value=False):
            targets = storage.artist_refresh_targets()
        self.assertNotIn(absent["id"], [target["id"] for target in targets])
        self.assertIn(repair["id"], [target["id"] for target in targets])

    def test_people_can_be_added_and_refreshed(self) -> None:
        created = storage.add_interest_person({
            "role":"actor", "name_original":"Jack Nicholson", "name_ru":"Джек Николсон", "tmdb_id":514,
            "profile_path":"/jack.jpg", "profile_url":"https://image.tmdb.org/t/p/w185/jack.jpg",
            "profile_local_path":"people/514.jpg", "details_json":{"birthday":"1937-04-22"},
        })
        self.assertEqual(created["tmdb_id"], 514)
        self.assertEqual(created["profile_local_path"], "people/514.jpg")
        self.assertIn("1937-04-22", created["details_json"])
        updated = storage.update_interest_person(created["id"], {
            "tmdb_id":514, "name_original":"Jack Nicholson", "name_ru":"Джек Николсон",
            "details_json":{"birthday":"1937-04-22"},
        })
        self.assertIn("1937-04-22", updated["details_json"])

    def test_adding_second_person_role_marks_existing_movie_credit_as_interest(self) -> None:
        actor = storage.add_interest_person({
            "role":"actor", "name_original":"John Travolta", "name_ru":"Джон Траволта", "tmdb_id":8891,
        })
        movie = storage.add_item({
            "content_type":"movie", "title_original":"Dual Credit", "title_ru":"Двойная роль",
            "directors_data":[{
                "name_original":"John Travolta", "name_ru":"Джон Траволта",
                "tmdb_id":8891, "role":"director",
            }],
            "key_people_data":[{
                "name_original":"John Travolta", "name_ru":"Джон Траволта",
                "tmdb_id":8891, "role":"actor",
            }],
        })
        self.assertEqual(storage.get_item(movie["id"])["key_directors"], "")
        director = storage.add_interest_person({
            "role":"director", "name_original":"John Travolta", "name_ru":"Джон Траволта", "tmdb_id":8891,
        })
        self.assertEqual(actor["id"], director["id"])
        refreshed = storage.get_item(movie["id"])
        self.assertIn("Джон Траволта", refreshed["key_actors"])
        self.assertIn("Джон Траволта", refreshed["key_directors"])

    def test_person_refresh_merges_existing_tmdb_credit_record(self) -> None:
        created = storage.add_interest_person({
            "role":"director", "name_original":"Bennett Miller", "name_ru":"Беннетт Миллер", "tmdb_id":999999,
        })
        movie = storage.add_item({
            "content_type":"movie", "title_original":"Foxcatcher", "title_ru":"Охотник на лис",
        })
        with storage.connect() as connection:
            connection.execute(
                "INSERT INTO people(id,name_original,name_ru,tmdb_id,active) VALUES ('provider-copy','Bennett Miller','Беннетт Миллер',5345,0)"
            )
            connection.execute(
                "INSERT INTO movie_people(movie_id,person_id,credit_role,is_interest) VALUES (?,'provider-copy','director',0)",
                (movie["id"],),
            )
            connection.execute(
                "INSERT INTO movie_people(movie_id,person_id,credit_role,is_interest) VALUES (?,'provider-copy','actor',0)",
                (movie["id"],),
            )
        updated = storage.update_interest_person(created["id"], {
            "tmdb_id":5345, "name_original":"Bennett Miller", "name_ru":"Беннетт Миллер",
        })
        self.assertEqual(updated["tmdb_id"], 5345)
        with storage.connect() as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM people WHERE id='provider-copy'").fetchone())
            flags = {
                row["credit_role"]: row["is_interest"]
                for row in connection.execute(
                    "SELECT credit_role,is_interest FROM movie_people WHERE movie_id=? AND person_id=?",
                    (movie["id"], created["id"]),
                )
            }
        self.assertEqual(flags, {"actor":0, "director":1})

    def test_successful_provider_checks_are_timestamped_on_insert(self) -> None:
        movie = storage.add_item({
            "content_type":"movie", "title_original":"Checked", "title_ru":"Проверен",
            "tmdb_id":123, "tmdb_checked":True, "omdb_checked":True,
            "kinopoisk_checked":True,
        })
        album = storage.add_item({
            "content_type":"music", "title_original":"Checked Album", "title_ru":"Checked Album",
            "release_group_mbid":"rg-checked", "musicbrainz_checked":True,
            "cover_art_checked":True, "total_listen_count":None,
        })
        artist = storage.add_music_artist({
            "name":"Checked Artist", "mbid":"artist-checked", "musicbrainz_checked":True,
            "fanart_checked":True,
        })
        storage.add_music_artist({"name":"Resolved Existing"})
        resolved_existing = storage.add_music_artist({
            "name":"Resolved Existing", "mbid":"resolved-existing", "musicbrainz_checked":True,
        })
        self.assertTrue(movie["tmdb_updated_at"])
        self.assertTrue(movie["omdb_updated_at"])
        self.assertTrue(movie["kinopoisk_updated_at"])
        self.assertTrue(album["musicbrainz_updated_at"])
        self.assertTrue(album["cover_art_updated_at"])
        self.assertTrue(album["listenbrainz_updated_at"])
        self.assertTrue(artist["musicbrainz_updated_at"])
        self.assertTrue(artist["fanart_updated_at"])
        self.assertTrue(resolved_existing["musicbrainz_updated_at"])

    def test_provider_details_and_external_links_are_persisted(self) -> None:
        created = storage.add_item({"content_type":"movie","title_original":"Test","title_ru":"Тест","tmdb_id":123})
        updated = storage.update_movie_from_provider(created["id"], {
            "title_original":"Test", "title_ru":"Тест", "tmdb_id":123, "imdb_id":"tt0123",
            "kinopoisk_id":456, "kinopoisk_rating":7.4,
            "tagline":"A tagline", "imdb_votes":"12,345", "metascore":77, "box_office":"$1,000",
            "cast":"Actor One; Actor Two", "keywords":"memory; dream",
        })
        self.assertEqual(updated["cast"], "Actor One; Actor Two")
        self.assertEqual(updated["imdb_link"], "https://www.imdb.com/title/tt0123/")
        self.assertEqual(updated["kinopoisk_link"], "https://www.kinopoisk.ru/film/456/")
        self.assertEqual(updated["kinopoisk_rating"], 7.4)
        self.assertEqual(updated["metascore"], 77)

        refreshed = storage.update_movie_from_provider(created["id"], {
            "title_original":"Test", "title_ru":"Тест", "tmdb_id":123,
        })
        self.assertEqual(refreshed["kinopoisk_rating"], 7.4)

    def test_rating_only_refresh_preserves_existing_movie_data(self) -> None:
        created = storage.add_item({
            "content_type":"movie", "title_original":"Movie", "title_ru":"Фильм",
            "year":2024, "imdb_rating":8.1, "overview":"Описание", "tmdb_id":10,
        })
        updated = storage.update_movie_ratings(created["id"], {
            "kinopoisk_rating":7.9, "kinopoisk_id":20,
        })
        self.assertEqual(updated["imdb_rating"], 8.1)
        self.assertEqual(updated["kinopoisk_rating"], 7.9)
        self.assertEqual(updated["title_ru"], "Фильм")
        self.assertEqual(updated["overview"], "Описание")

    def test_movie_raw_input_and_trash_restore(self) -> None:
        raw = {"title_ru":"сияние", "year":"1980"}
        created = storage.add_item({
            "content_type":"movie", "title_original":"The Shining", "title_ru":"Сияние",
            "year":"1980", "tmdb_id":694, "raw_data":raw,
        })
        self.assertEqual(json.loads(created["raw_json"]), raw)
        trashed = storage.trash_entity({"entity_type":"movie", "entity_id":created["id"]})
        self.assertEqual(storage.list_library(), [])
        self.assertEqual(storage.list_trash()[0]["title_original"], "The Shining")
        known_ids, _ = storage.known_movie_keys()
        self.assertIn("694", known_ids)
        storage.restore_trash(trashed["id"])
        self.assertEqual(storage.list_library()[0]["id"], created["id"])
        self.assertEqual(storage.list_trash(), [])

    def test_person_raw_input_and_trash_restore(self) -> None:
        raw = {"role":"actor", "name_ru":"иван иванов"}
        created = storage.add_interest_person({
            "role":"actor", "name_original":"Ivan Ivanov", "name_ru":"Иван Иванов",
            "tmdb_id":987654, "raw_data":raw,
        })
        self.assertEqual(json.loads(created["raw_json"]), raw)
        trashed = storage.trash_entity({"entity_type":"person", "entity_id":created["id"], "role":"actor"})
        self.assertFalse(any(item["id"] == created["id"] for item in storage.list_interests("movie")))
        self.assertEqual(storage.list_trash()[0]["role"], "actor")
        storage.restore_trash(trashed["id"])
        self.assertTrue(any(item["id"] == created["id"] for item in storage.list_interests("movie")))

    def test_empty_trash_deletes_entities_and_unreferenced_artwork(self) -> None:
        artwork_dir = Path(self.temp.name) / "artwork"
        deleted_poster = artwork_dir / "movies" / "deleted.jpg"
        shared_poster = artwork_dir / "movies" / "shared.jpg"
        deleted_cover = artwork_dir / "albums" / "deleted.jpg"
        for path in (deleted_poster, shared_poster, deleted_cover):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")

        kept_movie = storage.add_item({
            "content_type":"movie", "title_original":"Kept movie", "title_ru":"Kept movie",
            "poster_local_path":"movies/shared.jpg",
        })
        deleted_movie = storage.add_item({
            "content_type":"movie", "title_original":"Deleted movie", "title_ru":"Deleted movie",
            "poster_local_path":"movies/deleted.jpg",
        })
        shared_art_movie = storage.add_item({
            "content_type":"movie", "title_original":"Shared artwork movie", "title_ru":"Shared artwork movie",
            "poster_local_path":"movies/shared.jpg",
        })
        deleted_album = storage.add_item({
            "content_type":"music", "title_original":"Deleted album", "title_ru":"Deleted album",
            "release_group_mbid":"deleted-rg", "cover_path":"albums/deleted.jpg",
        })
        artist = storage.add_music_artist({"name":"Kept album artist", "mbid":"artist-kept"})
        kept_album = storage.add_item({
            "content_type":"music", "title_original":"Kept album", "title_ru":"Kept album",
            "release_group_mbid":"kept-rg",
            "artists_data":[{"name":"Kept album artist", "mbid":"artist-kept"}],
        })
        with storage.connect() as connection:
            connection.execute(
                "INSERT INTO interest_roles(person_id,content_type,role) VALUES ('nolan','movie','actor')"
            )

        storage.trash_entity({"entity_type":"movie", "entity_id":deleted_movie["id"]})
        storage.trash_entity({"entity_type":"movie", "entity_id":shared_art_movie["id"]})
        storage.trash_entity({"entity_type":"album", "entity_id":deleted_album["id"]})
        storage.trash_entity({"entity_type":"music_artist", "entity_id":artist["id"]})
        storage.trash_entity({"entity_type":"person", "entity_id":"nolan", "role":"actor"})

        with patch.object(artwork, "ARTWORK_DIR", artwork_dir):
            result = storage.empty_trash()

        self.assertEqual(result["deleted"], 5)
        self.assertEqual(result["artwork_deleted"], 2)
        self.assertEqual(result["artwork_errors"], [])
        self.assertEqual(storage.list_trash(), [])
        self.assertEqual([item["id"] for item in storage.list_library("movie")], [kept_movie["id"]])
        self.assertEqual([item["id"] for item in storage.list_library("music")], [kept_album["id"]])
        self.assertFalse(deleted_poster.exists())
        self.assertFalse(deleted_cover.exists())
        self.assertTrue(shared_poster.exists())
        self.assertEqual(storage.list_music_artists(), [])
        with storage.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT active FROM music_artists WHERE id = ?", (artist["id"],)).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM album_artists WHERE album_id = ?", (kept_album["id"],)).fetchone()[0],
                1,
            )
            roles = {
                row[0] for row in connection.execute(
                    "SELECT role FROM interest_roles WHERE person_id = 'nolan'"
                ).fetchall()
            }
        self.assertEqual(roles, {"director"})

    def test_empty_trash_is_a_noop_when_already_empty(self) -> None:
        self.assertEqual(storage.empty_trash()["deleted"], 0)


if __name__ == "__main__":
    unittest.main()
