from __future__ import annotations

import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from app import tmdb


class TmdbTests(unittest.TestCase):
    def test_secrets_file_is_parsed_without_execution_and_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "SECRETS").write_text(
                'TMDB_API_KEY = "tmdb-test"\nOMDB_API_KEY = "omdb-test"\nKINOPOISK_API_KEY = "kp-test"\n',
                encoding="utf-8",
            )
            with patch.object(tmdb, "ROOT", Path(directory)), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(tmdb.get_api_key(), ("tmdb-test", "SECRETS"))
                self.assertEqual(tmdb.get_omdb_key(), ("omdb-test", "SECRETS"))
                self.assertEqual(tmdb.get_kinopoisk_key(), ("kp-test", "SECRETS"))
            with patch.object(tmdb, "ROOT", Path(directory)), patch.dict(os.environ, {"TMDB_API_KEY":"env-test"}, clear=True):
                self.assertEqual(tmdb.get_api_key(), ("env-test", "environment"))

    def test_resolve_rejects_ambiguous_search(self) -> None:
        payload = {"results":[
            {"id":1,"title":"Версия A","original_title":"Version A","release_date":"1980-01-01"},
            {"id":2,"title":"Версия B","original_title":"Version B","release_date":"1980-06-01"},
        ]}
        with patch.object(tmdb,"_get",return_value=payload):
            with self.assertRaises(tmdb.TmdbError):
                tmdb.resolve_movie("Unknown","Неизвестный",1980,"key")

    def test_movie_details_maps_interests_and_companion_data(self) -> None:
        payload = {
            "id":42,"title":"Тест","original_title":"Test","release_date":"2025-05-01","runtime":121,
            "vote_average":7.64,"vote_count":500,"imdb_id":"tt42","overview":"Описание","original_language":"en",
            "genres":[{"id":18,"name":"драма"}],
            "credits":{"crew":[{"id":10,"name":"Director","job":"Director"}],"cast":[{"id":20,"name":"Actor","character":"Hero"}]},
        }
        interests = {10:{"director"},20:{"actor"}}
        with patch.object(tmdb,"_get",return_value=payload), patch.object(tmdb,"_interest_index",return_value=interests), patch.object(tmdb,"_get_omdb",return_value={"imdb_rating":8.1,"awards_json":[{"source":"omdb","summary":"2 wins"}]}), patch.object(tmdb,"_get_kinopoisk",return_value={"kinopoisk_id":301,"kinopoisk_rating":8.5}):
            result = tmdb.movie_details(42,"key")
        self.assertEqual(result["imdb_rating"],8.1)
        self.assertIn("Режиссёры: Director",result["key_people"])
        self.assertIn("Актёры: Actor",result["key_people"])
        self.assertEqual(result["awards_json"][0]["summary"],"2 wins")
        self.assertEqual(result["kinopoisk_rating"],8.5)
        self.assertTrue(result["tmdb_checked"])

    def test_movie_details_keeps_both_interest_roles_for_same_person(self) -> None:
        payload = {
            "id":42, "title":"Тест", "original_title":"Test", "release_date":"2025-05-01",
            "credits":{
                "crew":[{"id":8891,"name":"John Travolta","job":"Director"}],
                "cast":[{"id":8891,"name":"John Travolta","character":"Hero"}],
            },
        }
        with patch.object(tmdb,"_get",return_value=payload), \
             patch.object(tmdb,"_interest_index",return_value={8891:{"actor","director"}}), \
             patch.object(tmdb,"_get_kinopoisk",return_value={}):
            result = tmdb.movie_details(42,"key",fetch_omdb=False)
        self.assertEqual(
            {(person["tmdb_id"], person["role"]) for person in result["key_people_data"]},
            {(8891,"actor"),(8891,"director")},
        )

    def test_interest_index_groups_roles_by_tmdb_person(self) -> None:
        rows = [
            {"external_id":8891,"role":"actor"},
            {"external_id":8891,"role":"director"},
        ]
        with patch.object(tmdb.storage,"list_interests",return_value=rows):
            self.assertEqual(tmdb._interest_index(), {8891:{"actor","director"}})

    def test_kinopoisk_uses_imdb_lookup_first_and_maps_rating(self) -> None:
        payload = {"items":[{
            "kinopoiskId":301, "imdbId":"tt0133093", "nameRu":"Матрица",
            "nameOriginal":"The Matrix", "year":1999, "ratingKinopoisk":8.5,
        }]}
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",return_value=payload) as request:
            result = tmdb._get_kinopoisk("tt0133093","The Matrix","Матрица",1999)
        url, headers = request.call_args.args
        self.assertIn("imdbId=tt0133093", url)
        self.assertNotIn("keyword=", url)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(headers["X-API-KEY"], "kp-key")
        self.assertEqual(result["kinopoisk_id"], 301)
        self.assertEqual(result["kinopoisk_rating"], 8.5)
        self.assertEqual(result["kinopoisk_link"], "https://www.kinopoisk.ru/film/301/")

    def test_kinopoisk_falls_back_to_title_and_year_in_one_request(self) -> None:
        payload = {"items":[{
            "kinopoiskId":735, "imdbId":None, "nameRu":"Сияние",
            "nameOriginal":"The Shining", "year":1980, "ratingKinopoisk":7.8,
        }]}
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",return_value=payload) as request:
            result = tmdb._get_kinopoisk("","The Shining","Сияние",1980)
        url = request.call_args.args[0]
        self.assertIn("keyword=", url)
        self.assertIn("yearFrom=1979", url)
        self.assertIn("yearTo=1981", url)
        self.assertEqual(result["kinopoisk_id"], 735)

    def test_omdb_successful_not_found_is_marked_as_checked(self) -> None:
        with patch.object(tmdb, "get_omdb_key", return_value=("omdb-key", "test")), \
             patch.object(tmdb, "_request_json", return_value={"Response":"False", "Error":"Movie not found!"}):
            result = tmdb._get_omdb("tt-missing")
        self.assertEqual(result, {"omdb_checked":True})

    def test_malformed_companion_responses_are_not_marked_as_checked(self) -> None:
        with patch.object(tmdb, "get_omdb_key", return_value=("omdb-key", "test")), \
             patch.object(tmdb, "_request_json", return_value={}):
            self.assertEqual(tmdb._get_omdb("tt-bad"), {})
        with patch.object(tmdb, "get_kinopoisk_key", return_value=("kp-key", "test")), \
             patch.object(tmdb, "_request_json", return_value={"items":"invalid"}):
            self.assertEqual(tmdb._get_kinopoisk("", "Bad", "Плохой", 2026), {})

    def test_kinopoisk_successful_empty_search_is_marked_as_checked(self) -> None:
        with patch.object(tmdb, "get_kinopoisk_key", return_value=("kp-key", "test")), \
             patch.object(tmdb, "_request_json", return_value={"items":[]}):
            result = tmdb._get_kinopoisk("", "Missing", "Отсутствует", 2026)
        self.assertEqual(result, {"kinopoisk_checked":True})

    def test_kinopoisk_matches_title_when_provider_has_no_imdb_mapping(self) -> None:
        payload = {"items":[{
            "kinopoiskId":256408, "imdbId":None, "nameRu":"Древо жизни",
            "nameOriginal":"The Tree of Life", "year":2010, "ratingKinopoisk":6.6,
        }]}
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",side_effect=[{"items":[]},payload]) as request:
            result = tmdb._get_kinopoisk("tt0478304","The Tree of Life","Древо жизни",2011)
        self.assertEqual(result["kinopoisk_id"], 256408)
        self.assertEqual(result["kinopoisk_rating"], 6.6)
        self.assertEqual(request.call_count, 2)
        self.assertIn("imdbId=tt0478304", request.call_args_list[0].args[0])
        self.assertIn("keyword=", request.call_args_list[1].args[0])

    def test_kinopoisk_fallback_normalizes_yo_in_russian_title(self) -> None:
        payload = {"items":[{
            "kinopoiskId":7649, "imdbId":"tt0156701", "nameRu":"Хрусталев, машину!",
            "nameOriginal":None, "year":1998, "ratingKinopoisk":7.3,
        }]}
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",side_effect=[{"items":[]},payload]) as request:
            result = tmdb._get_kinopoisk(
                "tt0156701", "Хрусталёв, машину!", "Хрусталёв, машину!", 1999,
            )
        fallback_url = request.call_args_list[1].args[0]
        fallback_query = urllib.parse.parse_qs(urllib.parse.urlparse(fallback_url).query)
        self.assertEqual(fallback_query["keyword"], ["Хрусталев, машину!"])
        self.assertEqual(fallback_query["yearFrom"], ["1998"])
        self.assertEqual(fallback_query["yearTo"], ["2000"])
        self.assertEqual(result["kinopoisk_id"], 7649)
        self.assertEqual(result["kinopoisk_rating"], 7.3)

    def test_kinopoisk_falls_back_to_title_after_imdb_request_error(self) -> None:
        payload = {"items":[{
            "kinopoiskId":7649, "imdbId":"tt0156701", "nameRu":"Хрусталев, машину!",
            "nameOriginal":None, "year":1998, "ratingKinopoisk":7.3,
        }]}
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",side_effect=[tmdb.TmdbError("temporary failure"),payload]) as request:
            result = tmdb._get_kinopoisk(
                "tt0156701", "Хрусталёв, машину!", "Хрусталёв, машину!", 1999,
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["kinopoisk_rating"], 7.3)
        self.assertNotIn("provider_warnings", result)

    def test_kinopoisk_quota_error_becomes_non_fatal_warning(self) -> None:
        with patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb,"_request_json",side_effect=tmdb.TmdbError("Provider returned HTTP 402: quota exceeded")):
            result = tmdb._get_kinopoisk("tt0478304","The Tree of Life","Древо жизни",2011)
        self.assertNotIn("kinopoisk_rating", result)
        self.assertEqual(result["provider_warnings"][0]["kind"], "limit")
        self.assertIn("лимит", result["provider_warnings"][0]["message"])

    def test_tmdb_provider_error_remains_fatal_and_is_labeled(self) -> None:
        with patch.object(tmdb,"_request_json",side_effect=tmdb.TmdbError("Provider returned HTTP 429: limit")):
            with self.assertRaisesRegex(tmdb.TmdbError, "^TMDB:"):
                tmdb._get("/movie/1", {"language":"ru-RU"}, "key")

    def test_person_details_uses_canonical_and_russian_alias(self) -> None:
        payload = {
            "id":514, "name":"Jack Nicholson", "also_known_as":["Джек Николсон"],
            "birthday":"1937-04-22", "place_of_birth":"Neptune City", "known_for_department":"Acting",
            "external_ids":{"imdb_id":"nm0000197"},
        }
        with patch.object(tmdb,"_get",return_value=payload) as fetch:
            result = tmdb.person_details(514,"key")
        self.assertEqual(result["name_original"],"Jack Nicholson")
        self.assertEqual(result["name_ru"],"Джек Николсон")
        self.assertEqual(result["imdb_id"],"nm0000197")
        self.assertEqual(result["details_json"]["imdb_id"],"nm0000197")
        self.assertTrue(result["details_json"]["external_ids_checked"])
        self.assertEqual(result["details_json"]["birthday"],"1937-04-22")
        self.assertEqual(
            fetch.call_args_list[0].args,
            ("/person/514", {"language":"en-US", "append_to_response":"external_ids"}, "key"),
        )

    def test_person_details_caches_small_tmdb_profile(self) -> None:
        payload = {
            "id":514, "name":"Jack Nicholson", "also_known_as":["Джек Николсон"],
            "profile_path":"/jack.jpg", "known_for_department":"Acting",
        }
        with patch.object(tmdb,"_get",return_value=payload), \
             patch.object(tmdb.artwork,"cache_person_profile",return_value="people/514.jpg") as cache:
            result = tmdb.person_details(514,"key")
        self.assertEqual(result["profile_url"],"https://image.tmdb.org/t/p/w185/jack.jpg")
        self.assertEqual(result["profile_local_path"],"people/514.jpg")
        cache.assert_called_once_with(514,"/jack.jpg","https://image.tmdb.org/t/p/w185/jack.jpg")

    def test_resolve_person_prefers_expected_department_and_popularity(self) -> None:
        payload = {"results":[
            {"id":1,"name":"Ari Aster","known_for_department":"Production","popularity":5},
            {"id":2,"name":"Ari Aster","known_for_department":"Directing","popularity":1},
        ]}
        with patch.object(tmdb,"_get",return_value=payload):
            self.assertEqual(tmdb.resolve_person("Ari Aster","director","key"),2)

    def test_resolve_person_prefers_exact_identity_to_unrelated_department(self) -> None:
        payload = {"results":[
            {"id":8891,"name":"John Travolta","known_for_department":"Acting","popularity":100},
            {"id":123,"name":"John Other","known_for_department":"Directing","popularity":1},
        ]}
        with patch.object(tmdb,"_get",return_value=payload):
            self.assertEqual(tmdb.resolve_person("John Travolta","director","key"),8891)

    def test_resolve_movie_input_returns_normalized_details(self) -> None:
        details = {"tmdb_id":694, "title_original":"The Shining", "title_ru":"Сияние"}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb,"resolve_movie",return_value=694) as resolve, \
             patch.object(tmdb,"movie_details",return_value=details) as fetch:
            result = tmdb.resolve_movie_input({"title_ru":"сияние", "year":"1980"})
        resolve.assert_called_once_with("", "сияние", "1980", "key")
        fetch.assert_called_once_with(694, "key")
        self.assertEqual(result, details)

    def test_resolve_movie_input_researches_an_edited_title_instead_of_stale_tmdb_id(self) -> None:
        details = {"tmdb_id":123, "title_original":"Between Worlds", "title_ru":"На границе миров"}
        payload = {
            "title_original":"Old provider title", "title_ru":"На границе миров", "year":"2018",
            "tmdb_id":"999", "search_field":"title_ru",
        }
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb,"resolve_movie",return_value=123) as resolve, \
             patch.object(tmdb,"movie_details",return_value=details):
            result = tmdb.resolve_movie_input(payload)
        resolve.assert_called_once_with("", "На границе миров", "2018", "key")
        self.assertEqual(result["tmdb_id"], 123)

    def test_movie_candidate_search_returns_relevance_sorted_list_and_cached_posters(self) -> None:
        response = {"results":[
            {"id":1,"title":"Матрица: Воскрешение","original_title":"The Matrix Resurrections","release_date":"2021-12-16","poster_path":"/one.jpg","vote_average":5.7},
            {"id":2,"title":"Матрица","original_title":"The Matrix","release_date":"1999-03-30","poster_path":"/two.jpg","vote_average":8.2},
        ]}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=({"2"},set())), \
             patch.object(tmdb,"_get",return_value=response) as request, \
             patch.object(tmdb.artwork,"cache_movie_poster",side_effect=lambda movie_id,*_:f"movies/{movie_id}.jpg"):
            result = tmdb.search_movie_candidates({"title_ru":"Матрица","year":"1999"})
        self.assertEqual([item["tmdb_id"] for item in result["items"]], [2,1])
        self.assertTrue(result["items"][0]["already_added"])
        self.assertEqual(result["items"][0]["poster_local_path"], "movies/2.jpg")
        self.assertEqual(request.call_args.args[1]["query"], "Матрица")

    def test_person_candidate_search_keeps_exact_name_first_even_for_second_role(self) -> None:
        response = {"results":[
            {"id":123,"name":"John Other","known_for_department":"Directing","popularity":200,"profile_path":"/other.jpg"},
            {"id":8891,"name":"John Travolta","known_for_department":"Acting","popularity":100,"profile_path":"/travolta.jpg","known_for":[{"title":"Pulp Fiction","release_date":"1994-10-14"}]},
        ]}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=[]), \
             patch.object(tmdb,"_get",return_value=response), \
             patch.object(tmdb.artwork,"cache_person_profile",side_effect=lambda person_id,*_:f"people/{person_id}.jpg"):
            result = tmdb.search_person_candidates({"role":"director","name_original":"John Travolta"})
        self.assertEqual([item["tmdb_id"] for item in result["items"]], [8891,123])
        self.assertEqual(result["items"][0]["role"], "director")
        self.assertEqual(result["items"][0]["known_for"], "Pulp Fiction (1994)")
        self.assertEqual(result["items"][0]["profile_local_path"], "people/8891.jpg")

    def test_person_candidate_search_accepts_russian_name_and_checks_selected_role(self) -> None:
        response = {"results":[
            {"id":8891,"name":"John Travolta","known_for_department":"Acting","popularity":100},
            {"id":123,"name":"John Other","known_for_department":"Directing","popularity":200},
        ]}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=[]) as interests, \
             patch.object(tmdb,"_get",return_value=response) as request:
            result = tmdb.search_person_candidates({"role":"director","name_ru":"Джон Траволта"})
        interests.assert_called_once_with("movie", "director", include_trashed=True)
        self.assertEqual(request.call_args.args[1]["query"], "Джон Траволта")
        self.assertFalse(result["items"][0]["already_added"])
        self.assertEqual(result["items"][0]["tmdb_id"], 8891)

    def test_resolve_person_input_can_use_tmdb_id(self) -> None:
        details = {"tmdb_id":514, "name_original":"Jack Nicholson", "name_ru":"Джек Николсон"}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb,"person_details",return_value=details) as fetch:
            result = tmdb.resolve_person_input({"role":"actor", "tmdb_id":"514", "name_ru":"джек"})
        fetch.assert_called_once_with(514, "key")
        self.assertEqual(result["role"], "actor")

    def test_refresh_person_updates_only_requested_person(self) -> None:
        targets = [
            {"id":"person-one","name_original":"One","name_ru":"Один","role":"actor","tmdb_id":1},
            {"id":"person-two","name_original":"Two","name_ru":"Два","role":"director","tmdb_id":2},
        ]
        details = {"tmdb_id":2,"name_original":"Two","name_ru":"Два","details_json":{}}
        updated = {**targets[1], **details}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=targets), \
             patch.object(tmdb,"_fetch_person_target",return_value=("person-two",details)) as fetch, \
             patch.object(tmdb.storage,"update_interest_person",return_value=updated) as save:
            result = tmdb.refresh_person("person-two")
        fetch.assert_called_once_with(targets[1], "key")
        save.assert_called_once_with("person-two", details)
        self.assertEqual(result["id"], "person-two")

    def test_refresh_person_preserves_requested_role_for_shared_person(self) -> None:
        targets = [
            {"id":"shared","name_original":"Shared","name_ru":"Общий","role":"actor","tmdb_id":1},
            {"id":"shared","name_original":"Shared","name_ru":"Общий","role":"director","tmdb_id":1},
        ]
        details = {"tmdb_id":1,"name_original":"Shared","name_ru":"Общий","details_json":{}}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=targets), \
             patch.object(tmdb,"_fetch_person_target",return_value=("shared",details)) as fetch, \
             patch.object(tmdb.storage,"update_interest_person") as save:
            result = tmdb.refresh_person("shared", "director")
        fetch.assert_called_once_with(targets[1], "key")
        save.assert_called_once_with("shared", details)
        self.assertEqual(result["role"], "director")

    def test_bulk_people_refresh_updates_people_without_tmdb_id_or_cached_profile(self) -> None:
        targets = [
            {"id":"known","name_original":"Known","name_ru":"Известный","role":"actor","tmdb_id":10,"external_id":10,"tmdb_updated_at":"2026-01-01","profile_path":"","profile_url":"","profile_local_path":"","details_json":"{\"imdb_id\":\"nm10\"}"},
            {"id":"missing","name_original":"Missing","name_ru":"Без ID","role":"director","tmdb_id":"","external_id":""},
        ]
        details = {"tmdb_id":20,"name_original":"Missing","name_ru":"Без ID","details_json":{}}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=targets), \
             patch.object(tmdb,"_fetch_person_target",return_value=("missing",details)) as fetch, \
             patch.object(tmdb.storage,"update_interest_person") as save:
            result = tmdb.refresh_people()
        fetch.assert_called_once_with(targets[1], "key")
        save.assert_called_once_with("missing", details)
        self.assertEqual(result, {"total":1,"updated":1,"failed":0,"errors":[]})

    def test_bulk_people_refresh_updates_shared_person_only_once(self) -> None:
        targets = [
            {"id":"shared","name_original":"Shared","name_ru":"Общий","role":"actor","tmdb_id":"","external_id":""},
            {"id":"shared","name_original":"Shared","name_ru":"Общий","role":"director","tmdb_id":"","external_id":""},
        ]
        details = {"tmdb_id":1,"name_original":"Shared","name_ru":"Общий","details_json":{}}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=targets), \
             patch.object(tmdb,"_fetch_person_target",return_value=("shared",details)) as fetch, \
             patch.object(tmdb.storage,"update_interest_person") as save:
            result = tmdb.refresh_people()
        fetch.assert_called_once_with(targets[0], "key")
        save.assert_called_once_with("shared", details)
        self.assertEqual(result, {"total":1,"updated":1,"failed":0,"errors":[]})

    def test_person_without_imdb_id_is_not_rechecked_after_external_ids_lookup(self) -> None:
        target = {
            "tmdb_id":10, "tmdb_updated_at":"2026-01-01", "profile_path":"",
            "profile_url":"", "profile_local_path":"",
            "details_json":{"imdb_id":"", "external_ids_checked":True},
        }
        self.assertFalse(tmdb._person_needs_refresh(target))

    def test_bulk_people_refresh_recaches_known_person_with_missing_profile_file(self) -> None:
        target = {
            "id":"known", "name_original":"Known", "name_ru":"Известный", "role":"actor",
            "tmdb_id":10, "external_id":10, "tmdb_updated_at":"2026-01-01",
            "profile_path":"/known.jpg", "profile_url":"https://image.tmdb.org/t/p/w185/known.jpg",
            "profile_local_path":"people/10.jpg",
            "details_json":{"imdb_id":"nm10"},
        }
        details = {"tmdb_id":10,"name_original":"Known","name_ru":"Известный","details_json":{}}
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=[target]), \
             patch.object(tmdb.artwork,"is_cached",return_value=False), \
             patch.object(tmdb,"_fetch_person_target",return_value=("known",details)) as fetch, \
             patch.object(tmdb.storage,"update_interest_person") as save:
            result = tmdb.refresh_people()
        fetch.assert_called_once_with(target, "key")
        save.assert_called_once_with("known", details)
        self.assertEqual(result["updated"], 1)

    def test_refresh_movie_runs_full_tmdb_update_when_imdb_rating_is_missing(self) -> None:
        target = {
            "id":"movie-one", "title_original":"The Matrix", "title_ru":"Матрица",
            "year":1999, "tmdb_id":603, "imdb_id":"tt0133093",
            "imdb_rating":"", "kinopoisk_rating":8.5, "directors":"Lana Wachowski",
            "poster_local_path":"movies/603.jpg",
        }
        details = {**target, "imdb_rating":8.7}
        with patch.object(tmdb.storage,"get_item",return_value=target), \
             patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb,"get_omdb_key",return_value=("omdb-key","test")), \
             patch.object(tmdb,"movie_details",return_value=details) as fetch, \
             patch.object(tmdb,"_get_kinopoisk") as kinopoisk, \
             patch.object(tmdb.storage,"update_movie_from_provider",return_value=details) as save:
            tmdb.refresh_movie("movie-one")
        fetch.assert_called_once_with(603, "key", fetch_kinopoisk=False, fetch_omdb=True)
        kinopoisk.assert_not_called()
        save.assert_called_once_with("movie-one", details)

    def test_refresh_movie_calls_only_kinopoisk_when_only_its_rating_is_missing(self) -> None:
        target = {
            "id":"movie-one", "title_original":"The Matrix", "title_ru":"Матрица",
            "year":1999, "tmdb_id":603, "imdb_id":"tt0133093",
            "tmdb_updated_at":"2026-01-01T00:00:00+00:00",
            "imdb_rating":8.7, "kinopoisk_rating":"", "directors":"Lana Wachowski",
            "poster_local_path":"movies/603.jpg",
        }
        kp = {"kinopoisk_id":301, "kinopoisk_rating":8.5}
        with patch.object(tmdb.storage,"get_item",return_value=target), \
             patch.object(tmdb,"get_kinopoisk_key",return_value=("kp-key","test")), \
             patch.object(tmdb.artwork,"is_cached",return_value=True), \
             patch.object(tmdb,"_get_omdb") as imdb, \
             patch.object(tmdb,"_get_kinopoisk",return_value=kp) as kinopoisk, \
             patch.object(tmdb.storage,"update_movie_ratings",return_value={**target,**kp}) as save:
            tmdb.refresh_movie("movie-one")
        imdb.assert_not_called()
        kinopoisk.assert_called_once_with("tt0133093", "The Matrix", "Матрица", 1999)
        save.assert_called_once_with("movie-one", kp)

    def test_refresh_movie_does_not_retry_confirmed_missing_optional_metadata(self) -> None:
        target = {
            "id":"movie-one", "title_original":"The Matrix", "title_ru":"Матрица",
            "year":1999, "release_date":"1999-03-31", "tmdb_id":603,
            "tmdb_updated_at":"2026-01-01T00:00:00+00:00", "imdb_id":"tt0133093",
            "imdb_rating":8.7, "kinopoisk_rating":8.5, "directors":"Lana Wachowski",
            "duration_minutes":136, "genres":"фантастика", "overview":"", "cast":"Keanu Reeves",
            "poster_local_path":"movies/603.jpg",
        }
        with patch.object(tmdb.storage,"get_item",return_value=target), \
             patch.object(tmdb.artwork,"is_cached",return_value=True), \
             patch.object(tmdb,"movie_details") as fetch:
            result = tmdb.refresh_movie("movie-one")
        fetch.assert_not_called()
        self.assertEqual(result, target)

    def test_refresh_movie_skips_all_providers_when_both_ratings_exist(self) -> None:
        target = {
            "id":"movie-one", "tmdb_id":1,
            "tmdb_updated_at":"2026-01-01T00:00:00+00:00",
            "imdb_rating":8.7, "kinopoisk_rating":8.5,
            "poster_local_path":"movies/1.jpg",
        }
        with patch.object(tmdb.storage,"get_item",return_value=target), \
             patch.object(tmdb.artwork,"is_cached",return_value=True), \
             patch.object(tmdb,"_refresh_movie_target") as fetch:
            result = tmdb.refresh_movie("movie-one")
        fetch.assert_not_called()
        self.assertEqual(result, target)

    def test_refresh_movie_downloads_only_poster_when_only_local_file_is_missing(self) -> None:
        target = {
            "id":"movie-one", "tmdb_id":603, "imdb_rating":8.7,
            "tmdb_updated_at":"2026-01-01T00:00:00+00:00",
            "kinopoisk_rating":8.5, "directors":"Director", "poster_path":"/poster.jpg",
            "poster_url":"https://image.tmdb.org/t/p/w500/poster.jpg", "poster_local_path":"",
        }
        poster = {"poster_path":"/poster.jpg", "poster_url":target["poster_url"], "poster_local_path":"movies/603.jpg"}
        with patch.object(tmdb.storage,"get_item",return_value=target), \
             patch.object(tmdb.artwork,"is_cached",return_value=False), \
             patch.object(tmdb,"_poster_details",return_value=poster) as fetch, \
             patch.object(tmdb,"movie_details") as full, \
             patch.object(tmdb,"_get_kinopoisk") as kinopoisk, \
             patch.object(tmdb.storage,"update_movie_poster",return_value={**target,**poster}) as save:
            tmdb.refresh_movie("movie-one")
        fetch.assert_called_once_with(target)
        full.assert_not_called()
        kinopoisk.assert_not_called()
        save.assert_called_once_with("movie-one", poster)

    def test_bulk_rating_refresh_queues_only_movies_with_missing_rating(self) -> None:
        targets = [
            {"id":"complete", "tmdb_id":1, "tmdb_updated_at":"checked", "title_original":"Complete", "imdb_rating":8, "kinopoisk_rating":7, "poster_local_path":"movies/1.jpg"},
            {"id":"missing-imdb", "tmdb_id":2, "tmdb_updated_at":"checked", "title_original":"One", "imdb_rating":"", "kinopoisk_rating":7, "poster_local_path":"movies/2.jpg"},
            {"id":"missing-kp", "tmdb_id":3, "tmdb_updated_at":"checked", "title_original":"Two", "imdb_rating":8, "kinopoisk_rating":"", "poster_local_path":"movies/3.jpg"},
            {"id":"confirmed-absent", "tmdb_id":4, "tmdb_updated_at":"checked", "title_original":"None", "imdb_rating":"", "omdb_updated_at":"checked", "kinopoisk_rating":"", "kinopoisk_updated_at":"checked", "poster_local_path":"movies/4.jpg"},
        ]
        with patch.object(tmdb.storage, "movie_refresh_targets", return_value=targets), \
             patch.object(tmdb, "get_omdb_key", return_value=("omdb-key", "test")), \
             patch.object(tmdb, "get_kinopoisk_key", return_value=("kp-key", "test")), \
             patch.object(tmdb.artwork, "is_cached", return_value=True), \
             patch.object(tmdb, "_refresh_movie_target", side_effect=lambda item: item) as fetch:
            result = tmdb.refresh_library()
        self.assertEqual({call.args[0]["id"] for call in fetch.call_args_list}, {"missing-imdb", "missing-kp"})
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["updated"], 2)

    def test_api_recommendations_apply_year_and_translated_genres_before_enrichment(self) -> None:
        credits = {"cast":[
            {"id":1,"title":"Документальный","original_title":"Documentary","release_date":"2024-01-01","genre_ids":[99],"vote_average":9,"vote_count":500},
            {"id":2,"title":"Анимация","original_title":"Animation","release_date":"2023-01-01","genre_ids":[16],"vote_average":9,"vote_count":500},
            {"id":3,"title":"Драма","original_title":"Drama","release_date":"2024-06-01","genre_ids":[18],"vote_average":1,"vote_count":500},
            {"id":4,"title":"Будущее","original_title":"Future","release_date":"2027-01-01","genre_ids":[18],"vote_average":9,"vote_count":500},
        ]}
        details = {"tmdb_id":3,"title_ru":"Драма","title_original":"Drama","year":"2024","duration_minutes":120,"imdb_rating":7,"genres":"драма"}
        interests = lambda _content, role: [{"id":"actor-one","external_id":10,"role":"actor"}] if role == "actor" else []
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",side_effect=interests), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=(set(),set())), \
             patch.object(tmdb,"_get",return_value=credits), \
             patch.object(tmdb,"movie_details",return_value=details) as enrich:
            result = tmdb.recommend_movies({
                "actor_ids":["actor-one"], "date_from":"2020-01-01", "date_to":"2025-12-31",
                "excluded_genres":["Animation","Documentary"], "min_tmdb_rating":9, "limit":20,
            })
        self.assertEqual([item["tmdb_id"] for item in result["items"]], [3])
        self.assertEqual(
            {record["item"]["tmdb_id"] for record in result["filtered_items"]},
            {1, 2},
        )
        enrich.assert_called_once_with(3, "key")

    def test_api_recommendations_require_selected_person_before_provider_calls(self) -> None:
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",return_value=[]), \
             patch.object(tmdb,"_get") as provider:
            with self.assertRaisesRegex(tmdb.TmdbError, "выбрать хотя бы одного"):
                tmdb.recommend_movies({"actor_ids":[], "director_ids":[]})
        provider.assert_not_called()

    def test_cancelled_api_recommendation_returns_completed_cards(self) -> None:
        progress_id = "cancel-after-first-movie"
        credits = {"cast":[
            {"id":1,"title":"Первый","original_title":"First","release_date":"2024-01-01","genre_ids":[18],"vote_average":9,"vote_count":500},
            {"id":2,"title":"Второй","original_title":"Second","release_date":"2024-02-01","genre_ids":[18],"vote_average":8,"vote_count":400},
        ]}
        interests = lambda _content, role: [{"id":"actor-one","external_id":10,"role":"actor"}] if role == "actor" else []

        def details(movie_id: int, _api_key: str) -> dict[str, object]:
            tmdb.recommendation_progress.cancel(progress_id)
            return {
                "tmdb_id":movie_id, "title_ru":"Первый", "title_original":"First",
                "year":"2024", "duration_minutes":120, "imdb_rating":8, "genres":"драма",
            }

        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",side_effect=interests), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=(set(),set())), \
             patch.object(tmdb,"_get",return_value=credits), \
             patch.object(tmdb,"movie_details",side_effect=details) as enrich:
            result = tmdb.recommend_movies({"actor_ids":["actor-one"],"progress_id":progress_id})
        self.assertTrue(result["cancelled"])
        self.assertEqual([item["tmdb_id"] for item in result["items"]], [1])
        enrich.assert_called_once_with(1, "key")

    def test_api_recommendations_filter_by_kinopoisk_rating(self) -> None:
        credits = {"cast":[{"id":3,"title":"Драма","original_title":"Drama","release_date":"2024-06-01","genre_ids":[18],"vote_average":8,"vote_count":500}]}
        details = {"tmdb_id":3,"title_ru":"Драма","title_original":"Drama","year":"2024","duration_minutes":120,"imdb_rating":7,"kinopoisk_rating":6.5,"genres":"драма"}
        interests = lambda _content, role: [{"id":"actor-one","external_id":10,"role":"actor"}] if role == "actor" else []
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",side_effect=interests), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=(set(),set())), \
             patch.object(tmdb,"_get",return_value=credits), \
             patch.object(tmdb,"movie_details",return_value=details):
            result = tmdb.recommend_movies({"actor_ids":["actor-one"],"min_kinopoisk_rating":7})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["filtered_items"][0]["item"]["tmdb_id"], 3)
        self.assertIn("Кинопоиска ниже 7", result["filtered_items"][0]["reason"])

    def test_existing_api_movie_is_not_returned_as_filtered(self) -> None:
        credits = {"cast":[{"id":3,"title":"Драма","original_title":"Drama","release_date":"2024-06-01","genre_ids":[18],"vote_average":8,"vote_count":500}]}
        interests = lambda _content, role: [{"id":"actor-one","external_id":10,"role":"actor"}] if role == "actor" else []
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",side_effect=interests), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=({"3"},{"drama","drama:2024"})), \
             patch.object(tmdb,"_get",return_value=credits), \
             patch.object(tmdb,"movie_details") as details:
            result = tmdb.recommend_movies({"actor_ids":["actor-one"]})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["filtered_items"], [])
        details.assert_not_called()

    def test_existing_movie_detected_after_enrichment_is_not_filtered(self) -> None:
        credits = {"cast":[{"id":3,"title":"Другое имя","original_title":"Different Name","release_date":"2024-06-01","genre_ids":[18],"vote_average":8,"vote_count":500}]}
        enriched = {"tmdb_id":3,"title_ru":"Драма","title_original":"Drama","year":2024,"duration_minutes":80,"imdb_rating":5,"genres":"драма"}
        interests = lambda _content, role: [{"id":"actor-one","external_id":10,"role":"actor"}] if role == "actor" else []
        with patch.object(tmdb,"get_api_key",return_value=("key","test")), \
             patch.object(tmdb.storage,"list_interests",side_effect=interests), \
             patch.object(tmdb.storage,"known_movie_keys",return_value=(set(),{"drama","drama:2024"})), \
             patch.object(tmdb,"_get",return_value=credits), \
             patch.object(tmdb,"movie_details",return_value=enriched):
            result = tmdb.recommend_movies({"actor_ids":["actor-one"],"min_runtime":100,"min_imdb_rating":7})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["filtered_items"], [])


if __name__ == "__main__":
    unittest.main()
