from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import llm, storage


class LlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.db_patch = patch.object(storage, "DB_PATH", self.db)
        self.db_patch.start()
        storage.initialize_database()
        storage.add_item({
            "title_original":"Arrival", "title_ru":"Прибытие", "year":"2016",
            "status":"consumed", "reaction":"like", "directors":"Denis Villeneuve",
        })
        storage.add_item({
            "title_original":"Dune: Part Two", "title_ru":"Дюна: Часть вторая", "year":"2024",
            "status":"backlog", "directors":"Denis Villeneuve",
        })
        storage.add_interest_person({
            "role":"director", "name_original":"David Fincher", "name_ru":"Дэвид Финчер", "tmdb_id":7467,
        })

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp.cleanup()

    def test_configuration_checks_only_active_llm_schemas(self) -> None:
        with patch.object(Path, "exists", return_value=True):
            config = llm.configuration()
        self.assertTrue(config["configured"])
        self.assertEqual(config["provider"], "Codex SDK")

    def test_backlog_moods_schema_uses_codex_supported_keywords(self) -> None:
        schema = json.loads(llm.MOODS_SCHEMA_PATH.read_text(encoding="utf-8"))
        item_ids = schema["properties"]["moods"]["items"]["properties"]["item_ids"]
        self.assertNotIn("uniqueItems", item_ids)

    def test_build_prompt_contains_filters_and_library_context(self) -> None:
        prompt = llm.build_movie_prompt({
            "prompt":"Хочу мрачный детектив", "min_imdb_rating":"7", "min_kinopoisk_rating":"6.8", "year_from":"2020",
            "year_to":"2026", "min_runtime":"110",
        })
        self.assertIn("IMDb rating не ниже 7", prompt)
        self.assertIn("фильмы без IMDb rating не предлагай", prompt)
        self.assertIn("рейтинг Кинопоиска не ниже 6.8", prompt)
        self.assertIn("фильмы без рейтинга Кинопоиска не предлагай", prompt)
        self.assertIn("от 2020 до 2026", prompt)
        self.assertIn("не меньше 110 минут", prompt)
        self.assertIn("Прибытие (Arrival)", prompt)
        self.assertIn("Дюна: Часть вторая (Dune: Part Two)", prompt)
        self.assertIn("Дэвид Финчер (David Fincher)", prompt)
        self.assertIn("их участие не обязательно", prompt)
        self.assertIn("Хочу мрачный детектив", prompt)
        self.assertIn("Верни не более 5 наиболее релевантных", prompt)
        self.assertIn("верни не более 5 фильмов", prompt)
        self.assertIn("JSON-объект с массивом movies", prompt)

    def test_prompt_uses_configurable_random_sample_of_liked_movies(self) -> None:
        movies = [
            {"title_ru":f"Фильм {index}", "title_original":f"Movie {index}", "year":"2020", "status":"consumed", "reaction":"like"}
            for index in range(35)
        ]
        with patch.object(llm.storage, "list_library", return_value=movies), \
             patch.object(llm.storage, "list_interests", return_value=[]), \
             patch.object(llm.random, "sample", side_effect=lambda values, count: values[:count]):
            prompt = llm.build_movie_prompt({"year_to":"2026", "liked_sample_size":"30"})
        liked_section = prompt.split("Фильмы, которые понравились пользователю:", 1)[1].split(
            "Любимые актёры и режиссёры", 1
        )[0]
        self.assertIn("Фильм 29 (Movie 29)", liked_section)
        self.assertNotIn("Фильм 30 (Movie 30)", liked_section)

    def test_prompt_uses_configurable_random_sample_of_people(self) -> None:
        people = [
            {"name_ru":f"Персона {index}", "name_original":f"Person {index}", "role":"actor"}
            for index in range(3)
        ]
        with patch.object(llm.storage, "list_interests", return_value=people), \
             patch.object(llm.random, "sample", side_effect=lambda values, count: values[:count]):
            prompt = llm.build_movie_prompt({"year_to":"2026", "people_sample_size":"2"})
        people_section = prompt.split("Любимые актёры и режиссёры", 1)[1].split(
            "Уже добавленные фильмы", 1
        )[0]
        self.assertIn("Персона 1 (Person 1)", people_section)
        self.assertNotIn("Персона 2 (Person 2)", people_section)

    def test_movie_prompt_explicitly_excludes_trashed_movies(self) -> None:
        trashed = storage.add_item({
            "title_original":"Gone Movie", "title_ru":"Удалённый фильм", "year":"2022",
            "status":"backlog",
        })
        storage.trash_entity({"entity_type":"movie", "id":trashed["id"]})
        prompt = llm.build_movie_prompt({"year_to":"2026"})
        self.assertIn("включая бэклог, просмотренное и корзину", prompt)
        self.assertIn("Удалённый фильм (Gone Movie)", prompt)

    def test_rejects_inverted_year_range(self) -> None:
        with self.assertRaises(llm.LlmError):
                llm.build_movie_prompt({"year_from":"2026", "year_to":"2020"})

    def test_disabled_llm_filters_are_omitted_from_prompt_and_verification(self) -> None:
        payload = {
            "min_imdb_rating":"8", "min_kinopoisk_rating":"8", "year_from":"2020",
            "year_to":"2026", "min_runtime":"150",
            "disabled_filters":["min_imdb_rating", "min_kinopoisk_rating", "min_runtime"],
        }
        prompt = llm.build_movie_prompt(payload)
        self.assertNotIn("IMDb rating не ниже", prompt)
        self.assertNotIn("рейтинг Кинопоиска не ниже", prompt)
        self.assertNotIn("длительность не меньше", prompt)
        self.assertEqual(llm._passes_filters({"year":"2024"}, payload), "")

    def test_prompt_preview_is_the_complete_system_prompt(self) -> None:
        prompt = llm.build_recommendation_prompt({
            "content_type":"movie", "prompt":"Тихая фантастика", "year_to":"2026",
            "context_seed":"stable-preview",
        })
        self.assertTrue(prompt.startswith("You are a personal movie and music recommendation assistant."))
        self.assertIn("mandatory system-level context", prompt)
        self.assertIn("Тихая фантастика", prompt)

    def test_backlog_moods_prompt_maps_only_current_backlog(self) -> None:
        prompt = llm.build_backlog_moods_prompt({"content_type":"movie"})
        self.assertIn("до 15 коротких категорий", prompt)
        self.assertIn("Дюна: Часть вторая", prompt)
        self.assertNotIn('"title_original":"Arrival"', prompt)
        self.assertIn("JSON-объект с массивом moods", prompt)
        self.assertIn("item_ids — все ID объектов списка", prompt)

    def test_backlog_moods_use_dedicated_schema_and_map_valid_backlog_ids(self) -> None:
        backlog_id = storage.list_library(content_type="movie", status="backlog")[0]["id"]
        response = {"moods":[
            {"label":"мрачная научная фантастика", "item_ids":[backlog_id, "unknown-id"]},
            {"label":"Эпическое приключение", "item_ids":[backlog_id]},
            {"label":"мрачная научная фантастика", "item_ids":[backlog_id]},
            {"label":"пустая категория", "item_ids":["unknown-id"]},
        ]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "get_model", return_value="test-model"), \
             patch.object(llm, "_run_codex", return_value=completed) as run:
            result = llm.suggest_backlog_moods({
                "content_type":"movie", "progress_id":"backlog-moods-job",
            })
        self.assertEqual([
            {key:value for key,value in mood.items() if key != "updated_at"}
            for mood in result["moods"]
        ], [
            {"label":"мрачная научная фантастика", "item_ids":[backlog_id]},
            {"label":"Эпическое приключение", "item_ids":[backlog_id]},
        ])
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(run.call_args.args[2], llm.MOODS_SCHEMA_PATH)
        self.assertEqual(storage.list_backlog_moods("movie"), result["moods"])
        progress = llm.recommendation_progress.get("backlog-moods-job")
        self.assertEqual([stage["id"] for stage in progress["stages"]], ["backlog-moods-request"])
        self.assertTrue(progress["complete"])

    def test_recommendation_invokes_isolated_sdk_runner(self) -> None:
        response = {"movies":[{
            "title_ru":"Тест", "title_original":"Test", "year":2024, "comment":"Подходит по настроению",
        }]}
        details = {
            "title_ru":"Тест", "title_original":"Test", "year":"2024", "release_date":"2024-01-01",
            "duration_minutes":120, "imdb_rating":7.5, "tmdb_id":999, "status":"backlog", "reaction":"",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "get_model", return_value="test-model"), \
             patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm.tmdb, "resolve_movie", return_value=999), \
             patch.object(llm.tmdb, "movie_details", return_value=details), \
             patch.object(llm, "_run_codex", return_value=completed) as run:
            result = llm.recommend_movies({
                "prompt":"Научная фантастика", "year_to":"2026", "limit":"1",
                "progress_id":"llm-movie-job",
            })
        self.assertEqual(result["items"][0]["notes"], "Подходит по настроению")
        self.assertEqual(result["items"][0]["tmdb_id"], 999)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["requested"], 1)
        self.assertEqual(result["raw_response"], completed.stdout.strip())
        prompt, model, schema_path, progress_id = run.call_args.args
        self.assertIn("Научная фантастика", prompt)
        self.assertIn("Верни не более 1", prompt)
        self.assertEqual(model, "test-model")
        self.assertEqual(schema_path, llm.SCHEMA_PATH)
        self.assertEqual(progress_id, "llm-movie-job")
        progress = llm.recommendation_progress.get("llm-movie-job")
        self.assertEqual([stage["id"] for stage in progress["stages"]], ["llm-request", "llm-movie-details"])
        self.assertTrue(progress["complete"])

    def test_codex_process_is_terminated_after_cancel_request(self) -> None:
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(llm.subprocess, "Popen", return_value=process), \
             patch.object(llm.recommendation_progress, "is_cancelled", return_value=True):
            result = llm._run_codex("prompt", "model", llm.SCHEMA_PATH, "cancel-job")
        self.assertIsNone(result)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        process.kill.assert_not_called()

    def test_codex_runner_receives_prompt_larger_than_pipe_buffer(self) -> None:
        runner = Path(self.temp.name) / "read_prompt.py"
        runner.write_text("import sys\nprint(len(sys.stdin.read()))\n", encoding="utf-8")
        prompt = "x" * 50_000
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "RUNNER", runner), \
             patch.object(llm.recommendation_progress, "is_cancelled", return_value=False):
            result = llm._run_codex(prompt, "test-model", llm.SCHEMA_PATH, "large-prompt-job")
        self.assertIsNotNone(result)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "50000")

    def test_recommendation_skips_movies_that_fail_verified_filters(self) -> None:
        response = {"movies":[{
            "title_ru":"Короткий фильм", "title_original":"Short Movie", "year":2024, "comment":"Проверка",
        }]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response), "")
        details = {
            "title_ru":"Короткий фильм", "title_original":"Short Movie", "year":"2024",
            "duration_minutes":80, "imdb_rating":7.5, "tmdb_id":1000,
        }
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm.tmdb, "resolve_movie", return_value=1000), \
             patch.object(llm.tmdb, "movie_details", return_value=details), \
             patch.object(llm, "_run_codex", return_value=completed):
            result = llm.recommend_movies({"year_to":"2026", "limit":"1", "min_runtime":"100"})
        self.assertEqual(result["items"], [])
        self.assertIn("длительность меньше 100", result["errors"][0]["error"])
        self.assertEqual(result["filtered_items"][0]["item"]["tmdb_id"], 1000)
        self.assertIn("длительность меньше 100", result["filtered_items"][0]["reason"])

    def test_existing_movie_is_not_returned_as_filtered(self) -> None:
        candidate = {
            "title_ru":"Дюна: Часть вторая", "title_original":"Dune: Part Two",
            "year":2024, "comment":"Уже добавлен",
        }
        with patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm, "_enrich_candidate") as enrich:
            items, filtered_items, errors = llm._enrich_movies([candidate], {})
        self.assertEqual(items, [])
        self.assertEqual(filtered_items, [])
        self.assertIn("уже есть", errors[0]["error"])
        enrich.assert_not_called()

    def test_enriched_existing_movie_stays_out_of_filtered_results(self) -> None:
        candidate = {
            "title_ru":"Песчаная планета", "title_original":"Sand Planet",
            "year":2024, "comment":"Другое название",
        }
        existing_details = {
            "title_ru":"Дюна: Часть вторая", "title_original":"Dune: Part Two",
            "year":2024, "tmdb_id":900, "duration_minutes":80,
        }
        with patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm, "_enrich_candidate", return_value=existing_details):
            items, filtered_items, errors = llm._enrich_movies(
                [candidate], {"min_runtime":"100"},
            )
        self.assertEqual(items, [])
        self.assertEqual(filtered_items, [])
        self.assertIn("уже есть", errors[0]["error"])

    def test_missing_kinopoisk_rating_does_not_discard_tmdb_card(self) -> None:
        item = {"imdb_rating":7.5, "kinopoisk_rating":None, "year":"2024", "duration_minutes":120}
        reason = llm._passes_filters(item, {
            "min_imdb_rating":"7", "min_kinopoisk_rating":"8", "year_from":"2020",
            "year_to":"2026", "min_runtime":"100",
        })
        self.assertEqual(reason, "")

    def test_available_kinopoisk_rating_is_verified(self) -> None:
        item = {"imdb_rating":7.5, "kinopoisk_rating":6.5, "year":"2024", "duration_minutes":120}
        reason = llm._passes_filters(item, {
            "min_imdb_rating":"7", "min_kinopoisk_rating":"7", "year_from":"2020",
            "year_to":"2026", "min_runtime":"100",
        })
        self.assertIn("Кинопоиска ниже 7", reason)

    def test_tmdb_failure_does_not_create_recommendation_card(self) -> None:
        response = {"movies":[{
            "title_ru":"Тест", "title_original":"Test", "year":2024, "comment":"Проверка",
        }]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm.tmdb, "resolve_movie", side_effect=llm.tmdb.TmdbError("TMDB: HTTP 429")), \
             patch.object(llm, "_run_codex", return_value=completed):
            result = llm.recommend_movies({"year_to":"2026", "limit":"1"})
        self.assertEqual(result["items"], [])
        self.assertIn("TMDB", result["errors"][0]["error"])
        self.assertEqual(result["filtered_items"][0]["item"]["title_original"], "Test")

    def test_rejects_unstructured_model_response(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "1. Тест (Test)", "")
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "_run_codex", return_value=completed):
            with self.assertRaises(llm.LlmError):
                llm.recommend_movies({"year_to":"2026"})

    def test_accepts_empty_structured_response(self) -> None:
        completed = subprocess.CompletedProcess([], 0, '{"movies":[]}', "")
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm.tmdb, "get_api_key", return_value=("test-key", "test")), \
             patch.object(llm, "_run_codex", return_value=completed):
            result = llm.recommend_movies({"year_to":"2026", "limit":"3"})
        self.assertEqual(result["items"], [])
        self.assertEqual(result["errors"], [])

    def test_album_prompt_contains_liked_albums_artists_and_backlog(self) -> None:
        storage.add_music_artist({"content_type":"music", "name":"The Smile", "mbid":"artist-1"})
        storage.add_item({
            "content_type":"music", "title_original":"Wall of Eyes", "title_ru":"Wall of Eyes",
            "artists":"The Smile", "year":"2024", "status":"consumed", "reaction":"like",
            "release_group_mbid":"rg-liked",
        })
        storage.add_item({
            "content_type":"music", "title_original":"Cutouts", "title_ru":"Cutouts",
            "artists":"The Smile", "year":"2024", "status":"backlog",
            "release_group_mbid":"rg-backlog",
        })
        prompt = llm.build_album_prompt({"prompt":"Хочу арт-рок", "year_from":"2023", "year_to":"2026"})
        self.assertIn("Wall of Eyes", prompt)
        self.assertIn("Cutouts", prompt)
        self.assertIn("The Smile", prompt)
        self.assertIn("Хочу арт-рок", prompt)
        self.assertIn("массивом albums", prompt)

    def test_album_prompt_explicitly_excludes_trashed_albums(self) -> None:
        trashed = storage.add_item({
            "content_type":"music", "title_original":"Gone Album", "title_ru":"Gone Album",
            "artists":"Artist", "year":"2024", "status":"backlog", "release_group_mbid":"rg-gone",
        })
        storage.trash_entity({"entity_type":"album", "id":trashed["id"]})
        prompt = llm.build_album_prompt({"year_to":"2026"})
        self.assertIn("включая бэклог, прослушанное и корзину", prompt)
        self.assertIn("Gone Album — Artist, 2024", prompt)

    def test_album_recommendation_is_enriched_through_musicbrainz(self) -> None:
        response = {"albums":[{"title":"Album","artist":"Artist","year":2024,"comment":"Подходит"}]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        details = {
            "content_type":"music", "title_original":"Album", "title_ru":"Album",
            "artists":"Artist", "year":2024, "release_group_mbid":"rg-1", "track_count":9,
        }
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "get_model", return_value="test-model"), \
             patch.object(llm, "_run_codex", return_value=completed) as run, \
             patch.object(llm.musicbrainz, "search_album", return_value=details), \
             patch.object(llm.musicbrainz, "album_details", return_value=details) as album_details, \
             patch.object(llm.listenbrainz, "enrich_albums", side_effect=lambda items: items) as popularity:
            result = llm.recommend_albums({"year_from":"2023", "year_to":"2026", "limit":"1"})
        self.assertEqual(result["items"][0]["release_group_mbid"], "rg-1")
        self.assertEqual(result["items"][0]["notes"], "Подходит")
        self.assertEqual(result["raw_response"], completed.stdout.strip())
        self.assertEqual(run.call_args.args[2], llm.ALBUM_SCHEMA_PATH)
        album_details.assert_called_once_with("rg-1", fetch_popularity=False)
        popularity.assert_called_once_with(result["items"])

    def test_people_prompts_include_existing_and_trashed_people(self) -> None:
        actor = storage.add_interest_person({
            "role":"actor", "name_original":"Tilda Swinton", "name_ru":"Тильда Суинтон", "tmdb_id":3063,
        })
        storage.trash_entity({"entity_type":"person", "entity_id":actor["id"], "role":"actor"})
        artist = storage.add_music_artist({"content_type":"music", "name":"Portishead", "mbid":"artist-portishead"})
        storage.trash_entity({"entity_type":"music_artist", "entity_id":artist["id"]})

        movie_prompt = llm.build_people_prompt({"content_type":"movie", "prompt":"Необычные актёры", "limit":"5"})
        music_prompt = llm.build_people_prompt({"content_type":"music", "prompt":"Мрачный трип-хоп"})

        self.assertIn("Дэвид Финчер (David Fincher)", movie_prompt)
        self.assertIn("Тильда Суинтон (Tilda Swinton)", movie_prompt)
        self.assertIn("включая корзину", movie_prompt)
        self.assertIn("не более 5", movie_prompt)
        self.assertIn("Portishead", music_prompt)
        self.assertIn("Мрачный трип-хоп", music_prompt)

    def test_people_prompt_applies_role_and_context_limit(self) -> None:
        storage.add_interest_person({
            "role":"actor", "name_original":"Tilda Swinton", "name_ru":"Тильда Суинтон",
            "tmdb_id":3063,
        })
        storage.add_interest_person({
            "role":"actor", "name_original":"Song Kang-ho", "name_ru":"Сон Кан-хо",
            "tmdb_id":20738,
        })
        with patch.object(llm.random, "sample", side_effect=lambda values, count: values[:count]):
            prompt = llm.build_people_prompt({
                "content_type":"movie", "role":"actor", "people_sample_size":"1",
                "prompt":"Характерные исполнители",
            })
        liked_section = prompt.split("Любимые актёры", 1)[1].split("Уже добавленные актёры", 1)[0]
        self.assertIn("Задача: порекомендуй актёров", prompt)
        self.assertIn("поле role должно быть равно actor", prompt)
        self.assertIn("Сон Кан-хо (Song Kang-ho)", liked_section)
        self.assertNotIn("Тильда Суинтон (Tilda Swinton)", liked_section)
        self.assertNotIn("Дэвид Финчер (David Fincher)", prompt)

    def test_people_response_must_match_selected_role(self) -> None:
        response = json.dumps({"people":[{
            "name_original":"Jane Campion", "name_ru":"Джейн Кэмпион",
            "role":"director", "comment":"Авторское кино",
        }]}, ensure_ascii=False)
        with self.assertRaises(llm.LlmError):
            llm._parse_people_response(response, "movie", "actor")

    def test_movie_person_recommendation_is_resolved_through_tmdb(self) -> None:
        response = {"people":[{
            "name_original":"Jane Campion", "name_ru":"Джейн Кэмпион",
            "role":"director", "comment":"Подходит по авторскому стилю",
        }]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        details = {
            "name_original":"Jane Campion", "name_ru":"Джейн Кэмпион",
            "role":"director", "tmdb_id":100,
        }
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "get_model", return_value="test-model"), \
             patch.object(llm, "_run_codex", return_value=completed) as run, \
             patch.object(llm.tmdb, "resolve_person_input", return_value=details) as resolve:
            result = llm.recommend_people({
                "content_type":"movie", "prompt":"Женщины-режиссёры", "limit":"5", "progress_id":"people-movie-job",
            })
        self.assertEqual(result["items"][0]["tmdb_id"], 100)
        self.assertEqual(result["items"][0]["notes"], "Подходит по авторскому стилю")
        self.assertEqual(result["requested"], 5)
        resolve.assert_called_once()
        self.assertEqual(run.call_args.args[2], llm.PERSON_SCHEMA_PATH)
        progress = llm.recommendation_progress.get("people-movie-job")
        self.assertEqual([stage["id"] for stage in progress["stages"]], ["llm-request", "tmdb-people"])
        self.assertTrue(progress["complete"])

    def test_music_artist_recommendation_is_resolved_through_musicbrainz(self) -> None:
        response = {"people":[{
            "name_original":"Massive Attack", "name_ru":"Massive Attack",
            "role":"artist", "comment":"Атмосферный трип-хоп",
        }]}
        completed = subprocess.CompletedProcess([], 0, json.dumps(response, ensure_ascii=False), "")
        details = {
            "content_type":"music", "name":"Massive Attack", "name_original":"Massive Attack",
            "name_ru":"Massive Attack", "mbid":"artist-massive-attack", "artist_type":"Group",
        }
        with patch.object(llm, "VENV_PYTHON", Path(sys.executable)), \
             patch.object(llm, "_run_codex", return_value=completed), \
             patch.object(llm.musicbrainz, "resolve_artist_input", return_value=details) as resolve, \
             patch.object(
                 llm.musicbrainz, "enrich_artist_artwork",
                 side_effect=lambda artist: {
                     **artist, "profile_url":"https://fanart.test/massive-attack.jpg",
                 },
             ) as enrich:
            result = llm.recommend_people({
                "content_type":"music", "prompt":"Трип-хоп", "progress_id":"people-music-job",
            })
        self.assertEqual(result["items"][0]["mbid"], "artist-massive-attack")
        self.assertEqual(result["items"][0]["profile_url"], "https://fanart.test/massive-attack.jpg")
        self.assertEqual(result["items"][0]["role"], "artist")
        resolve.assert_called_once_with({"content_type":"music", "name":"Massive Attack"})
        enrich.assert_called_once()
        progress = llm.recommendation_progress.get("people-music-job")
        self.assertEqual(
            [stage["id"] for stage in progress["stages"]],
            ["llm-request", "musicbrainz-people", "fanart-people"],
        )
        self.assertEqual(
            [stage["label"] for stage in progress["stages"]],
            [
                "Codex · запрос к LLM",
                "MusicBrainz · карточки исполнителей",
                "fanart.tv · фотографии исполнителей",
            ],
        )
        self.assertTrue(all(stage["complete"] for stage in progress["stages"]))


if __name__ == "__main__":
    unittest.main()
