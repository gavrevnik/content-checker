from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

from app import listenbrainz, musicbrainz, recommendation_progress, storage, tmdb
from app.storage import ROOT


RUNNER = ROOT / "scripts" / "run_codex_recommendation.py"
SCHEMA_PATH = ROOT / "app" / "movie_recommendations.schema.json"
ALBUM_SCHEMA_PATH = ROOT / "app" / "album_recommendations.schema.json"
PERSON_SCHEMA_PATH = ROOT / "app" / "person_recommendations.schema.json"
MOODS_SCHEMA_PATH = ROOT / "app" / "backlog_moods.schema.json"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
DEFAULT_MODEL = "gpt-5.6-terra"
BASE_INSTRUCTIONS = """You are a personal movie and music recommendation assistant.
Follow the supplied Russian-language task exactly. Do not inspect files, execute shell commands,
modify anything, or ask follow-up questions. Use only the context included in the prompt and your
knowledge. Return only JSON that strictly matches the supplied output schema. Do not wrap it in
Markdown, add prose outside the JSON, or change the field names.
"""
class LlmError(RuntimeError):
    pass


def _run_codex(
    prompt: str, model: str, schema_path: Path, progress_id: object,
) -> subprocess.CompletedProcess[str] | None:
    command = [str(VENV_PYTHON), str(RUNNER), "--model", model, "--schema", str(schema_path)]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as input_stream, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_stream, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_stream:
        input_stream.write(prompt)
        input_stream.seek(0)
        try:
            process = subprocess.Popen(
                command, stdin=input_stream, stdout=output_stream, stderr=error_stream,
                text=True, cwd=ROOT,
            )
        except OSError as error:
            raise LlmError(f"Не удалось запустить Codex SDK: {error}") from error
        deadline = time.monotonic() + 300
        while process.poll() is None:
            if recommendation_progress.is_cancelled(progress_id):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return None
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise LlmError("Codex не успел ответить за 5 минут")
            time.sleep(0.25)
        output_stream.seek(0)
        error_stream.seek(0)
        return subprocess.CompletedProcess(
            command, process.returncode, output_stream.read(), error_stream.read(),
        )


def get_model() -> str:
    return (
        os.environ.get("CODEX_RECOMMENDATION_MODEL", "").strip()
        or tmdb._local_secrets().get("CODEX_RECOMMENDATION_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def configuration() -> dict[str, Any]:
    return {
        "configured": all(path.exists() for path in (
            VENV_PYTHON, RUNNER, SCHEMA_PATH, ALBUM_SCHEMA_PATH, PERSON_SCHEMA_PATH,
            MOODS_SCHEMA_PATH,
        )),
        "provider": "Codex SDK",
        "model": get_model(),
        "auth": "ChatGPT/Codex local login",
    }


def _number(payload: dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    raw = payload.get(key)
    try:
        value = default if raw in (None, "") else float(raw)
    except (TypeError, ValueError) as error:
        raise LlmError(f"Invalid value for {key}") from error
    if not minimum <= value <= maximum:
        raise LlmError(f"{key} must be between {minimum:g} and {maximum:g}")
    return value


def _optional_number(
    payload: dict[str, Any], key: str, default: float, minimum: float, maximum: float,
) -> float | None:
    disabled = payload.get("disabled_filters", [])
    if not isinstance(disabled, list):
        raise LlmError("disabled_filters must be an array")
    if key in {str(value) for value in disabled}:
        return None
    return _number(payload, key, default, minimum, maximum)


def _movie_line(item: dict[str, Any]) -> str:
    names = str(item.get("title_ru") or item.get("title_original") or "Без названия")
    original = str(item.get("title_original") or "")
    if original and original.casefold() != names.casefold():
        names += f" ({original})"
    details = [str(item.get("year") or ""), str(item.get("directors") or "")]
    suffix = ", ".join(value for value in details if value)
    return f"- {names}{f' — {suffix}' if suffix else ''}"


def _person_line(person: dict[str, Any]) -> str:
    localized = str(person.get("name_ru") or person.get("name_original") or "")
    original = str(person.get("name_original") or "")
    name = localized if not original or localized.casefold() == original.casefold() else f"{localized} ({original})"
    role = "режиссёр" if person.get("role") == "director" else "актёр"
    return f"- {name} — {role}"


def _section(title: str, lines: list[str]) -> str:
    return f"{title}:\n" + ("\n".join(lines) if lines else "- нет данных")


def _sample_context(items: list[dict[str, Any]], count: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    sample_size = min(count, len(items))
    seed = str(payload.get("context_seed") or "").strip()
    if seed:
        return random.Random(seed).sample(items, sample_size)
    return random.sample(items, sample_size)


def build_movie_prompt(payload: dict[str, Any]) -> str:
    current_year = date.today().year
    imdb_rating = _optional_number(payload, "min_imdb_rating", 7, 0, 10)
    kinopoisk_rating = _optional_number(payload, "min_kinopoisk_rating", 0, 0, 10)
    year_from_raw = _optional_number(payload, "year_from", 2020, 1888, current_year + 2)
    year_to_raw = _optional_number(payload, "year_to", current_year, 1888, current_year + 2)
    runtime_raw = _optional_number(payload, "min_runtime", 100, 0, 600)
    year_from = int(year_from_raw) if year_from_raw is not None else None
    year_to = int(year_to_raw) if year_to_raw is not None else None
    min_runtime = int(runtime_raw) if runtime_raw is not None else None
    limit_raw = _optional_number(payload, "limit", 5, 1, 20)
    limit = int(limit_raw) if limit_raw is not None else None
    if year_from is not None and year_to is not None and year_from > year_to:
        raise LlmError("Начальный год не может быть больше конечного")
    user_prompt = str(payload.get("prompt") or "").strip()
    if len(user_prompt) > 8000:
        raise LlmError("Пользовательский промпт слишком длинный")

    movies = storage.list_library(content_type="movie")
    liked_movies = [item for item in movies if item.get("status") == "consumed" and item.get("reaction") == "like"]
    liked_sample_size = int(_number(
        payload, "liked_sample_size", len(liked_movies), 0, max(10_000, len(liked_movies)),
    ))
    liked = [_movie_line(item) for item in _sample_context(liked_movies, liked_sample_size, payload)]
    existing_movies = storage.list_library(content_type="movie", include_trashed=True)
    exclusions = [_movie_line(item) for item in existing_movies]
    interest_people = storage.list_interests("movie")
    people_sample_size = int(_number(
        payload, "people_sample_size", len(interest_people), 0, max(10_000, len(interest_people)),
    ))
    people = [
        _person_line(person)
        for person in _sample_context(interest_people, people_sample_size, payload)
    ]
    user_block = user_prompt or "Дополнительных пожеланий нет — подбери наиболее релевантные фильмы по профилю вкусов."

    filters = []
    if imdb_rating is not None:
        filters.append(
            f"- у фильма обязательно должен быть опубликованный IMDb rating не ниже {imdb_rating:g}; "
            "фильмы без IMDb rating не предлагай"
        )
    if kinopoisk_rating is not None and kinopoisk_rating > 0:
        filters.append(
            f"- у фильма обязательно должен быть опубликованный рейтинг Кинопоиска не ниже {kinopoisk_rating:g}; "
            "фильмы без рейтинга Кинопоиска не предлагай"
        )
    if year_from is not None and year_to is not None:
        filters.append(f"- год выпуска от {year_from} до {year_to} включительно")
    elif year_from is not None:
        filters.append(f"- год выпуска не раньше {year_from}")
    elif year_to is not None:
        filters.append(f"- год выпуска не позже {year_to}")
    if min_runtime is not None:
        filters.append(f"- длительность не меньше {min_runtime} минут")
    if limit is not None:
        filters.append(f"- верни не более {limit} фильмов, соответствующих всем включённым условиям")

    return "\n\n".join([
        "Задача: порекомендуй фильмы с русским и оригинальным названием, соблюдая условия ниже. "
        f"Верни не более {limit if limit is not None else 10} наиболее релевантных позиций. "
        "Не включай фильмы из бэклога или просмотренные фильмы. Для каждой позиции дай короткий содержательный "
        "комментарий на русском: почему фильм подходит именно этому пользователю. Все включённые условия являются жёсткими: "
        "проверь их до формирования ответа и не предлагай фильм, если не можешь уверенно подтвердить соответствующее значение.",
        _section("Обязательные фильтры", filters),
        f"Свободный запрос пользователя:\n{user_block}",
        _section("Фильмы, которые понравились пользователю", liked),
        _section(
            "Любимые актёры и режиссёры (используй как мягкий сигнал вкуса; их участие не обязательно, если пользователь прямо этого не потребовал)",
            people,
        ),
        _section(
            "Уже добавленные фильмы, включая бэклог, просмотренное и корзину — не рекомендовать их повторно",
            exclusions,
        ),
        "Формат ответа задаётся JSON Schema. Верни только JSON-объект с массивом movies. У каждого фильма обязательны "
        "поля title_ru (официальное или наиболее употребимое русское название), title_original (оригинальное название), "
        "year (целое число) и comment (комментарий на русском). Не добавляй вступление, Markdown, вопросы пользователю "
        "или фильмы, нарушающие обязательные фильтры.",
    ])


def _parse_response(raw: str) -> list[dict[str, Any]]:
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LlmError(f"Codex вернул ответ, который не удалось разобрать как JSON: {error}") from error
    movies = response.get("movies") if isinstance(response, dict) else None
    if not isinstance(movies, list):
        raise LlmError("Codex вернул JSON без массива movies")
    parsed: list[dict[str, Any]] = []
    for index, movie in enumerate(movies, start=1):
        if not isinstance(movie, dict):
            raise LlmError(f"Позиция {index} в ответе Codex имеет неверный формат")
        title_ru = str(movie.get("title_ru") or "").strip()
        title_original = str(movie.get("title_original") or "").strip()
        comment = str(movie.get("comment") or "").strip()
        try:
            year = int(movie.get("year"))
        except (TypeError, ValueError) as error:
            raise LlmError(f"У позиции {index} отсутствует корректный год") from error
        if not title_ru or not title_original or not comment or not 1888 <= year <= 2100:
            raise LlmError(f"У позиции {index} не заполнены обязательные поля")
        parsed.append({"title_ru": title_ru, "title_original": title_original, "year": year, "comment": comment})
    return parsed


def _known_title(candidate: dict[str, Any], known_titles: set[str]) -> bool:
    year = str(candidate.get("year") or "")
    for field in ("title_original", "title_ru"):
        normalized = storage._normalized(str(candidate.get(field) or ""))
        if normalized and (normalized in known_titles or f"{normalized}:{year}" in known_titles):
            return True
    return False


def _enrich_candidate(candidate: dict[str, Any], api_key: str) -> dict[str, Any]:
    tmdb_id = tmdb.resolve_movie(candidate["title_original"], candidate["title_ru"], candidate["year"], api_key)
    details = tmdb.movie_details(tmdb_id, api_key)
    details.update({
        "notes": candidate["comment"],
        "llm_comment": candidate["comment"],
        "raw_data": {
            "title_ru": candidate["title_ru"],
            "title_original": candidate["title_original"],
            "year": candidate["year"],
            "notes": candidate["comment"],
        },
    })
    return details


def _passes_filters(item: dict[str, Any], payload: dict[str, Any]) -> str:
    current_year = date.today().year
    min_imdb = _optional_number(payload, "min_imdb_rating", 7, 0, 10)
    min_kinopoisk = _optional_number(payload, "min_kinopoisk_rating", 0, 0, 10)
    year_from_raw = _optional_number(payload, "year_from", 2020, 1888, current_year + 2)
    year_to_raw = _optional_number(payload, "year_to", current_year, 1888, current_year + 2)
    runtime_raw = _optional_number(payload, "min_runtime", 100, 0, 600)
    year_from = int(year_from_raw) if year_from_raw is not None else None
    year_to = int(year_to_raw) if year_to_raw is not None else None
    min_runtime = int(runtime_raw) if runtime_raw is not None else None
    rating = item.get("imdb_rating")
    kinopoisk_rating = item.get("kinopoisk_rating")
    year = str(item.get("year") or "")
    runtime = item.get("duration_minutes")
    if min_imdb is not None and (rating in (None, "") or float(rating) < min_imdb):
        return f"IMDb rating ниже {min_imdb:g} или недоступен"
    if min_kinopoisk is not None and min_kinopoisk > 0:
        # Kinopoisk constrains the model prompt, but a missing companion rating
        # must not hide an otherwise valid TMDB recommendation card.
        if kinopoisk_rating not in (None, "") and float(kinopoisk_rating) < min_kinopoisk:
            return f"рейтинг Кинопоиска ниже {min_kinopoisk:g}"
    if year_from is not None and (not year.isdigit() or int(year) < year_from):
        return f"год выпуска раньше {year_from}"
    if year_to is not None and (not year.isdigit() or int(year) > year_to):
        return f"год выпуска позже {year_to}"
    if min_runtime is not None and (runtime in (None, "") or int(runtime) < min_runtime):
        return f"длительность меньше {min_runtime} мин. или недоступна"
    return ""


def _movie_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_type": "movie",
        "title_ru": str(candidate.get("title_ru") or candidate.get("title_original") or "Без названия"),
        "title_original": str(candidate.get("title_original") or candidate.get("title_ru") or "Без названия"),
        "year": candidate.get("year"),
        "notes": str(candidate.get("comment") or candidate.get("notes") or ""),
        "source": "llm",
        "raw_data": candidate,
    }


def _album_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    title = str(candidate.get("title") or candidate.get("title_original") or "Без названия")
    return {
        "content_type": "music",
        "title_ru": title,
        "title_original": title,
        "artists": str(candidate.get("artist") or candidate.get("artists") or "Неизвестный исполнитель"),
        "year": candidate.get("year"),
        "notes": str(candidate.get("comment") or candidate.get("notes") or ""),
        "source": "llm",
        "raw_data": candidate,
    }


def _enrich_movies(
    candidates: list[dict[str, Any]], payload: dict[str, Any], progress_id: object = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    api_key, _ = tmdb.get_api_key()
    if not api_key:
        raise LlmError("TMDB_API_KEY не настроен: невозможно уточнить рекомендации")
    known_ids, known_titles = storage.known_movie_keys()
    errors: list[dict[str, str]] = []
    filtered_items: list[dict[str, Any]] = []
    results: list[tuple[int, dict[str, Any]]] = []

    eligible: list[tuple[int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        if recommendation_progress.is_cancelled(progress_id):
            break
        label = f"{candidate['title_ru']} ({candidate['title_original']}, {candidate['year']})"
        if _known_title(candidate, known_titles):
            errors.append({"title": label, "error": "уже есть в библиотеке или корзине"})
            recommendation_progress.advance(progress_id, "llm-movie-details")
        else:
            eligible.append((index, candidate))

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_enrich_candidate, candidate, api_key): (index, candidate) for index, candidate in eligible}
        for future in as_completed(futures):
            if future.cancelled():
                continue
            index, candidate = futures[future]
            label = f"{candidate['title_ru']} ({candidate['title_original']}, {candidate['year']})"
            try:
                item = future.result()
                if str(item.get("tmdb_id") or "") in known_ids or _known_title(item, known_titles):
                    errors.append({"title": label, "error": "уже есть в библиотеке или корзине"})
                    continue
                reason = _passes_filters(item, payload)
                if reason:
                    errors.append({"title": label, "error": reason})
                    filtered_items.append({"item": item, "reason": reason})
                    continue
                results.append((index, item))
            except Exception as error:
                reason = str(error)
                errors.append({"title": label, "error": reason})
                filtered_items.append({"item": _movie_candidate_payload(candidate), "reason": reason})
            finally:
                recommendation_progress.advance(progress_id, "llm-movie-details")
            if recommendation_progress.is_cancelled(progress_id):
                for pending in futures:
                    if not pending.running() and not pending.done():
                        pending.cancel()

    items: list[dict[str, Any]] = []
    seen_tmdb_ids: set[str] = set()
    for _, item in sorted(results, key=lambda row: row[0]):
        tmdb_id = str(item.get("tmdb_id") or "")
        if tmdb_id and tmdb_id in seen_tmdb_ids:
            filtered_items.append({"item": item, "reason": "дубликат в текущей выдаче"})
            continue
        if tmdb_id:
            seen_tmdb_ids.add(tmdb_id)
        items.append(item)
    return items, filtered_items, errors


def recommend_movies(payload: dict[str, Any]) -> dict[str, Any]:
    progress_id = payload.get("progress_id")
    recommendation_progress.start(
        progress_id, 1, stage_id="llm-request", label="Codex · запрос к LLM", unit="запросов",
    )
    try:
        prompt = build_recommendation_prompt({**payload, "content_type": "movie"})
        if not VENV_PYTHON.exists():
            raise LlmError("Codex SDK не установлен. Выполните: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        model = get_model()
        completed = _run_codex(prompt, model, SCHEMA_PATH, progress_id)
        if completed is None:
            return {
                "items": [], "filtered_items": [], "errors": [], "model": model,
                "requested": 0, "received": 0, "raw_response": "", "cancelled": True,
            }
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown Codex SDK error"
            raise LlmError(f"Codex SDK: {message[-1200:]}")
        recommendation_progress.finish_stage(progress_id, "llm-request")
        answer = completed.stdout.strip()
        if not answer:
            raise LlmError("Codex вернул пустой ответ")
        candidates = _parse_response(answer)
        limit_raw = _optional_number(payload, "limit", 5, 1, 20)
        requested = int(limit_raw) if limit_raw is not None else 10
        selected = candidates[:requested]
        recommendation_progress.set_stage(
            progress_id, "llm-movie-details", "TMDB / OMDb / Кинопоиск · карточки",
            len(selected), "фильмов",
        )
        items, filtered_items, errors = _enrich_movies(selected, payload, progress_id)
        recommendation_progress.finish_stage(progress_id, "llm-movie-details")
        return {
            "items": items, "filtered_items": filtered_items, "errors": errors,
            "model": model, "requested": requested, "received": len(candidates),
            "raw_response": answer,
            "cancelled": recommendation_progress.is_cancelled(progress_id),
        }
    finally:
        recommendation_progress.finish(progress_id)


def _album_line(item: dict[str, Any]) -> str:
    title = str(item.get("title_ru") or item.get("title_original") or "Без названия")
    original = str(item.get("title_original") or "")
    if original and original.casefold() != title.casefold():
        title += f" ({original})"
    details = [str(item.get("artists") or ""), str(item.get("year") or "")]
    suffix = ", ".join(value for value in details if value)
    return f"- {title}{f' — {suffix}' if suffix else ''}"


def _artist_line(person: dict[str, Any]) -> str:
    name = str(person.get("name_original") or person.get("name_ru") or "")
    return f"- {name} — исполнитель"


def build_album_prompt(payload: dict[str, Any]) -> str:
    current_year = date.today().year
    year_from_raw = _optional_number(payload, "year_from", 2023, 1900, current_year + 2)
    year_to_raw = _optional_number(payload, "year_to", current_year, 1900, current_year + 2)
    year_from = int(year_from_raw) if year_from_raw is not None else None
    year_to = int(year_to_raw) if year_to_raw is not None else None
    if year_from is not None and year_to is not None and year_from > year_to:
        raise LlmError("Начальный год не может быть больше конечного")
    limit_raw = _optional_number(payload, "limit", 5, 1, 20)
    limit = int(limit_raw) if limit_raw is not None else 10
    user_prompt = str(payload.get("prompt") or "").strip()
    if len(user_prompt) > 8000:
        raise LlmError("Пользовательский промпт слишком длинный")
    albums = storage.list_library(content_type="music")
    liked_albums = [
        item for item in albums
        if item.get("status") == "consumed" and item.get("reaction") == "like"
    ]
    liked_sample_size = int(_number(
        payload, "liked_sample_size", len(liked_albums), 0, max(10_000, len(liked_albums)),
    ))
    liked = [_album_line(item) for item in _sample_context(liked_albums, liked_sample_size, payload)]
    existing_albums = storage.list_library(content_type="music", include_trashed=True)
    exclusions = [_album_line(item) for item in existing_albums]
    interest_artists = storage.list_music_artists()
    people_sample_size = int(_number(
        payload, "people_sample_size", len(interest_artists), 0, max(10_000, len(interest_artists)),
    ))
    artists = [
        _artist_line(item)
        for item in _sample_context(interest_artists, people_sample_size, payload)
    ]
    filters = []
    if year_from is not None and year_to is not None:
        filters.append(f"- первый выпуск альбома с {year_from} по {year_to} год включительно")
    elif year_from is not None:
        filters.append(f"- первый выпуск альбома не раньше {year_from} года")
    elif year_to is not None:
        filters.append(f"- первый выпуск альбома не позже {year_to} года")
    filters.append(f"- верни не более {limit} альбомов")
    user_block = user_prompt or "Дополнительных пожеланий нет — подбери альбомы по профилю вкусов."
    return "\n\n".join([
        "Задача: порекомендуй музыкальные альбомы, соблюдая все условия ниже. Не включай синглы и EP, "
        "альбомы из бэклога или уже прослушанные альбомы. Для каждой позиции дай короткий содержательный "
        "комментарий на русском, почему она подходит этому пользователю.",
        _section("Обязательные фильтры", filters),
        f"Свободный запрос пользователя:\n{user_block}",
        _section("Альбомы, которые понравились пользователю", liked),
        _section("Любимые исполнители (мягкий сигнал; рекомендации не обязаны ограничиваться ими)", artists),
        _section(
            "Уже добавленные альбомы, включая бэклог, прослушанное и корзину — не рекомендовать их повторно",
            exclusions,
        ),
        "Формат задаётся JSON Schema. Верни только JSON-объект с массивом albums. Для каждого альбома "
        "обязательны title (каноническое название), artist (основной исполнитель), year (целое число) и "
        "comment (комментарий на русском). Не добавляй Markdown или вступление.",
    ])


def _parse_album_response(raw: str) -> list[dict[str, Any]]:
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LlmError(f"Codex вернул ответ, который не удалось разобрать как JSON: {error}") from error
    albums = response.get("albums") if isinstance(response, dict) else None
    if not isinstance(albums, list):
        raise LlmError("Codex вернул JSON без массива albums")
    parsed: list[dict[str, Any]] = []
    for index, album in enumerate(albums, start=1):
        if not isinstance(album, dict):
            raise LlmError(f"Позиция {index} в ответе Codex имеет неверный формат")
        title = str(album.get("title") or "").strip()
        artist = str(album.get("artist") or "").strip()
        comment = str(album.get("comment") or "").strip()
        try:
            year = int(album.get("year"))
        except (TypeError, ValueError) as error:
            raise LlmError(f"У позиции {index} отсутствует корректный год") from error
        if not title or not artist or not comment or not 1900 <= year <= 2100:
            raise LlmError(f"У позиции {index} не заполнены обязательные поля")
        parsed.append({"title": title, "artist": artist, "year": year, "comment": comment})
    return parsed


def recommend_albums(payload: dict[str, Any]) -> dict[str, Any]:
    progress_id = payload.get("progress_id")
    recommendation_progress.start(
        progress_id, 1, stage_id="llm-request", label="Codex · запрос к LLM", unit="запросов",
    )
    try:
        prompt = build_recommendation_prompt({**payload, "content_type": "music"})
        if not VENV_PYTHON.exists():
            raise LlmError(
                "Codex SDK не установлен. Выполните: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
            )
        model = get_model()
        completed = _run_codex(prompt, model, ALBUM_SCHEMA_PATH, progress_id)
        if completed is None:
            return {
                "items": [], "filtered_items": [], "errors": [], "model": model,
                "requested": 0, "received": 0, "provider_warnings": [], "raw_response": "", "cancelled": True,
            }
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown Codex SDK error"
            raise LlmError(f"Codex SDK: {message[-1200:]}")
        recommendation_progress.finish_stage(progress_id, "llm-request")
        answer = completed.stdout.strip()
        if not answer:
            raise LlmError("Codex вернул пустой ответ")
        candidates = _parse_album_response(answer)
        limit_raw = _optional_number(payload, "limit", 5, 1, 20)
        requested = int(limit_raw) if limit_raw is not None else 10
        selected = candidates[:requested]
        recommendation_progress.set_stage(
            progress_id, "llm-album-details", "MusicBrainz / Cover Art Archive · карточки",
            len(selected), "альбомов",
        )
        known_ids, known_titles = storage.known_album_keys()
        items: list[dict[str, Any]] = []
        filtered_items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in selected:
            if recommendation_progress.is_cancelled(progress_id):
                break
            label = f"{candidate['artist']} — {candidate['title']} ({candidate['year']})"
            normalized = storage._normalized(candidate["title"])
            if normalized in known_titles:
                errors.append({"title": label, "error": "уже есть в библиотеке или корзине"})
                recommendation_progress.advance(progress_id, "llm-album-details")
                continue
            try:
                found = musicbrainz.search_album(candidate["title"], candidate["artist"], candidate["year"])
                mbid = str(found.get("release_group_mbid") or "")
                if not mbid:
                    reason = "MusicBrainz ID недоступен"
                    errors.append({"title": label, "error": reason})
                    filtered_items.append({"item": _album_candidate_payload(candidate), "reason": reason})
                    continue
                if mbid in known_ids:
                    errors.append({"title": label, "error": "уже есть в библиотеке или корзине"})
                    continue
                if mbid in seen:
                    reason = "дубликат в текущей выдаче"
                    errors.append({"title": label, "error": reason})
                    filtered_items.append({"item": _album_candidate_payload(candidate), "reason": reason})
                    continue
                details = musicbrainz.album_details(mbid, fetch_popularity=False)
                details.update({
                    "notes": candidate["comment"],
                    "llm_comment": candidate["comment"],
                    "raw_data": candidate,
                })
                year_from = _optional_number(payload, "year_from", 2023, 1900, date.today().year + 2)
                year_to = _optional_number(payload, "year_to", date.today().year, 1900, date.today().year + 2)
                actual_year = details.get("year")
                if actual_year is None or (year_from is not None and int(actual_year) < int(year_from)) or (
                    year_to is not None and int(actual_year) > int(year_to)
                ):
                    reason = "год альбома не соответствует фильтру"
                    errors.append({"title": label, "error": reason})
                    filtered_items.append({"item": details, "reason": reason})
                    continue
                seen.add(mbid)
                items.append(details)
            except Exception as error:
                reason = str(error)
                errors.append({"title": label, "error": reason})
                filtered_items.append({"item": _album_candidate_payload(candidate), "reason": reason})
            finally:
                recommendation_progress.advance(progress_id, "llm-album-details")
        recommendation_progress.finish_stage(progress_id, "llm-album-details")
        warnings: list[dict[str, str]] = []
        batch_total = (len(items) + listenbrainz.BATCH_SIZE - 1) // listenbrainz.BATCH_SIZE
        recommendation_progress.set_stage(
            progress_id, "llm-listenbrainz", "ListenBrainz · прослушивания",
            batch_total, "пакетов",
        )
        if items:
            try:
                if progress_id:
                    listenbrainz.enrich_albums(
                        items,
                        on_batch=lambda: recommendation_progress.advance(progress_id, "llm-listenbrainz"),
                        should_cancel=lambda: recommendation_progress.is_cancelled(progress_id),
                    )
                else:
                    listenbrainz.enrich_albums(items)
            except listenbrainz.ListenBrainzError as error:
                warnings.append({"provider": "listenbrainz", "message": str(error)})
                recommendation_progress.add_warning(progress_id, "ListenBrainz", str(error))
        recommendation_progress.finish_stage(progress_id, "llm-listenbrainz")
        if errors:
            warnings.append({
                "provider": "musicbrainz",
                "message": f"Не удалось уточнить {len(errors)} рекомендаций через MusicBrainz.",
            })
        for item in items:
            item["provider_warnings"] = item.get("provider_warnings", []) + warnings
        return {
            "items": items, "filtered_items": filtered_items, "errors": errors, "model": model,
            "requested": requested, "received": len(candidates), "provider_warnings": warnings,
            "raw_response": answer,
            "cancelled": recommendation_progress.is_cancelled(progress_id),
        }
    finally:
        recommendation_progress.finish(progress_id)


def build_recommendation_prompt(payload: dict[str, Any]) -> str:
    contract = build_album_prompt(payload) if payload.get("content_type") == "music" else build_movie_prompt(payload)
    return (
        f"{BASE_INSTRUCTIONS}\n\n"
        "The following recommendation contract is mandatory system-level context. "
        "Apply every enabled filter before choosing recommendations:\n\n"
        f"{contract}"
    )


def _backlog_mood_item(item: dict[str, Any], content_type: str) -> dict[str, Any]:
    common = {
        "id": str(item.get("id") or ""),
        "title_ru": str(item.get("title_ru") or ""),
        "title_original": str(item.get("title_original") or ""),
        "year": item.get("year"),
        "genres": str(item.get("genres") or ""),
        "notes": str(item.get("notes") or "")[:300],
    }
    if content_type == "music":
        return {
            **common,
            "artists": str(item.get("artists") or ""),
            "release_date": str(item.get("first_release_date") or ""),
            "release_types": " · ".join(
                value for value in (
                    str(item.get("primary_type") or ""),
                    str(item.get("secondary_types") or ""),
                ) if value
            ),
            "track_count": item.get("track_count"),
            "annotation": str(item.get("annotation") or "")[:600],
        }
    return {
        **common,
        "release_date": str(item.get("release_date") or ""),
        "directors": str(item.get("directors") or ""),
        "countries": str(item.get("countries") or ""),
        "runtime_minutes": item.get("duration_minutes"),
        "imdb_rating": item.get("imdb_rating"),
        "kinopoisk_rating": item.get("kinopoisk_rating"),
        "key_people": str(item.get("key_people") or ""),
        "overview": str(item.get("overview") or "")[:600],
    }


def build_backlog_moods_prompt(payload: dict[str, Any]) -> str:
    content_type = str(payload.get("content_type") or "movie")
    if content_type not in {"movie", "music"}:
        raise LlmError("Неизвестный тип контента")
    backlog = storage.list_library(content_type=content_type, status="backlog")
    if not backlog:
        raise LlmError("В бэклоге пока нечего анализировать")
    entity = "фильмы" if content_type == "movie" else "альбомы"
    candidates = "\n".join(
        json.dumps(_backlog_mood_item(item, content_type), ensure_ascii=False, separators=(",", ":"))
        for item in backlog
    )
    contract = "\n\n".join([
        f"Задача: сгруппируй {entity} из текущего бэклога по настроению и верни до 15 коротких категорий на русском.",
        "Категории должны описывать эмоциональный тон, атмосферу или подходящий сценарий просмотра/прослушивания, "
        "которые действительно представлены объектами списка. Покрой разнообразие всего бэклога, но объединяй близкие варианты.",
        "Каждая категория — объект с полями label и item_ids. label — самостоятельная естественная фраза из 2–6 слов "
        "в нижнем регистре, например «мрачный корейский триллер». item_ids — все ID объектов списка, которые подходят "
        "к этой категории; один объект может входить в несколько категорий. Используй только переданные ID и не оставляй категорию пустой. "
        "Не используй названия произведений, имена авторов, нумерацию, Markdown и пояснения. Не повторяй категории и не предлагай "
        "настроения, для которых в списке нет подходящих объектов.",
        f"Полный бэклог ({len(backlog)} объектов, один JSON-объект на строку):\n{candidates}",
        "Формат ответа задаётся JSON Schema. Верни только JSON-объект с массивом moods, содержащим связи категорий с объектами.",
    ])
    return (
        f"{BASE_INSTRUCTIONS}\n\n"
        "The following backlog mood mapping contract is mandatory system-level context. "
        "Use only the supplied backlog:\n\n"
        f"{contract}"
    )


def _parse_backlog_moods(raw: str, allowed_ids: set[str]) -> list[dict[str, Any]]:
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LlmError(f"Codex вернул ответ, который не удалось разобрать как JSON: {error}") from error
    moods = response.get("moods") if isinstance(response, dict) else None
    if not isinstance(moods, list):
        raise LlmError("Codex вернул JSON без массива moods")
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mood in moods:
        if not isinstance(mood, dict):
            continue
        label = str(mood.get("label") or "").strip()
        normalized = label.casefold()
        raw_item_ids = mood.get("item_ids")
        if not label or normalized in seen or not isinstance(raw_item_ids, list):
            continue
        item_ids = list(dict.fromkeys(
            str(item_id) for item_id in raw_item_ids if str(item_id) in allowed_ids
        ))
        if not item_ids:
            continue
        parsed.append({"label": label[:80], "item_ids": item_ids})
        seen.add(normalized)
        if len(parsed) >= 15:
            break
    if not parsed:
        raise LlmError("Codex не вернул настроения для бэклога")
    return parsed


def suggest_backlog_moods(payload: dict[str, Any]) -> dict[str, Any]:
    progress_id = payload.get("progress_id")
    recommendation_progress.start(
        progress_id, 1, stage_id="backlog-moods-request", label="Codex · настроения бэклога", unit="запросов",
    )
    try:
        prompt = build_backlog_moods_prompt(payload)
        if not VENV_PYTHON.exists():
            raise LlmError(
                "Codex SDK не установлен. Выполните: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
            )
        model = get_model()
        completed = _run_codex(prompt, model, MOODS_SCHEMA_PATH, progress_id)
        if completed is None:
            return {"moods": [], "model": model, "cancelled": True}
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown Codex SDK error"
            raise LlmError(f"Codex SDK: {message[-1200:]}")
        answer = completed.stdout.strip()
        if not answer:
            raise LlmError("Codex вернул пустой ответ")
        content_type = str(payload.get("content_type") or "movie")
        allowed_ids = {
            str(item.get("id") or "")
            for item in storage.list_library(content_type=content_type, status="backlog")
        }
        moods = _parse_backlog_moods(answer, allowed_ids)
        cancelled = recommendation_progress.is_cancelled(progress_id)
        if not cancelled:
            moods = storage.replace_backlog_moods(content_type, moods)
        recommendation_progress.finish_stage(progress_id, "backlog-moods-request")
        return {
            "moods": moods,
            "model": model,
            "cancelled": cancelled,
        }
    finally:
        recommendation_progress.finish(progress_id)


def build_people_prompt(payload: dict[str, Any]) -> str:
    content_type = str(payload.get("content_type") or "movie")
    if content_type not in {"movie", "music"}:
        raise LlmError("Неизвестный тип контента")
    user_prompt = str(payload.get("prompt") or "").strip()
    if len(user_prompt) > 8000:
        raise LlmError("Пользовательский промпт слишком длинный")
    limit = int(_number(payload, "limit", 5, 1, 20))
    selected_role = str(payload.get("role") or "").strip() if content_type == "movie" else "artist"
    if content_type == "movie" and selected_role not in {"", "actor", "director"}:
        raise LlmError("Тип рекомендации должен быть actor или director")
    role_filter = selected_role or None
    existing = storage.list_interests(content_type, role_filter, include_trashed=True)
    existing_lines = [
        _artist_line(person) if content_type == "music" else _person_line(person)
        for person in existing
    ]
    if content_type == "music":
        task = (
            "Задача: порекомендуй исполнителей или музыкальные группы по запросу пользователя. "
            "Для каждой рекомендации укажи каноническое имя в name_original, наиболее употребимое "
            "русское написание в name_ru, role=artist и короткий содержательный comment на русском."
        )
        task_entity_label = "исполнителей"
        list_entity_label = "исполнители"
        format_note = "Для всех элементов поле role должно быть равно artist."
    else:
        role_settings = {
            "actor": ("актёров", "актёры", "actor"),
            "director": ("режиссёров", "режиссёры", "director"),
            "": ("актёров и режиссёров", "актёры и режиссёры", "actor или director"),
        }
        task_entity_label, list_entity_label, role_note = role_settings[selected_role]
        task = (
            f"Задача: порекомендуй {task_entity_label} по запросу пользователя. Для каждой рекомендации "
            f"укажи оригинальное имя в name_original, наиболее употребимое русское имя в name_ru, роль {role_note} "
            "и короткий содержательный comment на русском."
        )
        format_note = (
            f"Для всех элементов поле role должно быть равно {selected_role}."
            if selected_role else
            "Поле role должно быть actor для актёра или director для режиссёра."
        )
        active_people = storage.list_interests("movie", role_filter)
        people_sample_size = int(_number(
            payload, "people_sample_size", len(active_people), 0, max(10_000, len(active_people)),
        ))
        liked_people_lines = [
            _person_line(person)
            for person in _sample_context(active_people, people_sample_size, payload)
        ]
    user_block = user_prompt or "Дополнительных пожеланий нет — подбери наиболее релевантные рекомендации."
    sections = [
        task,
        f"Верни не более {limit} наиболее релевантных позиций. Не предлагай никого из уже добавленных списков, "
        "включая объекты в корзине. Не заменяй уже добавленную персону вариантом написания того же имени.",
        f"Свободный запрос пользователя:\n{user_block}",
    ]
    if content_type == "movie":
        sections.append(_section(
            f"Любимые {list_entity_label} (используй как мягкий сигнал вкуса)",
            liked_people_lines,
        ))
    sections.extend([
        _section(f"Уже добавленные {list_entity_label}, включая корзину — не рекомендовать повторно", existing_lines),
        "Формат ответа задаётся JSON Schema. Верни только JSON-объект с массивом people. "
        f"{format_note} Не добавляй Markdown, вступление или вопросы пользователю.",
    ])
    return "\n\n".join(sections)


def build_people_recommendation_prompt(payload: dict[str, Any]) -> str:
    return (
        f"{BASE_INSTRUCTIONS}\n\n"
        "The following people recommendation contract is mandatory system-level context:\n\n"
        f"{build_people_prompt(payload)}"
    )


def _parse_people_response(
    raw: str, content_type: str, selected_role: str = "",
) -> list[dict[str, str]]:
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LlmError(f"Codex вернул ответ, который не удалось разобрать как JSON: {error}") from error
    people = response.get("people") if isinstance(response, dict) else None
    if not isinstance(people, list):
        raise LlmError("Codex вернул JSON без массива people")
    parsed: list[dict[str, str]] = []
    allowed_roles = (
        {"artist"} if content_type == "music"
        else ({selected_role} if selected_role in {"actor", "director"} else {"actor", "director"})
    )
    for index, person in enumerate(people, start=1):
        if not isinstance(person, dict):
            raise LlmError(f"Позиция {index} в ответе Codex имеет неверный формат")
        item = {
            "name_original": str(person.get("name_original") or "").strip(),
            "name_ru": str(person.get("name_ru") or "").strip(),
            "role": str(person.get("role") or "").strip(),
            "comment": str(person.get("comment") or "").strip(),
        }
        if not item["name_original"] or not item["name_ru"] or not item["comment"]:
            raise LlmError(f"У позиции {index} не заполнены обязательные поля")
        if item["role"] not in allowed_roles:
            raise LlmError(f"У позиции {index} указана неподходящая роль")
        parsed.append(item)
    return parsed


def _person_name_keys(person: dict[str, Any]) -> set[str]:
    return {
        normalized
        for field in ("name", "name_original", "name_ru")
        if (normalized := storage._normalized(str(person.get(field) or "")))
    }


def recommend_people(payload: dict[str, Any]) -> dict[str, Any]:
    content_type = str(payload.get("content_type") or "movie")
    if content_type not in {"movie", "music"}:
        raise LlmError("Неизвестный тип контента")
    requested = int(_number(payload, "limit", 5, 1, 20))
    progress_id = payload.get("progress_id")
    recommendation_progress.start(
        progress_id, 1, stage_id="llm-request", label="Codex · запрос к LLM", unit="запросов",
    )
    try:
        prompt = build_people_recommendation_prompt(payload)
        if not VENV_PYTHON.exists():
            raise LlmError(
                "Codex SDK не установлен. Выполните: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
            )
        model = get_model()
        completed = _run_codex(prompt, model, PERSON_SCHEMA_PATH, progress_id)
        if completed is None:
            return {
                "items": [], "errors": [], "model": model,
                "requested": 0, "received": 0, "cancelled": True,
            }
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown Codex SDK error"
            raise LlmError(f"Codex SDK: {message[-1200:]}")
        recommendation_progress.finish_stage(progress_id, "llm-request")
        selected_role = str(payload.get("role") or "").strip() if content_type == "movie" else ""
        candidates = _parse_people_response(
            completed.stdout.strip(), content_type, selected_role,
        )[:requested]
        provider = "MusicBrainz" if content_type == "music" else "TMDB"
        stage_id = "musicbrainz-people" if content_type == "music" else "tmdb-people"
        recommendation_progress.set_stage(
            progress_id, stage_id,
            f"{provider} · карточки {'исполнителей' if content_type == 'music' else 'персон'}",
            len(candidates), "исполнителей" if content_type == "music" else "персон",
        )
        existing = storage.list_interests(
            content_type, selected_role or None, include_trashed=True,
        )
        known_names = set().union(*(_person_name_keys(person) for person in existing)) if existing else set()
        id_field = "mbid" if content_type == "music" else "tmdb_id"
        known_ids = {
            str(person.get(id_field) or person.get("external_id") or "")
            for person in existing
            if person.get(id_field) or person.get("external_id")
        }
        seen_names: set[str] = set()
        seen_ids: set[str] = set()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for candidate in candidates:
            if recommendation_progress.is_cancelled(progress_id):
                break
            label = candidate["name_ru"] or candidate["name_original"]
            candidate_names = _person_name_keys(candidate)
            try:
                if candidate_names & (known_names | seen_names):
                    errors.append({"title": label, "error": "уже есть в списке или корзине"})
                    continue
                if content_type == "music":
                    details = musicbrainz.resolve_artist_input({
                        "content_type": "music", "name": candidate["name_original"],
                    })
                    details["content_type"] = "music"
                    details["role"] = "artist"
                else:
                    details = tmdb.resolve_person_input(candidate)
                    details["content_type"] = "movie"
                external_id = str(details.get(id_field) or details.get("external_id") or "")
                resolved_names = _person_name_keys(details)
                if (external_id and external_id in known_ids | seen_ids) or resolved_names & (known_names | seen_names):
                    errors.append({"title": label, "error": "уже есть в списке или корзине"})
                    continue
                details.update({
                    "notes": candidate["comment"],
                    "llm_comment": candidate["comment"],
                    "raw_data": candidate,
                })
                if external_id:
                    seen_ids.add(external_id)
                seen_names.update(candidate_names | resolved_names)
                items.append(details)
            except Exception as error:
                errors.append({"title": label, "error": str(error)})
            finally:
                recommendation_progress.advance(progress_id, stage_id)
        recommendation_progress.finish_stage(progress_id, stage_id)
        if content_type == "music":
            artwork_stage_id = "fanart-people"
            recommendation_progress.set_stage(
                progress_id, artwork_stage_id,
                "fanart.tv · фотографии исполнителей", len(items), "исполнителей",
            )
            for index, details in enumerate(items):
                if recommendation_progress.is_cancelled(progress_id):
                    break
                try:
                    enriched = musicbrainz.enrich_artist_artwork(details)
                    items[index] = enriched
                    for warning in enriched.get("provider_warnings", []):
                        if isinstance(warning, dict):
                            recommendation_progress.add_warning(
                                progress_id,
                                str(warning.get("provider") or "fanart.tv"),
                                str(warning.get("message") or ""),
                            )
                except Exception as error:
                    recommendation_progress.add_warning(progress_id, "fanart.tv", str(error))
                finally:
                    recommendation_progress.advance(progress_id, artwork_stage_id)
            recommendation_progress.finish_stage(progress_id, artwork_stage_id)
        return {
            "items": items, "errors": errors, "model": model,
            "requested": requested, "received": len(candidates),
            "cancelled": recommendation_progress.is_cancelled(progress_id),
        }
    finally:
        recommendation_progress.finish(progress_id)
